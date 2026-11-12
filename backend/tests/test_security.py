import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.config import get_settings
from app.db import SessionLocal
from tests.conftest import PASSWORD, individual

V = "/api/v1/verifications"


# --------------------------------------------------------------------------- tenant isolation (IDOR)
async def test_cross_tenant_access_returns_404_everywhere(make_org):
    a, b = await make_org("Tenant A"), await make_org("Tenant B")
    va = (await a.verify(doc="P1235555")).json()  # creates a case too
    pol = (await a.client.get("/api/v1/policies", headers=a.h())).json()[0]
    risk_id = va["risk_assessment"]["id"]
    for url in (f"{V}/{va['id']}", f"/api/v1/customers/{va['customer_id']}", f"/api/v1/cases/{va['case']['id']}",
                f"/api/v1/policies/{pol['id']}", f"/api/v1/risk-assessments/{risk_id}"):
        r = await b.client.get(url, headers=b.h())
        assert r.status_code == 404, url
        assert r.json()["error"]["code"] == "not_found"
    # writes are equally blocked
    for method, url, body in (
        ("post", f"{V}/{va['id']}/review", {"decision": "approve", "notes": "steal"}),
        ("post", f"/api/v1/cases/{va['case']['id']}/decision", {"decision": "approve", "notes": "steal"}),
        ("post", f"/api/v1/cases/{va['case']['id']}/comments", {"body": "hi"}),
        ("patch", f"/api/v1/policies/{pol['id']}", {"priority": 1}),
        ("post", f"/api/v1/policies/{pol['id']}/deactivate", None),
    ):
        r = await getattr(b.client, method)(url, headers=b.h(), json=body)
        assert r.status_code == 404, url
    # lists never leak
    assert (await b.client.get(V, headers=b.h())).json()["total"] == 0
    assert (await b.client.get("/api/v1/customers", headers=b.h())).json()["total"] == 0
    assert (await b.client.get("/api/v1/cases", headers=b.h())).json()["total"] == 0
    assert all(e["actor_id"] != a.admin_id for e in (await b.client.get("/api/v1/audit-events", headers=b.h())).json()["items"])


async def test_cannot_assign_case_to_member_of_another_org(make_org):
    a, b = await make_org(), await make_org()
    case = (await a.verify(doc="P1235555")).json()["case"]
    outsider = await b.add_member("reviewer")
    r = await a.client.post(f"/api/v1/cases/{case['id']}/assign", headers=a.h(), json={"assignee_id": b.user_ids[outsider]})
    assert r.status_code == 422


async def test_database_rejects_cross_tenant_foreign_keys(make_org):
    a, b = await make_org(), await make_org()
    va = (await a.verify()).json()
    async with SessionLocal() as db:
        with pytest.raises(IntegrityError):
            await db.execute(text(
                "INSERT INTO verification_requests (id, org_id, customer_id, verification_type, status, request_hash, input_snapshot, submitted_by_type, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :org, :cust, 'individual', 'queued', 'x', '{}', 'user', now(), now())"),
                {"org": b.id, "cust": va["customer_id"]})
        await db.rollback()


async def test_switch_org_requires_membership(make_org):
    a, b = await make_org(), await make_org()
    r = await a.client.post("/api/v1/auth/switch-org", headers=a.h(), json={"organization_id": b.id})
    assert r.status_code == 404


# --------------------------------------------------------------------------- RBAC & escalation
async def test_role_matrix_enforced(org):
    for role in ("auditor", "reviewer", "compliance_analyst"):
        await org.add_member(role)
    expectations = [
        ("auditor", "post", V, individual(), 403),
        ("auditor", "get", "/api/v1/audit-events", None, 200),
        ("reviewer", "get", "/api/v1/audit-events", None, 403),
        ("reviewer", "post", "/api/v1/users", {"email": "x@example.com", "full_name": "X Y", "role": "reviewer", "temporary_password": PASSWORD}, 403),
        ("reviewer", "post", "/api/v1/api-keys", {"name": "k1", "scopes": ["verifications:read"]}, 403),
        ("compliance_analyst", "patch", "/api/v1/settings", {"ai_enabled": False}, 403),
        ("compliance_analyst", "post", V, individual(), 201),
        ("auditor", "patch", "/api/v1/providers/sim_idv", {"enabled": False}, 403),
        ("org_admin", "get", "/api/v1/admin/organizations", None, 403),
    ]
    for role, method, url, body, status in expectations:
        kw = {"json": body} if body is not None else {}
        r = await getattr(org.client, method)(url, headers=org.h(role), **kw)
        assert r.status_code == status, (role, url, r.text)


