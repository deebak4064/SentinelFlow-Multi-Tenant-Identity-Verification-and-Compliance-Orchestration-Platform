import logging
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import errors
from app.api.routes import (
    admin,
    auth,
    cases,
    customers,
    insights,
    policies,
    providers,
    risk,
    verifications,
)
from app.config import get_settings
from app.db import SessionLocal, engine
from app.services.audit import client_ip_var, request_id_var
from app.services.bootstrap import roles_exist, seed_reference_data
from app.services.cache import redis

settings = get_settings()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sentinelflow")
MAX_BODY_BYTES = 256 * 1024
REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
]
API_CSP = (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'")
DOCS_CSP = (b"content-security-policy",
            b"default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com")


class RequestContextMiddleware:
    """Pure ASGI: request id, client ip, security headers, body-size limit, access log."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        supplied = headers.get(b"x-request-id", b"").decode("latin-1")[:64]
        rid = supplied if REQUEST_ID_RE.fullmatch(supplied) else uuid.uuid4().hex  # never log/store arbitrary client bytes
        request_id_var.set(rid)
        client_ip_var.set(scope["client"][0] if scope.get("client") else None)
        if int(headers.get(b"content-length", b"0") or 0) > MAX_BODY_BYTES:
            resp = JSONResponse({"error": {"code": "payload_too_large", "message": "Request body too large", "details": None, "request_id": rid}}, 413)
            return await resp(scope, receive, send)
        path = scope["path"]
        start, status = time.perf_counter(), 500

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                h = list(message.get("headers", []))
                h += SECURITY_HEADERS + [DOCS_CSP if path.startswith(("/docs", "/redoc")) else API_CSP, (b"x-request-id", rid.encode())]
                if settings.cookie_secure:
                    h.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message["headers"] = h
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if not path.startswith("/health"):
                log.info("%s %s %s %.1fms rid=%s", scope["method"], path, status, (time.perf_counter() - start) * 1000, rid)


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with SessionLocal() as db:
        if not await roles_exist(db):
            await seed_reference_data(db)
            await db.commit()
    yield
    await engine.dispose()


app = FastAPI(
    title="SentinelFlow API",
    version="1.0.0",
    description="Identity verification & compliance orchestration. All providers are simulations; "
                "results are not legal compliance certification.",
    lifespan=lifespan,
)
errors.install(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # explicit allowlist, never "*" with credentials
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token", "X-API-Key", "Idempotency-Key", "Authorization", "X-Request-Id"],
    expose_headers=["X-Request-Id", "X-RateLimit-Remaining", "Idempotent-Replayed"],
)
app.add_middleware(RequestContextMiddleware)

api = APIRouter(prefix="/api/v1")
for r in (auth, verifications, customers, risk, policies, cases, providers, admin, insights):
    api.include_router(r.router)
app.include_router(api)


@app.get("/health/live", tags=["health"])
async def live():
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"])
async def ready():
    checks = {}
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)
