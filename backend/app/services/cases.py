from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_owned
from app.api.errors import ApiError, forbidden
from app.models import ComplianceCase, Customer, VerificationRequest
from app.security.permissions import Principal
from app.services import audit

OPEN_STATUSES = ("open", "in_review", "escalated")


def check_can_decide(case: ComplianceCase, p: Principal) -> None:
    """Resource-level rule: reviewers decide only cases assigned to them; escalated or unassigned
    cases need `cases:decide_any` (analysts/admins)."""
    if not p.can("cases:decide"):
        raise forbidden()
    if p.can("cases:decide_any"):
        return
    if case.status == "escalated":
        raise forbidden("Escalated cases require a compliance analyst or admin")
    if case.assignee_id != p.user_id:
        raise forbidden("You can only decide cases assigned to you")


async def decide(db: AsyncSession, p: Principal, case_id, decision: str, notes: str) -> ComplianceCase:
    # Row lock serializes concurrent decisions on the same case (no double-decide race).
    case = await get_owned(db, ComplianceCase, case_id, p, "case", for_update=True)
    if case.status not in OPEN_STATUSES:
        raise ApiError(409, "case_closed", f"Case is already {case.status}")
    check_can_decide(case, p)
    final = "approved" if decision == "approve" else "rejected"
    now = datetime.now(UTC)
    case.status, case.decided_by, case.decided_at, case.decision_notes = final, p.user_id, now, notes
    await db.execute(update(VerificationRequest).where(VerificationRequest.id == case.verification_id, VerificationRequest.org_id == p.org_id)
                     .values(decision=final, decision_source="manual", decided_by=p.user_id))
    await db.execute(update(Customer).where(Customer.id == case.customer_id, Customer.org_id == p.org_id)
                     .values(status="verified" if final == "approved" else "rejected"))
    audit.record(db, "case.decided", org_id=p.org_id, principal=p, resource_type="case", resource_id=case.id,
                 details={"decision": final, "verification_id": str(case.verification_id)})
    await db.commit()
    return case
