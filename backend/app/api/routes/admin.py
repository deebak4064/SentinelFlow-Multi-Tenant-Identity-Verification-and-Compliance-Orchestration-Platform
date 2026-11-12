"""Members & roles, API keys, organization settings, system metrics and platform administration."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_owned, require
from app.api.errors import ApiError, forbidden, not_found
from app.db import get_db
from app.engine.providers import REGISTRY, breaker_for
from app.engine.risk import DEFAULT_THRESHOLDS, DEFAULT_WEIGHTS
from app.models import (
    ApiKey,
    Organization,
    OrganizationMembership,
    User,
    VerificationRequest,
)
from app.schemas import ApiKeyIn, MemberIn, OrgStatusIn, RoleChangeIn, SettingsIn
from app.security.crypto import hash_password, new_token, sha256
from app.security.permissions import (
    API_KEY_GRANTABLE,
    PERMISSIONS,
    ROLES,
    Principal,
    can_assign_role,
)
from app.services import audit
from app.services.cache import QUEUE_KEY, cache_stats, redis

router = APIRouter(tags=["administration"])


# --------------------------------------------------------------------------- roles & members
@router.get("/roles")
async def roles(p: Principal = Depends(require("users:read"))):
    return {"permissions": PERMISSIONS,
            "roles": [{"key": k, "name": r["name"], "description": r["description"], "rank": r["rank"],
                       "permissions": sorted(r["permissions"])} for k, r in sorted(ROLES.items(), key=lambda x: -x[1]["rank"])]}


@router.get("/users")
async def members(p: Principal = Depends(require("users:read")), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(OrganizationMembership, User).join(User, User.id == OrganizationMembership.user_id)
                             .where(OrganizationMembership.org_id == p.org_id).order_by(User.full_name))).all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": m.role_key, "role_name": ROLES[m.role_key]["name"],
             "is_active": u.is_active, "last_login_at": u.last_login_at, "joined_at": m.created_at} for m, u in rows]


@router.post("/users", status_code=201)
async def add_member(body: MemberIn, p: Principal = Depends(require("users:write")), db: AsyncSession = Depends(get_db)):
    if not can_assign_role(p.role or "", body.role):
        raise forbidden("You cannot grant a role more privileged than your own")
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        user = User(email=body.email.lower(), full_name=body.full_name, password_hash=hash_password(body.temporary_password))
        db.add(user)
        await db.flush()
    elif await db.scalar(select(OrganizationMembership.id).where(OrganizationMembership.org_id == p.org_id, OrganizationMembership.user_id == user.id)):
        raise ApiError(409, "already_member", "User is already a member of this organization")
    db.add(OrganizationMembership(org_id=p.org_id, user_id=user.id, role_key=body.role))
    audit.record(db, "member.added", org_id=p.org_id, principal=p, resource_type="user", resource_id=user.id, details={"role": body.role})
    await db.commit()
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": body.role}


async def _membership(db, p, user_id) -> OrganizationMembership:
    m = await db.scalar(select(OrganizationMembership).where(OrganizationMembership.org_id == p.org_id, OrganizationMembership.user_id == user_id)
                        .with_for_update())
    if not m:
        raise not_found("member")
    return m


async def _guard_last_admin(db, p, m) -> None:
    if m.role_key in ("org_admin", "super_admin"):
        # Lock every admin membership row first: two admins demoting each other concurrently would
        # otherwise both see "2 admins" and leave the organization with none.
        admins = len((await db.scalars(select(OrganizationMembership.id).where(
            OrganizationMembership.org_id == p.org_id, OrganizationMembership.role_key.in_(["org_admin", "super_admin"]))
            .with_for_update())).all())
        if admins <= 1:
            raise ApiError(409, "last_admin", "An organization must keep at least one admin")


@router.patch("/users/{user_id}/role")
async def change_role(user_id: UUID, body: RoleChangeIn, p: Principal = Depends(require("users:write")), db: AsyncSession = Depends(get_db)):
    if user_id == p.user_id:
        raise forbidden("You cannot change your own role")
    m = await _membership(db, p, user_id)
    # Must outrank-or-equal both the current and the new role (can't demote a higher-ranked admin either).
    if not (can_assign_role(p.role or "", body.role) and can_assign_role(p.role or "", m.role_key)):
        raise forbidden("You cannot change a role at or above your own privilege level")
    if body.role not in ("org_admin", "super_admin"):
        await _guard_last_admin(db, p, m)
    old, m.role_key = m.role_key, body.role
    audit.record(db, "member.role_changed", org_id=p.org_id, principal=p, resource_type="user", resource_id=user_id,
                 details={"from": old, "to": body.role})
    await db.commit()
    return {"id": user_id, "role": body.role}


@router.delete("/users/{user_id}", status_code=204)
async def remove_member(user_id: UUID, p: Principal = Depends(require("users:write")), db: AsyncSession = Depends(get_db)):
    if user_id == p.user_id:
        raise forbidden("You cannot remove yourself")
    m = await _membership(db, p, user_id)
    if not can_assign_role(p.role or "", m.role_key):
        raise forbidden("You cannot remove a member with a higher role")
    await _guard_last_admin(db, p, m)
    await db.delete(m)
    audit.record(db, "member.removed", org_id=p.org_id, principal=p, resource_type="user", resource_id=user_id, details={"role": m.role_key})
    await db.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- API keys
def _key_out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": k.scopes, "created_at": k.created_at, "last_used_at": k.last_used_at,
            "expires_at": k.expires_at, "revoked_at": k.revoked_at, "rotated_from_id": k.rotated_from_id,
            "status": "revoked" if k.revoked_at else ("expired" if k.expires_at and k.expires_at < datetime.now(UTC) else "active")}


def _mint(org_id, name, scopes, created_by, expires_at, rotated_from=None) -> tuple[ApiKey, str]:
    raw = new_token("sf_live_")
    return ApiKey(org_id=org_id, name=name, prefix=raw[:14], key_hash=sha256(raw), scopes=scopes, created_by=created_by,
                  expires_at=expires_at, rotated_from_id=rotated_from), raw


@router.get("/api-keys")
async def list_keys(p: Principal = Depends(require("api_keys:manage")), db: AsyncSession = Depends(get_db)):
    return [_key_out(k) for k in (await db.scalars(select(ApiKey).where(ApiKey.org_id == p.org_id).order_by(ApiKey.created_at.desc()))).all()]


@router.post("/api-keys", status_code=201)
async def create_key(body: ApiKeyIn, p: Principal = Depends(require("api_keys:manage")), db: AsyncSession = Depends(get_db)):
    scopes = sorted(set(body.scopes))
    bad = [s for s in scopes if s not in API_KEY_GRANTABLE or not p.can(s)]
    if bad:
        raise ApiError(422, "invalid_scopes", "Scopes not grantable to an API key or not held by you", details=bad)
    exp = datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    key, raw = _mint(p.org_id, body.name, scopes, p.user_id, exp)
    db.add(key)
    await db.flush()
    audit.record(db, "api_key.created", org_id=p.org_id, principal=p, resource_type="api_key", resource_id=key.id, details={"scopes": scopes})
    await db.commit()
    return {**_key_out(key), "secret": raw, "secret_notice": "Store this secret now. It cannot be retrieved again."}


@router.post("/api-keys/{key_id}/revoke")
async def revoke_key(key_id: UUID, p: Principal = Depends(require("api_keys:manage")), db: AsyncSession = Depends(get_db)):
    key = await get_owned(db, ApiKey, key_id, p, "API key", for_update=True)
    if not key.revoked_at:
        key.revoked_at = datetime.now(UTC)
        audit.record(db, "api_key.revoked", org_id=p.org_id, principal=p, resource_type="api_key", resource_id=key.id)
        await db.commit()
    return _key_out(key)


@router.post("/api-keys/{key_id}/rotate", status_code=201)
async def rotate_key(key_id: UUID, p: Principal = Depends(require("api_keys:manage")), db: AsyncSession = Depends(get_db)):
    old = await get_owned(db, ApiKey, key_id, p, "API key", for_update=True)
    if old.revoked_at:
        raise ApiError(409, "key_revoked", "Revoked keys cannot be rotated")
    new, raw = _mint(p.org_id, old.name, old.scopes, p.user_id, old.expires_at, rotated_from=old.id)
    old.revoked_at = datetime.now(UTC)
    db.add(new)
    await db.flush()
    audit.record(db, "api_key.rotated", org_id=p.org_id, principal=p, resource_type="api_key", resource_id=new.id, details={"rotated_from": str(old.id)})
    await db.commit()
    return {**_key_out(new), "secret": raw, "secret_notice": "Store this secret now. The previous key has been revoked."}


# --------------------------------------------------------------------------- settings & system
@router.get("/settings")
async def get_settings_(p: Principal = Depends(require("policies:read")), db: AsyncSession = Depends(get_db)):
    org = await db.get(Organization, p.org_id)
    st = org.settings or {}
    return {"organization": {"id": org.id, "name": org.name, "slug": org.slug, "status": org.status, "created_at": org.created_at},
            "risk_weights": {**DEFAULT_WEIGHTS, **(st.get("risk_weights") or {})},
            "risk_thresholds": {**DEFAULT_THRESHOLDS, **(st.get("risk_thresholds") or {})},
            "ai_enabled": st.get("ai_enabled", True), "ai_external_llm_allowed": st.get("ai_external_llm_allowed", False),
            "default_execution_mode": st.get("default_execution_mode", "inline")}


@router.patch("/settings")
async def update_settings(body: SettingsIn, p: Principal = Depends(require("settings:write")), db: AsyncSession = Depends(get_db)):
    org = await db.scalar(select(Organization).where(Organization.id == p.org_id).with_for_update())
    changes = body.model_dump(exclude_none=True)
    org.settings = {**(org.settings or {}), **changes}
    audit.record(db, "settings.updated", org_id=p.org_id, principal=p, resource_type="organization", resource_id=org.id, details=changes)
    await db.commit()
    return await get_settings_(p, db)


@router.get("/system/metrics")
async def system_metrics(p: Principal = Depends(require("settings:write")), db: AsyncSession = Depends(get_db)):
    try:
        depth = await redis.llen(QUEUE_KEY)
    except Exception:
        depth = None
    stuck = await db.scalar(select(func.count()).select_from(VerificationRequest).where(
        VerificationRequest.org_id == p.org_id, VerificationRequest.status.in_(["queued", "processing"]),
        VerificationRequest.updated_at < datetime.now(UTC) - timedelta(minutes=5)))
    # Cache counters and queue depth are platform-wide, so only platform admins see them.
    platform = p.can("platform:manage")
    return {"cache": await cache_stats() if platform else None, "queue_depth": depth if platform else None, "stale_in_flight": stuck,
            "circuit_breakers": {k: breaker_for(p.org_id, k).state for k in REGISTRY}}


# --------------------------------------------------------------------------- platform (super admin)
@router.get("/admin/organizations")
async def platform_orgs(p: Principal = Depends(require("platform:manage")), db: AsyncSession = Depends(get_db)):
    """Organization metadata only. Platform admins get NO tenant customer data from here."""
    rows = (await db.execute(select(Organization, func.count(OrganizationMembership.id)).outerjoin(
        OrganizationMembership, OrganizationMembership.org_id == Organization.id).group_by(Organization.id).order_by(Organization.created_at))).all()
    return [{"id": o.id, "name": o.name, "slug": o.slug, "status": o.status, "members": n, "created_at": o.created_at} for o, n in rows]


@router.patch("/admin/organizations/{org_id}")
async def platform_org_status(org_id: UUID, body: OrgStatusIn, p: Principal = Depends(require("platform:manage")), db: AsyncSession = Depends(get_db)):
    if org_id == p.org_id and body.status == "suspended":
        raise forbidden("You cannot suspend your own organization")
    org = await db.get(Organization, org_id)
    if not org:
        raise not_found("organization")
    org.status = body.status
    audit.record(db, "platform.org_status", org_id=org.id, principal=p, resource_type="organization", resource_id=org.id, details={"status": body.status})
    await db.commit()
    return {"id": org.id, "status": org.status}
