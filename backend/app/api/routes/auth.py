from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CSRF_COOKIE, SESSION_COOKIE, get_principal, settings
from app.api.errors import ApiError, not_found
from app.db import get_db
from app.models import Organization, OrganizationMembership, User, UserSession
from app.schemas import LoginIn, RegisterIn, SwitchOrgIn
from app.security.crypto import hash_password, new_token, sha256, verify_password
from app.security.permissions import ROLES, Principal, role_permissions
from app.services import audit
from app.services.bootstrap import provision_org
from app.services.cache import rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


async def _limit_login(request: Request, email: str) -> None:
    ip = request.client.host if request.client else "unknown"
    for bucket in (f"login:ip:{ip}", f"login:email:{email.lower()}"):
        ok, _, reset = await rate_limit(bucket, settings.login_rate_limit_per_minute)
        if not ok:
            raise ApiError(429, "rate_limited", "Too many login attempts", headers={"Retry-After": str(reset)})


async def _start_session(db: AsyncSession, request: Request, user: User, org_id: UUID) -> tuple[str, str, UserSession]:
    token, csrf = new_token("sfs_"), new_token()
    sess = UserSession(
        user_id=user.id, org_id=org_id, token_hash=sha256(token), csrf_hash=sha256(csrf),
        ip=request.client.host if request.client else None, user_agent=(request.headers.get("user-agent") or "")[:300],
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.session_ttl_minutes),
    )
    db.add(sess)
    await db.flush()
    return token, csrf, sess


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    max_age = settings.session_ttl_minutes * 60
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=settings.cookie_secure, samesite="lax", path="/")


async def _me(db: AsyncSession, user_id: UUID, org_id: UUID) -> dict:
    user = await db.get(User, user_id)
    rows = (await db.execute(
        select(OrganizationMembership, Organization).join(Organization, Organization.id == OrganizationMembership.org_id)
        .where(OrganizationMembership.user_id == user_id).order_by(Organization.name)
    )).all()
    active = next((m for m, o in rows if o.id == org_id), None)
    org = next((o for m, o in rows if o.id == org_id), None)
    return {
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "last_login_at": user.last_login_at},
        "organization": {"id": org.id, "name": org.name, "slug": org.slug, "status": org.status},
        "role": active.role_key,
        "role_name": ROLES[active.role_key]["name"],
        "permissions": sorted(role_permissions(active.role_key)),
        "memberships": [{"organization_id": o.id, "organization_name": o.name, "role": m.role_key} for m, o in rows],
    }


async def _authenticate(db: AsyncSession, request: Request, body: LoginIn) -> tuple[User, OrganizationMembership]:
    await _limit_login(request, body.email)
    user = await db.scalar(select(User).where(User.email == body.email.lower()))
    now = datetime.now(UTC)
    if user and user.locked_until and user.locked_until > now:
        raise ApiError(423, "account_locked", "Account temporarily locked after repeated failed logins")
    if not verify_password(body.password, user.password_hash if user else None) or not user.is_active:
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                user.failed_login_count = 0
            audit.record(db, "auth.login_failed", org_id=None, actor_type="user", actor_id=user.id, actor_label=user.email)
            await db.commit()
        raise ApiError(401, "invalid_credentials", "Invalid email or password")
    q = select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
    if body.organization_id:
        q = q.where(OrganizationMembership.org_id == body.organization_id)
    membership = await db.scalar(q.order_by(OrganizationMembership.created_at))
    if membership is None:
        raise ApiError(403, "no_membership", "User has no access to that organization")
    user.failed_login_count, user.locked_until, user.last_login_at = 0, None, now
    return user, membership