async def test_permission_denials_are_audited(org):
    await org.add_member("auditor")
    await org.client.post(V, headers=org.h("auditor"), json=individual())
    events = (await org.client.get("/api/v1/audit-events?action=authz.denied", headers=org.h())).json()["items"]
    assert events and events[0]["details"]["missing"] == ["verifications:create"]


async def test_privilege_escalation_blocked(org):
    analyst = await org.add_member("compliance_analyst")
    # org admin cannot mint super admins
    r = await org.client.post("/api/v1/users", headers=org.h(), json={"email": "su@example.com", "full_name": "Su Per", "role": "super_admin", "temporary_password": PASSWORD})
    assert r.status_code == 403
    # cannot change own role
    assert (await org.client.patch(f"/api/v1/users/{org.admin_id}/role", headers=org.h(), json={"role": "super_admin"})).status_code == 403
    # last admin cannot be demoted/removed
    second = await org.add_member("org_admin")
    await org.client.delete(f"/api/v1/users/{org.user_ids[second]}", headers=org.h())
    r = await org.client.patch(f"/api/v1/users/{org.user_ids[analyst]}/role", headers=org.h(), json={"role": "reviewer"})
    assert r.status_code == 200
    # unknown role
    assert (await org.client.patch(f"/api/v1/users/{org.user_ids[analyst]}/role", headers=org.h(), json={"role": "god"})).status_code == 403


async def test_last_admin_guard(org):
    a2 = await org.add_member("org_admin")
    # a second admin may demote the first...
    assert (await org.client.patch(f"/api/v1/users/{org.admin_id}/role", headers=org.h(a2), json={"role": "reviewer"})).status_code == 200
    # ...but the last remaining admin cannot remove themselves, and nobody below admin can touch them
    assert (await org.client.delete(f"/api/v1/users/{org.user_ids[a2]}", headers=org.h(a2))).status_code == 403
    assert (await org.client.patch(f"/api/v1/users/{org.user_ids[a2]}/role", headers=org.h("org_admin"), json={"role": "reviewer"})).status_code == 403


