"""Verification workflow orchestration.

Lifecycle of one verification request:

    submit()        validate → upsert customer → idempotency check → persist request (status=processing|queued)
    run_workflow()  claim → load config → select providers → execute (concurrently, with timeout,
                    retry, circuit breaker, tenant-scoped cache) → normalize → derive signals →
                    risk score → policy evaluation → decision → persist everything in ONE transaction

Database connections are NOT held while waiting on providers: configuration is loaded and the
session committed before any network I/O, and results are written in a single short transaction.
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.engine import policy as policy_engine
from app.engine.providers import (
    REGISTRY,
    NormalizedResult,
    breaker_for,
    execute_with_resilience,
)
from app.engine.risk import MODEL_VERSION, compute_risk
from app.models import (
    ComplianceCase,
    Customer,
    Organization,
    Policy,
    PolicyVersion,
    ProviderConfig,
    ProviderResult,
    RiskAssessment,
    VerificationRequest,
    WorkflowExecution,
)
from app.security.crypto import decrypt_field, encrypt_field
from app.services import audit
from app.services.cache import cache_get_json, cache_set_json

log = logging.getLogger("sentinelflow.orchestrator")

WORKFLOWS = {"individual": ["sim_idv", "sim_watchlist"], "business": ["sim_kyb", "sim_watchlist"]}
MANDATORY = {
    "individual": ["full_name", "dob", "country", "document_type", "document_number"],
    "business": ["company_name", "registration_number", "country"],
}
RECOMMENDED = {"individual": ["email"], "business": ["email"]}
ACTION_TO_DECISION = {"approve": "approved", "reject": "rejected", "manual_review": "manual_review", "escalate": "escalated"}
DEFAULT_PROVIDER_CFG = {"enabled": True, "timeout_ms": 800, "max_retries": 2, "simulation": {"latency_ms": 40}}
SLA_HOURS = {"critical": 4, "high": 24, "normal": 72, "low": 120}
WATCHLIST_CACHE_TTL_S = 3600


class IdempotencyConflict(Exception):
    def __init__(self, existing: VerificationRequest | None, reason: str):
        self.existing, self.reason = existing, reason


_PII_KEYS = {"name": "mask_name", "birth_date": "mask_dob", "dob": "mask_dob"}


def scrub_pii(obj):
    """Mask subject PII inside provider payloads before they are persisted (raw responses are kept
    for audit/debugging, but never with clear-text names or dates of birth)."""
    from app.security import crypto

    if isinstance(obj, dict):
        return {k: getattr(crypto, _PII_KEYS[k])(v) if k in _PII_KEYS and isinstance(v, str) else scrub_pii(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_pii(v) for v in obj]
    return obj


def request_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# --------------------------------------------------------------------------- submission
async def submit(db: AsyncSession, principal, payload: dict, idempotency_key: str | None, mode: str) -> tuple[VerificationRequest, bool]:
    """Returns (request, replayed). Raises IdempotencyConflict."""
    org_id = principal.org_id
    rhash = request_hash(payload)
    if idempotency_key:
        existing = await db.scalar(
            select(VerificationRequest).where(VerificationRequest.org_id == org_id, VerificationRequest.idempotency_key == idempotency_key)
        )
        if existing:
            return _replay_or_conflict(existing, rhash), True

    subject = payload["subject"]
    vtype = payload["verification_type"]
    display_name = subject.get("full_name") or subject.get("company_name") or "Unknown"

    # Upsert customer by (org_id, external_ref). ON CONFLICT keeps this race-free.
    values = dict(
        org_id=org_id,
        external_ref=payload["external_ref"],
        customer_type=vtype,
        full_name=display_name,
        email=subject.get("email"),
        country=subject["country"],
        dob_enc=encrypt_field(subject.get("dob")),
        document_type=subject.get("document_type"),
        document_number_enc=encrypt_field(subject.get("document_number")),
        document_last4=(subject.get("document_number") or "")[-4:] or None,
        company_name=subject.get("company_name"),
        registration_number=subject.get("registration_number"),
        status="pending",
    )
    stmt = pg_insert(Customer).values(id=uuid.uuid4(), **values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_customers_org_ref",
        set_={k: stmt.excluded[k] for k in values if k not in ("org_id", "external_ref", "status")} | {"updated_at": func.now()},
    ).returning(Customer.id)
    customer_id = await db.scalar(stmt)

    vr = VerificationRequest(
        org_id=org_id,
        customer_id=customer_id,
        verification_type=vtype,
        status="processing" if mode == "inline" else "queued",
        idempotency_key=idempotency_key,
        request_hash=rhash,
        input_snapshot={
            "external_ref": payload["external_ref"],
            "verification_type": vtype,
            "country": subject["country"],
            "fields_provided": sorted(k for k, v in subject.items() if v),
            "execution_mode": mode,
        },
        submitted_by_type=principal.kind,
        submitted_by_id=principal.actor_id,
    )
    db.add(vr)
    try:
        await db.flush()
    except Exception as e:  # unique violation on idempotency key from a concurrent duplicate
        await db.rollback()
        if idempotency_key and "uq_verifications_idempotency" in str(e):
            existing = await db.scalar(
                select(VerificationRequest).where(VerificationRequest.org_id == org_id, VerificationRequest.idempotency_key == idempotency_key)
            )
            return _replay_or_conflict(existing, rhash), True
        raise
    audit.record(db, "verification.submitted", org_id=org_id, principal=principal, resource_type="verification",
                 resource_id=vr.id, details={"type": vtype, "mode": mode, "external_ref": payload["external_ref"]})
    await db.commit()
    return vr, False


def _replay_or_conflict(existing: VerificationRequest, rhash: str) -> VerificationRequest:
    if existing.request_hash != rhash:
        raise IdempotencyConflict(existing, "Idempotency-Key was already used with a different request body")
    if existing.status in ("processing", "queued") and existing.input_snapshot.get("execution_mode") == "inline":
        raise IdempotencyConflict(existing, "A request with this Idempotency-Key is still being processed")
    return existing


# --------------------------------------------------------------------------- execution
def _subject(c: Customer) -> dict:
    s = {
        "full_name": c.full_name if c.customer_type == "individual" else None,
        "email": c.email,
        "country": c.country,
        "dob": decrypt_field(c.dob_enc),
        "document_type": c.document_type,
        "document_number": decrypt_field(c.document_number_enc),
        "company_name": c.company_name,
        "registration_number": c.registration_number,
    }
    s["screen_name"] = s["full_name"] or s["company_name"]
    return s


def derive_signals(vtype: str, subject: dict, results: dict[str, dict]) -> dict:
    expected = MANDATORY[vtype] + RECOMMENDED[vtype]
    missing = [f for f in expected if not subject.get(f)]
    missing_mandatory = [f for f in MANDATORY[vtype] if not subject.get(f)]
    inconsistencies = []
    idv = results.get("identity")
    if idv and idv["outcome"] not in ("error", "skipped"):
        if idv["signals"].get("name_similarity", 1) < 0.85:
            inconsistencies.append("Document name does not match submitted name")
        if idv["signals"].get("dob_match") is False:
            inconsistencies.append("Document date of birth differs from submitted value")
    kyb = results.get("business")
    if kyb and kyb["outcome"] not in ("error", "skipped") and kyb["signals"].get("name_similarity", 1) < 0.85:
        inconsistencies.append("Registry legal name differs from submitted company name")
    outcomes = [r["outcome"] for r in results.values() if r["outcome"] != "skipped"]
    return {
        "missing_fields": missing,
        "missing_count": len(missing),
        "missing_mandatory": missing_mandatory,
        "missing_mandatory_count": len(missing_mandatory),
        "expected_field_count": len(expected),
        "inconsistencies": inconsistencies,
        "inconsistency_count": len(inconsistencies),
        "provider_errors": outcomes.count("error"),
        "uncertain_results": any(o in ("review", "error") for o in outcomes),
        "conflicting_results": "pass" in outcomes and "fail" in outcomes,
    }


async def _run_provider(org_id, key: str, subject: dict, cfg: dict) -> tuple[NormalizedResult, dict]:
    adapter = REGISTRY[key]
    meta = {"attempts": 0, "latency_ms": 0.0, "cache_hit": False, "raw": None, "recovered": False}
    if not cfg["enabled"]:
        return NormalizedResult(key, adapter.check_type, "skipped", 0.0, reasons=["Provider disabled for organization"]), meta
    missing = [f for f in adapter.required_fields if not subject.get(f)]
    if missing:
        return NormalizedResult(key, adapter.check_type, "error", 0.0, reasons=[f"Insufficient input: {', '.join(missing)}"], error_code="insufficient_input"), meta

    payload = adapter.build_request(subject)
    ckey = adapter.cache_key(payload)
    cache_key = f"sf:pc:{org_id}:{key}:{hashlib.sha256(ckey.encode()).hexdigest()}" if ckey else None  # tenant-scoped
    if cache_key and (cached := await cache_get_json(cache_key, f"provider:{key}")):
        meta |= {"cache_hit": True, "raw": cached}
        return adapter.normalize(cached, subject), meta

    out = await execute_with_resilience(adapter, payload, cfg, breaker_for(org_id, key))
    meta |= {"attempts": out.attempts, "latency_ms": round(out.latency_ms, 2), "raw": out.raw, "recovered": out.recovered}
    if out.raw is None:
        return NormalizedResult(key, adapter.check_type, "error", 0.0, reasons=[f"Provider call failed: {out.error_code}"], error_code=out.error_code), meta
    if cache_key:
        await cache_set_json(cache_key, out.raw, WATCHLIST_CACHE_TTL_S)
    return adapter.normalize(out.raw, subject), meta


async def claim(verification_id: uuid.UUID) -> bool:
    """Atomically move a queued request to processing. Safe with any number of workers."""
    async with SessionLocal() as db:
        res = await db.execute(
            update(VerificationRequest)
            .where(VerificationRequest.id == verification_id, VerificationRequest.status == "queued")
            .values(status="processing")
        )
        await db.commit()
        return res.rowcount == 1


async def run_workflow(verification_id: uuid.UUID, mode: str = "inline") -> None:
    t0 = time.perf_counter()
    steps: list[dict] = []

    def step(name: str, started: float, status: str = "completed", **detail):
        steps.append({"name": name, "status": status, "offset_ms": round((started - t0) * 1000, 2),
                      "duration_ms": round((time.perf_counter() - started) * 1000, 2), **detail})

    # ---- load everything we need, then release the connection before network I/O
    s = time.perf_counter()
    async with SessionLocal() as db:
        row = (await db.execute(
            select(VerificationRequest, Customer, Organization)
            .join(Customer, Customer.id == VerificationRequest.customer_id)
            .join(Organization, Organization.id == VerificationRequest.org_id)
            .where(VerificationRequest.id == verification_id)
        )).first()
        if row is None or row[0].status != "processing":
            return
        vr, customer, org = row
        org_id = vr.org_id
        cfg_rows = (await db.scalars(select(ProviderConfig).where(ProviderConfig.org_id == org_id))).all()
        pol_rows = (await db.execute(
            select(Policy, PolicyVersion)
            .join(PolicyVersion, (PolicyVersion.policy_id == Policy.id) & (PolicyVersion.version == Policy.current_version))
            .where(Policy.org_id == org_id, Policy.is_active.is_(True))
        )).all()
        failed_history = await db.scalar(
            select(func.count()).select_from(VerificationRequest).where(
                VerificationRequest.org_id == org_id, VerificationRequest.customer_id == vr.customer_id,
                VerificationRequest.id != vr.id, VerificationRequest.decision == "rejected")
        )
        vtype = vr.verification_type
        subject = _subject(customer)
        settings = org.settings or {}
        await db.commit()
    step("load_context", s)

    try:
        s = time.perf_counter()
        cfgs = {c.provider_key: {"enabled": c.enabled, "timeout_ms": c.timeout_ms, "max_retries": c.max_retries, "simulation": c.simulation} for c in cfg_rows}
        selected = WORKFLOWS[vtype]
        step("select_providers", s, providers=selected)

        s = time.perf_counter()
        outs = await asyncio.gather(*[_run_provider(org_id, k, subject, cfgs.get(k, DEFAULT_PROVIDER_CFG)) for k in selected])
        for (nr, meta) in outs:
            steps.append({"name": f"provider:{nr.provider_key}", "status": nr.outcome, "offset_ms": round((s - t0) * 1000, 2),
                          "duration_ms": meta["latency_ms"], "attempts": meta["attempts"], "cache_hit": meta["cache_hit"],
                          "error_code": nr.error_code})
        results = {nr.check_type: nr.to_dict() for nr, _ in outs}

        s = time.perf_counter()
        signals = derive_signals(vtype, subject, results)
        step("normalize_and_derive_signals", s)

        s = time.perf_counter()
        risk = compute_risk(results, signals, {"failed_verifications": failed_history or 0},
                            settings.get("risk_weights"), settings.get("risk_thresholds"))
        step("risk_scoring", s, score=risk.score, category=risk.category)

        s = time.perf_counter()
        ctx = {
            "request": {"type": vtype},
            "customer": {"country": subject["country"], "type": vtype, "has_email": bool(subject.get("email"))},
            "providers": {k: {"outcome": v["outcome"], "confidence": v["confidence"], "signals": v["signals"]} for k, v in results.items()},
            "signals": signals,
            "risk": {"score": risk.score, "category": risk.category},
            "history": {"failed_verifications": failed_history or 0},
        }
        snapshots = [policy_engine.PolicySnapshot(str(p.id), p.name, p.current_version, p.priority, v.definition) for p, v in pol_rows]
        decision = policy_engine.evaluate(snapshots, ctx, [r["outcome"] for r in results.values()])
        step("policy_evaluation", s, policies_evaluated=len(snapshots), decided_by=decision.decided_by, action=decision.action)

        final = ACTION_TO_DECISION[decision.action]
        total_ms = round((time.perf_counter() - t0) * 1000, 2)

        # ---- persist atomically
        s = time.perf_counter()
        async with SessionLocal() as db, db.begin():
            for nr, meta in outs:
                db.add(ProviderResult(
                    org_id=org_id, verification_id=verification_id, provider_key=nr.provider_key, check_type=nr.check_type,
                    outcome=nr.outcome, confidence=nr.confidence, attempts=meta["attempts"], latency_ms=meta["latency_ms"],
                    error_code=nr.error_code, cache_hit=meta["cache_hit"], raw_response=scrub_pii(meta["raw"]),
                    normalized=scrub_pii(nr.to_dict()),
                ))
            db.add(RiskAssessment(org_id=org_id, verification_id=verification_id, score=risk.score, category=risk.category,
                                  breakdown=risk.breakdown, weights=settings.get("risk_weights") or {}, model_version=MODEL_VERSION))
            now = datetime.now(UTC)
            await db.execute(update(VerificationRequest).where(VerificationRequest.id == verification_id).values(
                status="completed", decision=final, automated_decision=final, decision_source="policy",
                policy_trace={**decision.to_dict(), "context": ctx}, latency_ms=total_ms, completed_at=now))
            await db.execute(update(Customer).where(Customer.id == customer.id).values(
                status={"approved": "verified", "rejected": "rejected"}.get(final, "in_review"),
                latest_risk_score=risk.score, latest_risk_category=risk.category))
            if final in ("manual_review", "escalated"):
                prio = "critical" if (final == "escalated" and risk.category == "critical") else (
                    "high" if final == "escalated" or risk.category in ("high", "critical") else "normal")
                case = ComplianceCase(org_id=org_id, verification_id=verification_id, customer_id=customer.id,
                                      status="escalated" if final == "escalated" else "open", priority=prio,
                                      reason=decision.reason, sla_due_at=now + timedelta(hours=SLA_HOURS[prio]))
                db.add(case)
                await db.flush()
                audit.record(db, "case.created", org_id=org_id, actor_type="system", actor_label="orchestrator",
                             resource_type="case", resource_id=case.id, details={"priority": prio, "reason": decision.reason})
            steps.append({"name": "persist", "status": "completed", "offset_ms": round((s - t0) * 1000, 2), "duration_ms": None})
            db.add(WorkflowExecution(org_id=org_id, verification_id=verification_id, status="completed", execution_mode=mode,
                                     steps=steps, finished_at=now, duration_ms=total_ms))
            audit.record(db, "verification.completed", org_id=org_id, actor_type="system", actor_label="orchestrator",
                         resource_type="verification", resource_id=verification_id,
                         details={"decision": final, "risk_score": risk.score, "decided_by": decision.decided_by})
    except Exception as e:
        log.exception("workflow failed for %s", verification_id)
        async with SessionLocal() as db, db.begin():
            await db.execute(update(VerificationRequest).where(VerificationRequest.id == verification_id).values(
                status="failed", error=f"{type(e).__name__}: {e}"[:500], latency_ms=round((time.perf_counter() - t0) * 1000, 2)))
            db.add(WorkflowExecution(org_id=org_id, verification_id=verification_id, status="failed", execution_mode=mode,
                                     steps=steps, finished_at=datetime.now(UTC), error=str(e)[:500],
                                     duration_ms=round((time.perf_counter() - t0) * 1000, 2)))
            audit.record(db, "verification.failed", org_id=org_id, actor_type="system", actor_label="orchestrator",
                         resource_type="verification", resource_id=verification_id, details={"error": type(e).__name__})
