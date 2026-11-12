"""Dashboard aggregates and the audit log explorer. All figures are computed from stored data."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams, paginate, require
from app.db import get_db
from app.models import (
    AuditEvent,
    ComplianceCase,
    ProviderResult,
    RiskAssessment,
    VerificationRequest,
    WorkflowExecution,
)
from app.schemas import AuditOut
from app.security.permissions import Principal
from app.services.cache import cache_get_json, cache_set_json

router = APIRouter(tags=["insights"])


@router.get("/dashboard/overview")
async def overview(days: int = Query(14, ge=1, le=90), p: Principal = Depends(require("verifications:read")), db: AsyncSession = Depends(get_db)):
    key = f"sf:dash:{p.org_id}:{days}"
    if cached := await cache_get_json(key, "dashboard"):
        return cached
    since = datetime.now(UTC) - timedelta(days=days)
    V, W, R = VerificationRequest, WorkflowExecution, ProviderResult
    org = V.org_id == p.org_id

    total = await db.scalar(select(func.count()).select_from(V).where(org, V.created_at >= since))
    decisions = dict((await db.execute(select(V.decision, func.count()).where(org, V.created_at >= since, V.decision.is_not(None))
                                       .group_by(V.decision))).all())
    statuses = dict((await db.execute(select(W.status, func.count()).where(W.org_id == p.org_id, W.started_at >= since)
                                      .group_by(W.status))).all())
    lat = (await db.execute(select(func.avg(W.duration_ms), func.percentile_cont(0.5).within_group(W.duration_ms),
                                   func.percentile_cont(0.95).within_group(W.duration_ms))
                            .where(W.org_id == p.org_id, W.started_at >= since, W.status == "completed"))).one()
    providers = [{"provider_key": k, "calls": n, "success_rate": round(ok / n, 4) if n else None, "avg_latency_ms": round(al or 0, 2)}
                 for k, n, ok, al in (await db.execute(
                     select(R.provider_key, func.count(), func.sum((R.outcome != "error").cast(Integer)), func.avg(R.latency_ms))
                     .where(R.org_id == p.org_id, R.created_at >= since, R.outcome != "skipped").group_by(R.provider_key))).all()]
    day = func.date_trunc("day", V.created_at)
    throughput = [{"date": d.date().isoformat(), "total": n, "approved": a, "rejected": r, "review": m}
                  for d, n, a, r, m in (await db.execute(
                      select(day, func.count(), func.sum((V.decision == "approved").cast(Integer)),
                             func.sum((V.decision == "rejected").cast(Integer)),
                             func.sum(V.decision.in_(["manual_review", "escalated"]).cast(Integer)))
                      .where(org, V.created_at >= since).group_by(day).order_by(day))).all()]
    high_risk_open = await db.scalar(
        select(func.count()).select_from(ComplianceCase).join(RiskAssessment, RiskAssessment.verification_id == ComplianceCase.verification_id)
        .where(ComplianceCase.org_id == p.org_id, ComplianceCase.status.in_(["open", "in_review", "escalated"]),
               RiskAssessment.category.in_(["high", "critical"])))
    open_cases = await db.scalar(select(func.count()).select_from(ComplianceCase).where(
        ComplianceCase.org_id == p.org_id, ComplianceCase.status.in_(["open", "in_review", "escalated"])))
    risk_mix = dict((await db.execute(select(RiskAssessment.category, func.count()).where(
        RiskAssessment.org_id == p.org_id, RiskAssessment.created_at >= since).group_by(RiskAssessment.category))).all())
    calls = sum(x["calls"] for x in providers)
    result = {
        "window_days": days,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_verifications": total or 0,
        "decision_distribution": {k: decisions.get(k, 0) for k in ("approved", "rejected", "manual_review", "escalated")},
        "avg_processing_latency_ms": round(lat[0] or 0, 2),
        "p50_processing_latency_ms": round(lat[1] or 0, 2),
        "p95_processing_latency_ms": round(lat[2] or 0, 2),
        "provider_success_rate": round(sum((x["success_rate"] or 0) * x["calls"] for x in providers) / calls, 4) if calls else None,
        "providers": providers,
        "high_risk_open_cases": high_risk_open or 0,
        "open_cases": open_cases or 0,
        "risk_distribution": {k: risk_mix.get(k, 0) for k in ("low", "medium", "high", "critical")},
        "throughput": throughput,
        "workflow_health": {k: statuses.get(k, 0) for k in ("completed", "failed", "running")},
    }
    await cache_set_json(key, result, 15)  # short TTL: dashboard tolerates 15s staleness
    return result


@router.get("/audit-events")
async def audit_events(
    params: PageParams = Depends(),
    action: str | None = Query(None, max_length=80, pattern=r"^[a-z_.]+$"),
    actor_id: UUID | None = None,
    resource_type: str | None = Query(None, max_length=40, pattern=r"^[a-z_]+$"),
    resource_id: str | None = Query(None, max_length=64),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    p: Principal = Depends(require("audit:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AuditEvent).where(AuditEvent.org_id == p.org_id)
    if action:
        stmt = stmt.where(AuditEvent.action.startswith(action))
    for col, val in ((AuditEvent.actor_id, actor_id), (AuditEvent.resource_type, resource_type), (AuditEvent.resource_id, resource_id)):
        if val:
            stmt = stmt.where(col == val)
    if created_from:
        stmt = stmt.where(AuditEvent.created_at >= created_from)
    if created_to:
        stmt = stmt.where(AuditEvent.created_at <= created_to)
    rows, total = await paginate(db, stmt, params, {"created_at": AuditEvent.created_at, "id": AuditEvent.id}, "-id")
    return {"items": [AuditOut.model_validate(e).model_dump() for (e,) in rows], "total": total, "page": params.page, "page_size": params.page_size}