async def test_api_key_scope_cannot_exceed_creator_or_grantable(org):
    r = await org.client.post("/api/v1/api-keys", headers=org.h(), json={"name": "bad", "scopes": ["users:write"]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_scopes"


async def test_api_key_lifecycle_revoke_and_rotate(org):
    k = (await org.client.post("/api/v1/api-keys", headers=org.h(), json={"name": "svc", "scopes": ["verifications:read"]})).json()
    assert k["secret"].startswith("sf_live_")
    h = {"X-API-Key": k["secret"]}
    assert (await org.client.get(V, headers=h)).status_code == 200
    assert (await org.client.post(V, headers=h, json=individual())).status_code == 403  # scope
    assert (await org.client.get("/api/v1/users", headers=h)).status_code == 403
    rot = (await org.client.post(f"/api/v1/api-keys/{k['id']}/rotate", headers=org.h())).json()
    assert (await org.client.get(V, headers=h)).status_code == 401  # old key dead after rotation
    h2 = {"X-API-Key": rot["secret"]}
    assert (await org.client.get(V, headers=h2)).status_code == 200
    await org.client.post(f"/api/v1/api-keys/{rot['id']}/revoke", headers=org.h())
    r = await org.client.get(V, headers=h2)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    keys = (await org.client.get("/api/v1/api-keys", headers=org.h())).json()
    assert all("secret" not in x and "key_hash" not in x for x in keys)


async def test_api_key_cannot_be_used_across_tenants(make_org):
    a, b = await make_org(), await make_org()
    vb = (await b.verify()).json()
    k = (await a.client.post("/api/v1/api-keys", headers=a.h(), json={"name": "svc", "scopes": ["verifications:read"]})).json()
    assert (await a.client.get(f"{V}/{vb['id']}", headers={"X-API-Key": k["secret"]})).status_code == 404


async def test_expired_api_key(org):
    k = (await org.client.post("/api/v1/api-keys", headers=org.h(), json={"name": "svc", "scopes": ["verifications:read"], "expires_in_days": 1})).json()
    async with SessionLocal() as db:
        await db.execute(text("UPDATE api_keys SET expires_at = now() - interval '1 minute' WHERE id = :i"), {"i": k["id"]})
        await db.commit()
    assert (await org.client.get(V, headers={"X-API-Key": k["secret"]})).status_code == 401


# --------------------------------------------------------------------------- authentication
@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer "}, {"Authorization": "Bearer sfs_forged"}, {"Authorization": "Basic YWRtaW46YWRtaW4="},
    {"X-API-Key": "sf_live_nope"}, {"Authorization": "Bearer ' OR 1=1 --"},
])
async def test_invalid_tokens(client, headers):
    r = await client.get(V, headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


async def test_logout_revokes_session(org):
    h = org.h()
    assert (await org.client.post("/api/v1/auth/logout", headers=h)).status_code == 204
    assert (await org.client.get(V, headers=h)).status_code == 401


async def test_expired_session(org):
    async with SessionLocal() as db:
        await db.execute(text("UPDATE user_sessions SET expires_at = now() - interval '1 second' WHERE user_id = :u"), {"u": org.admin_id})
        await db.commit()
    assert (await org.client.get(V, headers=org.h())).status_code == 401


async def test_cookie_session_requires_csrf_for_writes(client):
    email = f"c.{uuid.uuid4().hex[:8]}@test.example"
    r = await client.post("/api/v1/auth/register", json={"email": email, "full_name": "Cookie User", "password": PASSWORD, "organization_name": "Cookie Org"})
    csrf = r.json()["csrf_token"]
    assert "sf_session" in client.cookies
    assert (await client.get(V)).status_code == 200  # safe method: no CSRF needed
    r = await client.post(V, json=individual())
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert (await client.post(V, json=individual(), headers={"X-CSRF-Token": "wrong"})).status_code == 403
    assert (await client.post(V, json=individual(), headers={"X-CSRF-Token": csrf})).status_code == 201
    set_cookie = r.headers.get("set-cookie", "")
    client.cookies.clear()
    assert set_cookie == ""  # no cookie rotation on failure


async def test_session_cookie_flags(client):
    r = await client.post("/api/v1/auth/register", json={"email": f"f.{uuid.uuid4().hex[:8]}@test.example", "full_name": "Flag User", "password": PASSWORD, "organization_name": "Flags"})
    cookies = r.headers.get_list("set-cookie")
    sess = next(c for c in cookies if c.startswith("sf_session="))
    assert "HttpOnly" in sess and "SameSite=lax" in sess
    assert "HttpOnly" not in next(c for c in cookies if c.startswith("sf_csrf="))
    client.cookies.clear()


async def test_login_errors_do_not_enumerate_users(client, org):
    r1 = await client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": "wrongpassword1"})
    r2 = await client.post("/api/v1/auth/token", json={"email": "nobody2@example.com", "password": "x"})
    assert r1.status_code == r2.status_code == 401 and r1.json()["error"]["message"] == r2.json()["error"]["message"]


async def test_account_lockout(client, make_org):
    email = f"lock.{uuid.uuid4().hex[:8]}@test.example"
    await client.post("/api/v1/auth/register", json={"email": email, "full_name": "Lock User", "password": PASSWORD, "organization_name": "Lock"})
    client.cookies.clear()
    for _ in range(get_settings().max_failed_logins):
        assert (await client.post("/api/v1/auth/token", json={"email": email, "password": "wrong-password-1"})).status_code == 401
    r = await client.post("/api/v1/auth/token", json={"email": email, "password": PASSWORD})
    assert r.status_code == 423  # correct password still refused while locked


async def test_weak_password_rejected(client):
    r = await client.post("/api/v1/auth/register", json={"email": "w@example.com", "full_name": "Weak", "password": "short", "organization_name": "W"})
    assert r.status_code == 422


# --------------------------------------------------------------------------- rate limiting
async def test_api_rate_limit(org, monkeypatch):
    from app.services.cache import redis

    monkeypatch.setattr(get_settings(), "rate_limit_per_minute", 5)
    await redis.flushdb()
    codes = [(await org.client.get(V, headers=org.h())).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]
    r = await org.client.get(V, headers=org.h())
    assert r.json()["error"]["code"] == "rate_limited" and int(r.headers["Retry-After"]) > 0


async def test_login_rate_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    codes = [(await client.post("/api/v1/auth/token", json={"email": "rl@example.com", "password": "nope"})).status_code for _ in range(5)]
    assert codes[:3] == [401] * 3 and codes[3:] == [429, 429]


# --------------------------------------------------------------------------- PII
async def test_pii_masked_by_default_and_reveal_is_permissioned_and_audited(org):
    await org.add_member("auditor")
    await org.add_member("compliance_analyst")
    v = (await org.verify(name="Priya Raman", doc="P55554444")).json()
    url = f"/api/v1/customers/{v['customer_id']}"
    masked = (await org.client.get(url, headers=org.h("auditor"))).json()
    assert masked["full_name"] == "P*** R***" and masked["document_number"] == "*****4444" and masked["dob"] == "1990-**-**"
    assert (await org.client.get(url + "?reveal=true", headers=org.h("auditor"))).status_code == 403
    full = (await org.client.get(url + "?reveal=true", headers=org.h("compliance_analyst"))).json()
    assert full["full_name"] == "Priya Raman" and full["document_number"] == "P55554444" and full["pii_masked"] is False
    events = (await org.client.get("/api/v1/audit-events?action=customer.pii_revealed", headers=org.h())).json()["items"]
    assert len(events) == 1 and events[0]["resource_id"] == v["customer_id"]


# --------------------------------------------------------------------------- cases: resource-level permissions
async def test_reviewer_can_only_decide_assigned_cases(org):
    r1, r2 = await org.add_member("reviewer"), await org.add_member("reviewer")
    case = (await org.verify(doc="P1235555")).json()["case"]
    url = f"/api/v1/cases/{case['id']}"
    body = {"decision": "approve", "notes": "looks fine"}
    assert (await org.client.post(f"{url}/decision", headers=org.h(r1), json=body)).status_code == 403  # unassigned
    await org.client.post(f"{url}/assign", headers=org.h(), json={"assignee_id": org.user_ids[r1]})
    assert (await org.client.post(f"{url}/decision", headers=org.h(r2), json=body)).status_code == 403  # someone else's
    assert (await org.client.post(f"{url}/decision", headers=org.h(r1), json=body)).status_code == 200


async def test_escalated_case_needs_analyst(org):
    rev = await org.add_member("reviewer")
    ana = await org.add_member("compliance_analyst")
    case = (await org.verify(name="Viktor Sanctionov")).json()["case"]
    assert case["status"] == "escalated"
    url = f"/api/v1/cases/{case['id']}"
    await org.client.post(f"{url}/assign", headers=org.h(), json={"assignee_id": org.user_ids[rev]})
    body = {"decision": "reject", "notes": "confirmed match"}
    assert (await org.client.post(f"{url}/decision", headers=org.h(rev), json=body)).status_code == 403
    assert (await org.client.post(f"{url}/decision", headers=org.h(ana), json=body)).status_code == 200


async def test_concurrent_decisions_only_one_wins(org):
    case = (await org.verify(doc="P1235555")).json()["case"]
    url = f"/api/v1/cases/{case['id']}/decision"
    rs = await asyncio.gather(*[org.client.post(url, headers=org.h(), json={"decision": d, "notes": "race test"}) for d in ("approve", "reject", "approve")])
    assert sorted(r.status_code for r in rs) == [200, 409, 409]


# --------------------------------------------------------------------------- audit & platform
async def test_audit_log_is_immutable_in_database(org):
    async with SessionLocal() as db:
        for stmt in ("UPDATE audit_events SET action = 'tampered'", "DELETE FROM audit_events", "TRUNCATE audit_events"):
            with pytest.raises(DBAPIError, match="append-only"):
                await db.execute(text(stmt))
            await db.rollback()


async def test_suspended_org_is_locked_out(make_org, client):
    org = await make_org()
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organizations SET status='suspended' WHERE id=:i"), {"i": org.id})
        await db.commit()
    r = await client.get(V, headers=org.h())
    assert r.status_code == 403 and "suspended" in r.json()["error"]["message"]


async def test_super_admin_platform_view_has_no_customer_data(org):
    async with SessionLocal() as db:
        await db.execute(text("UPDATE organization_memberships SET role_key='super_admin' WHERE user_id=:u"), {"u": org.admin_id})
        await db.commit()
    r = await org.client.get("/api/v1/admin/organizations", headers=org.h())
    assert r.status_code == 200 and set(r.json()[0]) == {"id", "name", "slug", "status", "members", "created_at"}


# --------------------------------------------------------------------------- hardening
async def test_security_headers_and_request_id(client):
    r = await client.get("/health/live", headers={"X-Request-Id": "abc123"})
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["x-request-id"] == "abc123"


async def test_cors_allowlist(client):
    bad = await client.options(V, headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    good = await client.options(V, headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers
    assert good.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_sql_injection_payloads_are_inert(org):
    await org.verify()
    for q in ("' OR '1'='1", "x'; DROP TABLE customers; --", "%' UNION SELECT password_hash FROM users --"):
        for url in (f"{V}?q={q}", f"/api/v1/customers?q={q}"):
            r = await org.client.get(url, headers=org.h())
            assert r.status_code == 200 and r.json()["total"] == 0
    assert (await org.client.get(f"{V}?sort=created_at;drop", headers=org.h())).status_code == 422
    assert (await org.client.get(f"{V}/not-a-uuid", headers=org.h())).status_code == 422
    assert (await org.client.get("/api/v1/customers", headers=org.h())).json()["total"] == 1  # still intact
