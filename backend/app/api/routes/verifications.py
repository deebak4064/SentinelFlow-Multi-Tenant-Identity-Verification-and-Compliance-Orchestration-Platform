import asyncio
import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import serializers as ser
from app.api.deps import PageParams, contains, get_owned, paginate, require
from app.api.errors import ApiError, not_found
from app.config import get_settings
from app.db import get_db
from app.engine import orchestrator
from app.models import (
    ComplianceCase,
    Customer,
    Organization,
    ProviderResult,
    RiskAssessment,
    VerificationRequest,
    WorkflowExecution,
)
from app.schemas import ReviewIn, VerificationIn
from app.security.permissions import Principal
from app.services import cases as case_service
from app.services.cache import enqueue_workflow

router = APIRouter(prefix="/verifications", tags=["verifications"])
log = logging.getLogger("sentinelflow.verifications")


async def verification_detail(db: AsyncSession, p: Principal, vid: UUID) -> dict:
    # Two round trips: the request with its 1:1 children (tenant-scoped), then provider results.
    row = (await db.execute(
        select(VerificationRequest, Customer, RiskAssessment, ComplianceCase)
        .join(Customer, Customer.id == VerificationRequest.customer_id)
        .outerjoin(RiskAssessment, RiskAssessment.verification_id == VerificationRequest.id)
        .outerjoin(ComplianceCase, ComplianceCase.verification_id == VerificationRequest.id)
        .where(VerificationRequest.id == vid, VerificationRequest.org_id == p.org_id)
    )).first()
    if row is None:
        raise not_found("verification")
    v, c, risk, case = row
    results = (await db.scalars(select(ProviderResult).where(ProviderResult.verification_id == v.id).order_by(ProviderResult.check_type))).all()
    # Latest workflow run (re-runs are possible after a stale-request reap).
    wf = await db.scalar(select(WorkflowExecution).where(WorkflowExecution.verification_id == v.id).order_by(WorkflowExecution.started_at.desc()).limit(1))
    return {
        **ser.verification_summary(v, c, risk),
        "error": v.error,
        "input_snapshot": v.input_snapshot,
        "customer": ser.customer(c),
        "provider_results": [ser.provider_result(r) for r in results],
        "risk_assessment": ser.risk(risk) if risk else None,
        "policy_trace": v.policy_trace,
        "workflow": ser.workflow(wf) if wf else None,
        "case": ser.case(case) if case else None,
    }


@router.post("", status_code=201, summary="Submit a verification request")
async def create_verification(
    body: VerificationIn,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$"),
    p: Principal = Depends(require("verifications:create")),
    db: AsyncSession = Depends(get_db),
):
    org = await db.get(Organization, p.org_id)
    mode = body.execution_mode or (org.settings or {}).get("default_execution_mode") or get_settings().default_execution_mode
    payload = body.model_dump(mode="json", exclude={"execution_mode"})
    try:
        vr, replayed = await orchestrator.submit(db, p, payload, idempotency_key, mode)
    except orchestrator.IdempotencyConflict as e:
        raise ApiError(409, "idempotency_conflict", e.reason, details={"verification_id": str(e.existing.id)}) from None

    vid = vr.id
    if replayed:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    elif mode == "queue":
        try:
            await enqueue_workflow(str(vid))
            response.status_code = 202
        except Exception:
            log.warning("queue unavailable, executing inline")
            await db.execute(VerificationRequest.__table__.update().where(VerificationRequest.id == vid).values(status="processing"))
            await db.commit()
            await asyncio.shield(orchestrator.run_workflow(vid, "inline"))
    else:
        # shield: a client disconnect must not leave a half-processed request behind
        await asyncio.shield(orchestrator.run_workflow(vid, "inline"))
    db.expire_all()
    return await verification_detail(db, p, vid)


@router.get("", summary="List verification requests")
async def list_verifications(
    params: PageParams = Depends(),
    status: Literal["queued", "processing", "completed", "failed"] | None = None,
    decision: Literal["approved", "rejected", "manual_review", "escalated"] | None = None,
    verification_type: Literal["individual", "business"] | None = None,
    risk_category: Literal["low", "medium", "high", "critical"] | None = None,
    customer_id: UUID | None = None,
    q: str | None = Query(None, max_length=120),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    p: Principal = Depends(require("verifications:read")),
    db: AsyncSession = Depends(get_db),
):
    V = VerificationRequest
    stmt = (select(V, Customer, RiskAssessment).join(Customer, Customer.id == V.customer_id)
            .outerjoin(RiskAssessment, RiskAssessment.verification_id == V.id).where(V.org_id == p.org_id))
    for col, val in ((V.status, status), (V.decision, decision), (V.verification_type, verification_type),
                     (RiskAssessment.category, risk_category), (V.customer_id, customer_id)):
        if val is not None:
            stmt = stmt.where(col == val)
    if q:
        stmt = stmt.where(Customer.external_ref.ilike(contains(q), escape="\\"))
    if created_from:
        stmt = stmt.where(V.created_at >= created_from)
    if created_to:
        stmt = stmt.where(V.created_at <= created_to)
    rows, total = await paginate(db, stmt, params, {"created_at": V.created_at, "latency_ms": V.latency_ms, "risk_score": RiskAssessment.score}, "-created_at")
    return {"items": [ser.verification_summary(v, c, r) for v, c, r in rows], "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/{verification_id}", summary="Verification detail with provider results, risk and policy trace")
async def get_verification(verification_id: UUID, p: Principal = Depends(require("verifications:read")), db: AsyncSession = Depends(get_db)):
    return await verification_detail(db, p, verification_id)


@router.post("/{verification_id}/review", summary="Record a manual review decision")
async def review(verification_id: UUID, body: ReviewIn, p: Principal = Depends(require("verifications:review")), db: AsyncSession = Depends(get_db)):
    v = await get_owned(db, VerificationRequest, verification_id, p, "verification")
    case = await db.scalar(select(ComplianceCase).where(ComplianceCase.verification_id == v.id, ComplianceCase.org_id == p.org_id))
    if case is None:
        raise ApiError(409, "no_review_required", "This verification was decided automatically and has no open case")
    await case_service.decide(db, p, case.id, body.decision, body.notes)
    db.expire_all()
    return await verification_detail(db, p, verification_id)