@router.post("/register", status_code=201)
async def register(body: RegisterIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    await _limit_login(request, body.email)
    if await db.scalar(select(User.id).where(User.email == body.email.lower())):
        raise ApiError(409, "email_taken", "An account with this email already exists")
    user = User(email=body.email.lower(), full_name=body.full_name, password_hash=hash_password(body.password))
    db.add(user)
    await db.flush()
    org = await provision_org(db, body.organization_name, created_by=user.id)
    db.add(OrganizationMembership(org_id=org.id, user_id=user.id, role_key="org_admin"))
    token, csrf, _ = await _start_session(db, request, user, org.id)
    audit.record(db, "organization.created", org_id=org.id, actor_type="user", actor_id=user.id, actor_label=user.email,
                 resource_type="organization", resource_id=org.id)
    await db.commit()
    _set_cookies(response, token, csrf)
    return {**await _me(db, user.id, org.id), "csrf_token": csrf}


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    user, m = await _authenticate(db, request, body)
    token, csrf, sess = await _start_session(db, request, user, m.org_id)
    audit.record(db, "auth.login", org_id=m.org_id, actor_type="user", actor_id=user.id, actor_label=user.email,
                 resource_type="session", resource_id=sess.id)
    await db.commit()
    _set_cookies(response, token, csrf)
    return {**await _me(db, user.id, m.org_id), "csrf_token": csrf}


@router.post("/token")
async def token(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    """Bearer token for non-browser clients (CLI, load tests). Not set as a cookie."""
    user, m = await _authenticate(db, request, body)
    tok, _, sess = await _start_session(db, request, user, m.org_id)
    audit.record(db, "auth.token_issued", org_id=m.org_id, actor_type="user", actor_id=user.id, actor_label=user.email,
                 resource_type="session", resource_id=sess.id)
    await db.commit()
    return {"access_token": tok, "token_type": "bearer", "expires_in": settings.session_ttl_minutes * 60}


@router.post("/logout", status_code=204)
async def logout(response: Response, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    if p.session_id:
        await db.execute(update(UserSession).where(UserSession.id == p.session_id).values(revoked_at=datetime.now(UTC)))
        audit.record(db, "auth.logout", org_id=p.org_id, principal=p)
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me")
async def me(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    if p.kind != "user":
        return {"api_key": {"id": p.api_key_id, "label": p.label}, "organization_id": p.org_id, "permissions": sorted(p.permissions)}
    return await _me(db, p.user_id, p.org_id)


@router.post("/switch-org")
async def switch_org(body: SwitchOrgIn, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    if p.kind != "user":
        raise ApiError(400, "not_supported", "API keys are bound to one organization")
    m = await db.scalar(select(OrganizationMembership).where(
        OrganizationMembership.user_id == p.user_id, OrganizationMembership.org_id == body.organization_id))
    if not m:
        raise not_found("organization")
    await db.execute(update(UserSession).where(UserSession.id == p.session_id).values(org_id=body.organization_id))
    audit.record(db, "auth.switch_org", org_id=body.organization_id, principal=p, details={"from": str(p.org_id)})
    await db.commit()
    return await _me(db, p.user_id, body.organization_id)


@router.get("/sessions")
async def sessions(p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(UserSession).where(
        UserSession.user_id == p.user_id, UserSession.revoked_at.is_(None), UserSession.expires_at > datetime.now(UTC)
    ).order_by(UserSession.last_seen_at.desc()).limit(50))).all()
    return [{"id": s.id, "ip": s.ip, "user_agent": s.user_agent, "created_at": s.created_at, "last_seen_at": s.last_seen_at,
             "expires_at": s.expires_at, "current": s.id == p.session_id} for s in rows]


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_session(session_id: UUID, p: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)):
    res = await db.execute(update(UserSession).where(UserSession.id == session_id, UserSession.user_id == p.user_id)
                           .values(revoked_at=datetime.now(UTC)))
    if res.rowcount == 0:
        raise not_found("session")
    audit.record(db, "auth.session_revoked", org_id=p.org_id, principal=p, resource_type="session", resource_id=session_id)
    await db.commit()
    return Response(status_code=204)
