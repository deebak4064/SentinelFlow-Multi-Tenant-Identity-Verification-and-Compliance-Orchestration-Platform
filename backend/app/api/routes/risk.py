from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import serializers as ser
from app.api.deps import PageParams, get_owned, paginate, require
from app.db import get_db
from app.engine.risk import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS, FLOORS
from app.models import Customer, Organization, RiskAssessment, VerificationRequest
from app.security.permissions import Principal

router = APIRouter(prefix="/risk-assessments", tags=["risk"])


@router.get("")
async def list_assessments(
    params: PageParams = Depends(),
    category: Literal["low", "medium", "high", "critical"] | None = None,
    min_score: float | None = Query(None, ge=0, le=100),
    p: Principal = Depends(require("verifications:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = (select(RiskAssessment, VerificationRequest, Customer)
            .join(VerificationRequest, VerificationRequest.id == RiskAssessment.verification_id)
            .join(Customer, Customer.id == VerificationRequest.customer_id)
            .where(RiskAssessment.org_id == p.org_id))
    if category:
        stmt = stmt.where(RiskAssessment.category == category)
    if min_score is not None:
        stmt = stmt.where(RiskAssessment.score >= min_score)
    rows, total = await paginate(db, stmt, params, {"score": RiskAssessment.score, "created_at": RiskAssessment.created_at}, "-score")
    return {"items": [{**ser.risk(r), "verification": ser.verification_summary(v, c)} for r, v, c in rows],
            "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/summary")
async def summary(p: Principal = Depends(require("verifications:read")), db: AsyncSession = Depends(get_db)):
    cats = dict((await db.execute(select(RiskAssessment.category, func.count()).where(RiskAssessment.org_id == p.org_id)
                                  .group_by(RiskAssessment.category))).all())
    buckets = (await db.execute(
        select(func.least(func.floor(RiskAssessment.score / 10), 9).label("b"), func.count())
        .where(RiskAssessment.org_id == p.org_id).group_by("b").order_by("b"))).all()
    # Points contributed per factor, summed in SQL over the JSONB breakdown.
    contrib = (await db.execute(text(
        "SELECT f->>'factor', sum((f->>'points')::float) FROM risk_assessments, jsonb_array_elements(breakdown) f "
        "WHERE org_id = :org GROUP BY 1"), {"org": p.org_id})).all()
    org = await db.get(Organization, p.org_id)
    st = org.settings or {}
    return {
        "categories": {k: cats.get(k, 0) for k in ("low", "medium", "high", "critical")},
        "histogram": [{"bucket": f"{int(b) * 10}-{int(b) * 10 + 9 if b < 9 else 100}", "count": n} for b, n in buckets],
        "avg_factor_points": {f: round(v / max(sum(cats.values()), 1), 2) for f, v in contrib},  # mean over ALL assessments
        "weights": {**DEFAULT_WEIGHTS, **(st.get("risk_weights") or {})},
        "thresholds": {**DEFAULT_THRESHOLDS, **(st.get("risk_thresholds") or {})},
        "floors": FLOORS,
    }


@router.get("/{assessment_id}")
async def get_assessment(assessment_id: UUID, p: Principal = Depends(require("verifications:read")), db: AsyncSession = Depends(get_db)):
    return ser.risk(await get_owned(db, RiskAssessment, assessment_id, p, "risk assessment"))
