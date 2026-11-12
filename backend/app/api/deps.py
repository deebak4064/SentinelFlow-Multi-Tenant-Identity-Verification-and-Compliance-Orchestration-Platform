"""Authentication, authorization and query helpers shared by all routes.

Auth modes:
  * Browser sessions: opaque random token in an HttpOnly, SameSite=Lax cookie. Only its SHA-256
    is stored server-side. Unsafe methods additionally require the X-CSRF-Token header to match
    the per-session CSRF secret (double-submit, bound to the session).
  * Bearer session tokens: same session table, sent as Authorization header (CLI/tests). No CSRF
    needed because browsers never attach this header automatically.
  * API keys: `X-API-Key: sf_live_...`, hashed at rest, scoped to a permission subset.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import Depends, Query, Request, Response
from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError, forbidden, not_found
from app.config import get_settings
from app.db import get_db
from app.models import ApiKey, Organization, OrganizationMembership, User, UserSession
from app.security.crypto import constant_time_eq, sha256
from app.security.permissions import Principal, role_permissions
from app.services import audit
from app.services.cache import rate_limit

SESSION_COOKIE, CSRF_COOKIE, CSRF_HEADER = "sf_session", "sf_csrf", "x-csrf-token"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
settings = get_settings()


def _unauthorized(msg: str = "Authentication required") -> ApiError:
    return ApiError(401, "unauthorized", msg, headers={"WWW-Authenticate": "Bearer"})


async def _apply_rate_limit(principal_key: str, response: Response) -> None:
    allowed, remaining, reset = await rate_limit(f"api:{principal_key}", settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise ApiError(429, "rate_limited", "Rate limit exceeded", headers={"Retry-After": str(reset)})


async def get_principal(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> Principal:
    now = datetime.now(UTC)

    if raw_key := request.headers.get("x-api-key"):
        key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == sha256(raw_key)))
        if not key or key.revoked_at or (key.expires_at and key.expires_at < now):
            raise _unauthorized("Invalid, expired or revoked API key")
        org = await db.get(Organization, key.org_id)
        if org.status != "active":
            raise forbidden("Organization is suspended")
        if not key.last_used_at or now - key.last_used_at > timedelta(minutes=1):
            await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
            await db.commit()
        p = Principal("api_key", key.org_id, frozenset(key.scopes), api_key_id=key.id, label=f"api_key:{key.prefix}")
        await _apply_rate_limit(f"k:{key.id}", response)
        return p

    auth = request.headers.get("authorization", "")
    via_cookie = not auth.lower().startswith("bearer ")
    token = request.cookies.get(SESSION_COOKIE) if via_cookie else auth[7:].strip()
    if not token:
        raise _unauthorized()

    row = (await db.execute(
        select(UserSession, User, OrganizationMembership, Organization)
        .join(User, User.id == UserSession.user_id)
        .join(OrganizationMembership, (OrganizationMembership.user_id == User.id) & (OrganizationMembership.org_id == UserSession.org_id))
        .join(Organization, Organization.id == UserSession.org_id)
        .where(UserSession.token_hash == sha256(token))
    )).first()
    if not row:
        raise _unauthorized("Invalid session")
    sess, user, membership, org = row
    if sess.revoked_at or sess.expires_at < now or not user.is_active:
        raise _unauthorized("Session expired or revoked")
    if org.status != "active":
        raise forbidden("Organization is suspended")
    if via_cookie and request.method in UNSAFE:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not constant_time_eq(sha256(header), sess.csrf_hash):
            raise ApiError(403, "csrf_failed", "Missing or invalid CSRF token")
    if now - sess.last_seen_at > timedelta(minutes=1):  # throttle writes
        await db.execute(update(UserSession).where(UserSession.id == sess.id).values(last_seen_at=now))
        await db.commit()

    p = Principal("user", org.id, role_permissions(membership.role_key), user_id=user.id, role=membership.role_key,
                  label=user.email, session_id=sess.id)
    await _apply_rate_limit(f"u:{user.id}", response)
    return p


def require(*perms: str):
    """Route dependency: authenticate, then require ALL listed permissions."""

    async def dep(principal: Principal = Depends(get_principal), db: AsyncSession = Depends(get_db)) -> Principal:
        missing = [p for p in perms if not principal.can(p)]
        if missing:
            audit.record(db, "authz.denied", org_id=principal.org_id, principal=principal, details={"missing": missing})
            await db.commit()
            raise forbidden()
        return principal

    return dep


async def get_owned(db: AsyncSession, model, obj_id: UUID, principal: Principal, what: str, *, for_update: bool = False):
    """Tenant-scoped fetch. The ONLY way routes load a tenant resource by id."""
    stmt = select(model).where(model.id == obj_id, model.org_id == principal.org_id)
    if for_update:
        stmt = stmt.with_for_update()
    obj = await db.scalar(stmt)
    if obj is None:
        raise not_found(what)
    return obj


def contains(q: str) -> str:
    """ILIKE pattern with user wildcards escaped (use with escape='\\')."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class PageParams:
    def __init__(self, page: int = Query(1, ge=1, le=100000), page_size: int = Query(25, ge=1, le=200),
                 sort: str | None = Query(None, max_length=40, pattern=r"^-?[a-z_]+$")):
        self.page, self.page_size, self.sort = page, page_size, sort


async def paginate(db: AsyncSession, stmt: Select, params: PageParams, sortable: dict, default_sort: str) -> tuple[list, int]:
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    sort = params.sort or default_sort
    col = sortable.get(sort.lstrip("-"))
    if col is None:  # whitelist only: user input never reaches SQL as an identifier
        raise ApiError(400, "invalid_sort", f"Cannot sort by '{sort}'. Allowed: {sorted(sortable)}")
    stmt = stmt.order_by(col.desc() if sort.startswith("-") else col.asc())
    rows = (await db.execute(stmt.limit(params.page_size).offset((params.page - 1) * params.page_size))).all()
    return rows, total or 0
