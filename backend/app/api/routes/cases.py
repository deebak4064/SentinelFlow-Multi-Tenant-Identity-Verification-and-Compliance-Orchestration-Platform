from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import serializers as ser
from app.api.deps import PageParams, get_owned, paginate, require
from app.api.errors import ApiError, forbidden
from app.api.routes.verifications import verification_detail
from app.db import get_db
from app.models import (
    AuditEvent,
    CaseComment,
    ComplianceCase,
    Customer,
    Organization,
    OrganizationMembership,
    ProviderResult,
    User,
)
from app.schemas import CaseAssignIn, CaseCommentIn, CaseEscalateIn, ReviewIn
from app.security.permissions import Principal, role_permissions
from app.services import ai_analyst, audit
from app.services import cases as svc

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("")
async def list_cases(
    params: PageParams = Depends(),
    status: Literal["open", "in_review", "escalated", "approved", "rejected"] | None = None,
    priority: Literal["low", "normal", "high", "critical"] | None = None,
    assigned: Literal["me", "unassigned"] | None = None,
    open_only: bool = False,
    closed_only: bool = False,
    p: Principal = Depends(require("cases:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = (select(ComplianceCase, Customer, User).join(Customer, Customer.id == ComplianceCase.customer_id)
            .outerjoin(User, User.id == ComplianceCase.assignee_id).where(ComplianceCase.org_id == p.org_id))
    if status:
        stmt = stmt.where(ComplianceCase.status == status)
    if open_only:
        stmt = stmt.where(ComplianceCase.status.in_(svc.OPEN_STATUSES))
    if closed_only:
        stmt = stmt.where(ComplianceCase.status.in_(["approved", "rejected"]))
    if priority:
        stmt = stmt.where(ComplianceCase.priority == priority)
    if assigned == "me":
        stmt = stmt.where(ComplianceCase.assignee_id == p.user_id)
    elif assigned == "unassigned":
        stmt = stmt.where(ComplianceCase.assignee_id.is_(None))
    rows, total = await paginate(db, stmt, params, {"created_at": ComplianceCase.created_at, "sla_due_at": ComplianceCase.sla_due_at,
                                                     "priority": ComplianceCase.priority}, "-created_at")
    items = [{**ser.case(c), "customer_name": cu.full_name if cu.customer_type == "business" else ser.mask_name(cu.full_name),
              "customer_type": cu.customer_type, "risk_category": cu.latest_risk_category, "risk_score": cu.latest_risk_score,
              "assignee_name": u.full_name if u else None} for c, cu, u in rows]
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}


@router.get("/{case_id}")
async def get_case(case_id: UUID, p: Principal = Depends(require("cases:read")), db: AsyncSession = Depends(get_db)):
    case = await get_owned(db, ComplianceCase, case_id, p, "case")
    comments = (await db.execute(select(CaseComment, User).outerjoin(User, User.id == CaseComment.author_id)
                                 .where(CaseComment.case_id == case.id).order_by(CaseComment.created_at))).all()
    history = (await db.scalars(select(AuditEvent).where(AuditEvent.org_id == p.org_id, AuditEvent.resource_type == "case",
                                                          AuditEvent.resource_id == str(case.id)).order_by(AuditEvent.id))).all()
    assignee = await db.get(User, case.assignee_id) if case.assignee_id else None
    detail = await verification_detail(db, p, case.verification_id) if p.can("verifications:read") else None
    return {
        **ser.case(case),
        "assignee_name": assignee.full_name if assignee else None,
        "verification": detail,
        "comments": [{"id": c.id, "body": c.body, "evidence_refs": c.evidence_refs, "author_id": c.author_id,
                      "author_name": u.full_name if u else None, "created_at": c.created_at} for c, u in comments],
        "history": [{"action": h.action, "actor_label": h.actor_label, "details": h.details, "created_at": h.created_at} for h in history],
        "can_decide": _can_decide(case, p),
    }


def _can_decide(case, p) -> bool:
    try:
        svc.check_can_decide(case, p)
        return case.status in svc.OPEN_STATUSES
    except ApiError:
        return False


@router.post("/{case_id}/assign")
async def assign(case_id: UUID, body: CaseAssignIn, p: Principal = Depends(require("cases:assign")), db: AsyncSession = Depends(get_db)):
    case = await get_owned(db, ComplianceCase, case_id, p, "case", for_update=True)
    if case.status not in svc.OPEN_STATUSES:
        raise ApiError(409, "case_closed", "Closed cases cannot be reassigned")
    if body.assignee_id:
        m = await db.scalar(select(OrganizationMembership).where(OrganizationMembership.org_id == p.org_id,
                                                                  OrganizationMembership.user_id == body.assignee_id))
        if not m or "cases:decide" not in role_permissions(m.role_key):
            raise ApiError(422, "invalid_assignee", "Assignee must be a member of this organization who can decide cases")
    prev = case.assignee_id
    case.assignee_id = body.assignee_id
    if case.status == "open" and body.assignee_id:
        case.status = "in_review"
    audit.record(db, "case.assigned", org_id=p.org_id, principal=p, resource_type="case", resource_id=case.id,
                 details={"from": str(prev) if prev else None, "to": str(body.assignee_id) if body.assignee_id else None})
    await db.commit()
    return ser.case(case)


@router.post("/{case_id}/comments", status_code=201)
async def comment(case_id: UUID, body: CaseCommentIn, p: Principal = Depends(require("cases:write")), db: AsyncSession = Depends(get_db)):
    case = await get_owned(db, ComplianceCase, case_id, p, "case")
    valid_ids = {str(i) for i in (await db.scalars(select(ProviderResult.id).where(ProviderResult.verification_id == case.verification_id))).all()}
    for ref in body.evidence_refs:
        if set(ref) != {"type", "id"} or ref["type"] not in ("provider_result", "risk_factor", "policy", "external"):
            raise ApiError(422, "invalid_evidence_ref", "evidence_refs items need 'type' and 'id'")
        if ref["type"] == "provider_result" and ref["id"] not in valid_ids:
            raise ApiError(422, "invalid_evidence_ref", "Referenced provider result does not belong to this case")
    c = CaseComment(org_id=p.org_id, case_id=case.id, author_id=p.user_id, body=body.body, evidence_refs=body.evidence_refs)
    db.add(c)
    audit.record(db, "case.commented", org_id=p.org_id, principal=p, resource_type="case", resource_id=case.id,
                 details={"evidence_refs": len(body.evidence_refs)})
    await db.commit()
    return {"id": c.id, "body": c.body, "evidence_refs": c.evidence_refs, "created_at": c.created_at}


@router.post("/{case_id}/escalate")
async def escalate(case_id: UUID, body: CaseEscalateIn, p: Principal = Depends(require("cases:write")), db: AsyncSession = Depends(get_db)):
    case = await get_owned(db, ComplianceCase, case_id, p, "case", for_update=True)
    if case.status not in ("open", "in_review"):
        raise ApiError(409, "invalid_transition", f"Cannot escalate a case that is {case.status}")
    case.status = "escalated"
    case.priority = {"low": "normal", "normal": "high"}.get(case.priority, "critical")
    case.assignee_id = None  # escalated cases go back to the analyst queue
    db.add(CaseComment(org_id=p.org_id, case_id=case.id, author_id=p.user_id, body=f"Escalated: {body.reason}", evidence_refs=[]))
    audit.record(db, "case.escalated", org_id=p.org_id, principal=p, resource_type="case", resource_id=case.id,
                 details={"reason": body.reason, "priority": case.priority})
    await db.commit()
    return ser.case(case)


@router.post("/{case_id}/decision")
async def decision(case_id: UUID, body: ReviewIn, p: Principal = Depends(require("cases:decide")), db: AsyncSession = Depends(get_db)):
    return ser.case(await svc.decide(db, p, case_id, body.decision, body.notes))


@router.post("/{case_id}/ai-analysis", summary="Advisory AI summary of a case (never decides)")
async def ai_analysis(case_id: UUID, p: Principal = Depends(require("ai:use", "cases:read", "verifications:read")), db: AsyncSession = Depends(get_db)):
    case = await get_owned(db, ComplianceCase, case_id, p, "case")
    org = await db.get(Organization, p.org_id)
    if not (org.settings or {}).get("ai_enabled", True):
        raise forbidden("AI assistant is disabled for this organization")
    detail = await verification_detail(db, p, case.verification_id)
    result = await ai_analyst.analyze(detail, org.settings or {})
    audit.record(db, "case.ai_analysis", org_id=p.org_id, principal=p, resource_type="case", resource_id=case.id,
                 details={"source": result["source"], "warnings": len(result["warnings"])})
    await db.commit()
    return result
