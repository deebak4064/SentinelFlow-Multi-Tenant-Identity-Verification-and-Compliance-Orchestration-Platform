from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import serializers as ser
from app.api.deps import PageParams, contains, get_owned, paginate, require
from app.api.errors import forbidden
from app.db import get_db
from app.models import ComplianceCase, Customer, RiskAssessment, VerificationRequest
from app.security.permissions import Principal
from app.services import audit

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("")
async def list_customers(
    params: PageParams = Depends(),
    q: str | None = Query(None, max_length=120),
    status: Literal["pending", "verified", "rejected", "in_review"] | None = None,
    customer_type: Literal["individual", "business"] | None = None,
    risk_category: Literal["low", "medium", "high", "critical"] | None = None,
    p: Principal = Depends(require("customers:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Customer).where(Customer.org_id == p.org_id)
    if q:
        # Search by reference or business name only; masked personal names are not searchable.
        stmt = stmt.where(or_(Customer.external_ref.ilike(contains(q), escape="\\"), Customer.company_name.ilike(contains(q), escape="\\")))
    for col, val in ((Customer.status, status), (Customer.customer_type, customer_type), (Customer.latest_risk_category, risk_category)):
        if val:
            stmt = stmt.where(col == val)
    rows, total = await paginate(db, stmt, params, {"created_at": Customer.created_at, "risk_score": Customer.latest_risk_score,
                                                     "external_ref": Customer.external_ref}, "-created_at")
    return {"items": [ser.customer(c) for (c,) in rows], "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/{customer_id}")
async def get_customer(
    customer_id: UUID,
    reveal: bool = False,
    p: Principal = Depends(require("customers:read")),
    db: AsyncSession = Depends(get_db),
):
    c = await get_owned(db, Customer, customer_id, p, "customer")
    if reveal:
        if not p.can("customers:read_pii"):
            raise forbidden("Revealing PII requires customers:read_pii")
        audit.record(db, "customer.pii_revealed", org_id=p.org_id, principal=p, resource_type="customer", resource_id=c.id)
        await db.commit()
    verifs = (await db.execute(
        select(VerificationRequest, RiskAssessment).outerjoin(RiskAssessment, RiskAssessment.verification_id == VerificationRequest.id)
        .where(VerificationRequest.org_id == p.org_id, VerificationRequest.customer_id == c.id)
        .order_by(VerificationRequest.created_at.desc()).limit(50)
    )).all()
    cases = (await db.scalars(select(ComplianceCase).where(ComplianceCase.org_id == p.org_id, ComplianceCase.customer_id == c.id)
                              .order_by(ComplianceCase.created_at.desc()).limit(50))).all()
    return {
        **ser.customer(c, reveal=reveal),
        "verifications": [ser.verification_summary(v, None, r) for v, r in verifs],
        "cases": [ser.case(k) for k in cases],
    }
