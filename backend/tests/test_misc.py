import uuid

import redis.asyncio as aioredis
from sqlalchemy import text

from app import worker
from app.db import SessionLocal
from app.services import cache
from tests.conftest import PASSWORD


async def test_risk_workspace_endpoints(org):
    await org.verify(name="Viktor Sanctionov")
    await org.verify()
    lst = (await org.client.get("/api/v1/risk-assessments?category=critical", headers=org.h())).json()
    assert lst["total"] == 1 and lst["items"][0]["verification"]["decision"] == "escalated"
    assert (await org.client.get("/api/v1/risk-assessments?min_score=90", headers=org.h())).json()["total"] == 0
    s = (await org.client.get("/api/v1/risk-assessments/summary", headers=org.h())).json()
    assert s["categories"]["critical"] == 1 and s["categories"]["low"] == 1
    assert sum(h["count"] for h in s["histogram"]) == 2 and s["avg_factor_points"]["floor"] > 0


async def test_worker_reaps_stale_and_processes(org):
    r = await org.verify(execution_mode="queue")
    vid = r.json()["id"]
    async with SessionLocal() as db:
        await db.execute(text("UPDATE verification_requests SET updated_at = now() - interval '10 minutes' WHERE id = :i"), {"i": vid})
        await db.commit()
    await cache.redis.delete(cache.QUEUE_KEY)
    assert await worker.reap_stale() >= 1
    queued = await cache.redis.lrange(cache.QUEUE_KEY, 0, -1)
    assert vid in queued
    await worker.handle(vid)
    await worker.handle(vid)  # duplicate delivery is harmless
    b = (await org.client.get(f"/api/v1/verifications/{vid}", headers=org.h())).json()
    assert b["status"] == "completed" and b["workflow"]["execution_mode"] == "queue"


async def test_redis_outage_fails_open_and_reports_degraded(org, monkeypatch):
    dead = aioredis.from_url("redis://localhost:1/0", socket_connect_timeout=0.2)
    monkeypatch.setattr(cache, "redis", dead)
    r = await org.verify()  # rate limiter + cache unavailable: request still served
    assert r.status_code == 201
    from app import main
    monkeypatch.setattr(main, "redis", dead)
    ready = await org.client.get("/health/ready")
    assert ready.status_code == 503 and ready.json()["checks"]["redis"] == "unavailable"
    assert await cache.cache_stats() == {}


async def test_queue_unavailable_falls_back_to_inline(org, monkeypatch):
    async def boom(_):
        raise aioredis.ConnectionError("down")
    from app.api.routes import verifications
    monkeypatch.setattr(verifications, "enqueue_workflow", boom)
    r = await org.verify(execution_mode="queue")
    assert r.status_code == 201 and r.json()["status"] == "completed"


async def test_session_management_and_org_switch(make_org, client):
    a, b = await make_org(), await make_org()
    email = f"multi.{uuid.uuid4().hex[:6]}@test.example"
    await a.client.post("/api/v1/users", headers=a.h(), json={"email": email, "full_name": "Multi Org", "role": "reviewer", "temporary_password": PASSWORD})
    await b.client.post("/api/v1/users", headers=b.h(), json={"email": email, "full_name": "Multi Org", "role": "auditor", "temporary_password": PASSWORD})
    tok = (await client.post("/api/v1/auth/token", json={"email": email, "password": PASSWORD, "organization_id": a.id})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    me = (await client.get("/api/v1/auth/me", headers=h)).json()
    assert me["role"] == "reviewer" and len(me["memberships"]) == 2
    me = (await client.post("/api/v1/auth/switch-org", headers=h, json={"organization_id": b.id})).json()
    assert me["role"] == "auditor" and me["organization"]["id"] == b.id
    assert (await client.get("/api/v1/audit-events", headers=h)).status_code == 200  # auditor perms now apply
    other = (await client.post("/api/v1/auth/token", json={"email": email, "password": PASSWORD})).json()["access_token"]
    sessions = (await client.get("/api/v1/auth/sessions", headers=h)).json()
    assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
    victim = next(s for s in sessions if not s["current"])
    assert (await client.delete(f"/api/v1/auth/sessions/{victim['id']}", headers=h)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {other}"})).status_code == 401
    # cannot revoke someone else's session
    assert (await client.delete(f"/api/v1/auth/sessions/{uuid.uuid4()}", headers=h)).status_code == 404


async def test_duplicate_registration_and_membership(org, client):
    me = (await client.get("/api/v1/auth/me", headers=org.h())).json()
    r = await client.post("/api/v1/auth/register", json={"email": me["user"]["email"], "full_name": "Again", "password": PASSWORD, "organization_name": "Again Org"})
    assert r.status_code == 409
    key = await org.add_member("reviewer")
    users = (await client.get("/api/v1/users", headers=org.h())).json()
    email = next(u["email"] for u in users if u["id"] == org.user_ids[key])
    r = await client.post("/api/v1/users", headers=org.h(), json={"email": email, "full_name": "Dup", "role": "reviewer", "temporary_password": PASSWORD})
    assert r.status_code == 409
    roles = (await client.get("/api/v1/roles", headers=org.h())).json()
    assert [r["key"] for r in roles["roles"]][0] == "super_admin" and "platform:manage" in roles["permissions"]


async def test_api_key_principal_me_and_system_metrics(org):
    k = (await org.client.post("/api/v1/api-keys", headers=org.h(), json={"name": "svc", "scopes": ["verifications:read"]})).json()
    me = (await org.client.get("/api/v1/auth/me", headers={"X-API-Key": k["secret"]})).json()
    assert me["permissions"] == ["verifications:read"]
    m = (await org.client.get("/api/v1/system/metrics", headers=org.h())).json()
    assert set(m["circuit_breakers"]) == {"sim_idv", "sim_kyb", "sim_watchlist"}


async def test_request_id_is_sanitized(client):
    ok = await client.get("/health/live", headers={"X-Request-Id": "trace-123.abc"})
    assert ok.headers["x-request-id"] == "trace-123.abc"
    bad = await client.get("/health/live", headers={"X-Request-Id": "evil\" injected <script>"})
    assert bad.headers["x-request-id"] != "evil\" injected <script>" and len(bad.headers["x-request-id"]) == 32


async def test_system_metrics_hide_platform_counters_from_tenants(org):
    await org.add_member("compliance_analyst")
    assert (await org.client.get("/api/v1/system/metrics", headers=org.h("compliance_analyst"))).status_code == 403
    m = (await org.client.get("/api/v1/system/metrics", headers=org.h())).json()
    assert m["cache"] is None and m["queue_depth"] is None
