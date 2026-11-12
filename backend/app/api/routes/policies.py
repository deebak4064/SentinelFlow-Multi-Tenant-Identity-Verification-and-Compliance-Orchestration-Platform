from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_owned, require
from app.api.errors import ApiError, not_found
from app.db import get_db
from app.engine import policy as engine
from app.models import Policy, PolicyVersion, ProviderResult, VerificationRequest
from app.schemas import PolicyIn, PolicySimulateIn, PolicyUpdateIn
from app.security.permissions import Principal
from app.services import audit

router = APIRouter(prefix="/policies", tags=["policies"])


def _out(p: Policy, v: PolicyVersion | None) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description, "priority": p.priority, "is_active": p.is_active,
            "current_version": p.current_version, "definition": v.definition if v else None,
            "created_at": p.created_at, "updated_at": p.updated_at}


async def _current(db, pol: Policy) -> PolicyVersion | None:
    return await db.scalar(select(PolicyVersion).where(PolicyVersion.policy_id == pol.id, PolicyVersion.version == pol.current_version))


@router.get("")
async def list_policies(p: Principal = Depends(require("policies:read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Policy, PolicyVersion).join(PolicyVersion, (PolicyVersion.policy_id == Policy.id) & (PolicyVersion.version == Policy.current_version))
        .where(Policy.org_id == p.org_id).order_by(Policy.priority, Policy.name))).all()
    return [_out(pol, v) for pol, v in rows]


@router.post("", status_code=201)
async def create_policy(body: PolicyIn, p: Principal = Depends(require("policies:write")), db: AsyncSession = Depends(get_db)):
    pol = Policy(org_id=p.org_id, name=body.name, description=body.description, priority=body.priority,
                 is_active=body.is_active, current_version=1, created_by=p.user_id)
    db.add(pol)
    try:
        await db.flush()
    except IntegrityError:
        raise ApiError(409, "policy_exists", "A policy with this name already exists") from None
    v = PolicyVersion(org_id=p.org_id, policy_id=pol.id, version=1, definition=body.definition.model_dump(exclude_none=True),
                      change_note="Created", created_by=p.user_id)
    db.add(v)
    audit.record(db, "policy.created", org_id=p.org_id, principal=p, resource_type="policy", resource_id=pol.id, details={"name": pol.name})
    await db.commit()
    return _out(pol, v)


@router.get("/{policy_id}")
async def get_policy(policy_id: UUID, p: Principal = Depends(require("policies:read")), db: AsyncSession = Depends(get_db)):
    pol = await get_owned(db, Policy, policy_id, p, "policy")
    versions = (await db.scalars(select(PolicyVersion).where(PolicyVersion.policy_id == pol.id).order_by(PolicyVersion.version.desc()))).all()
    cur = next((v for v in versions if v.version == pol.current_version), None)
    return {**_out(pol, cur), "versions": [{"version": v.version, "definition": v.definition, "change_note": v.change_note,
                                            "created_by": v.created_by, "created_at": v.created_at} for v in versions]}


@router.patch("/{policy_id}")
async def update_policy(policy_id: UUID, body: PolicyUpdateIn, p: Principal = Depends(require("policies:write")), db: AsyncSession = Depends(get_db)):
    pol = await get_owned(db, Policy, policy_id, p, "policy", for_update=True)  # lock: concurrent edits get sequential versions
    changes = {}
    if body.description is not None:
        pol.description, changes["description"] = body.description, True
    if body.priority is not None and body.priority != pol.priority:
        changes["priority"] = {"from": pol.priority, "to": body.priority}
        pol.priority = body.priority
    v = None
    if body.definition is not None:
        pol.current_version += 1
        v = PolicyVersion(org_id=p.org_id, policy_id=pol.id, version=pol.current_version,
                          definition=body.definition.model_dump(exclude_none=True), change_note=body.change_note, created_by=p.user_id)
        db.add(v)
        changes["version"] = pol.current_version
    audit.record(db, "policy.updated", org_id=p.org_id, principal=p, resource_type="policy", resource_id=pol.id, details=changes)
    await db.commit()
    return _out(pol, v or await _current(db, pol))


async def _set_active(db, p, policy_id, active: bool):
    pol = await get_owned(db, Policy, policy_id, p, "policy")
    pol.is_active = active
    audit.record(db, "policy.activated" if active else "policy.deactivated", org_id=p.org_id, principal=p, resource_type="policy", resource_id=pol.id)
    await db.commit()
    return _out(pol, await _current(db, pol))


@router.post("/{policy_id}/activate")
async def activate(policy_id: UUID, p: Principal = Depends(require("policies:write")), db: AsyncSession = Depends(get_db)):
    return await _set_active(db, p, policy_id, True)


@router.post("/{policy_id}/deactivate")
async def deactivate(policy_id: UUID, p: Principal = Depends(require("policies:write")), db: AsyncSession = Depends(get_db)):
    return await _set_active(db, p, policy_id, False)


@router.post("/{policy_id}/versions/{version}/restore", summary="Roll back by creating a new version from an old one")
async def restore(policy_id: UUID, version: int, p: Principal = Depends(require("policies:write")), db: AsyncSession = Depends(get_db)):
    pol = await get_owned(db, Policy, policy_id, p, "policy", for_update=True)
    old = await db.scalar(select(PolicyVersion).where(PolicyVersion.policy_id == pol.id, PolicyVersion.version == version))
    if not old:
        raise not_found("policy version")
    pol.current_version += 1
    v = PolicyVersion(org_id=p.org_id, policy_id=pol.id, version=pol.current_version, definition=old.definition,
                      change_note=f"Restored from v{version}", created_by=p.user_id)
    db.add(v)
    audit.record(db, "policy.restored", org_id=p.org_id, principal=p, resource_type="policy", resource_id=pol.id,
                 details={"from_version": version, "new_version": pol.current_version})
    await db.commit()
    return _out(pol, v)


@router.post("/simulate", summary="Dry-run a policy (or the active set) against a stored verification or a custom context")
async def simulate(body: PolicySimulateIn, p: Principal = Depends(require("policies:read")), db: AsyncSession = Depends(get_db)):
    outcomes: list[str] = []
    if body.verification_id:
        v = await get_owned(db, VerificationRequest, body.verification_id, p, "verification")
        if not v.policy_trace:
            raise ApiError(409, "not_evaluated", "Verification has no evaluation context yet")
        ctx = v.policy_trace["context"]
        outcomes = list((await db.scalars(select(ProviderResult.outcome).where(ProviderResult.verification_id == v.id))).all())
    elif body.context is not None:
        ctx = body.context
        outcomes = [r.get("outcome", "pass") for r in (ctx.get("providers") or {}).values() if isinstance(r, dict)]
    else:
        raise ApiError(422, "validation_error", "Provide verification_id or context")

    if body.definition:
        snaps = [engine.PolicySnapshot("draft", "Draft policy", 0, 0, body.definition.model_dump(exclude_none=True))]
    else:
        rows = (await db.execute(
            select(Policy, PolicyVersion).join(PolicyVersion, (PolicyVersion.policy_id == Policy.id) & (PolicyVersion.version == Policy.current_version))
            .where(Policy.org_id == p.org_id, Policy.is_active.is_(True)))).all()
        snaps = [engine.PolicySnapshot(str(pol.id), pol.name, pol.current_version, pol.priority, v.definition) for pol, v in rows]
    return {"context": ctx, **engine.evaluate(snaps, ctx, outcomes).to_dict()}
