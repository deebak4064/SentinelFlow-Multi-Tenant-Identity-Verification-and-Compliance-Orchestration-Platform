"""Test harness: real PostgreSQL + Redis (no mocks for the data layer).

Each test gets fresh organizations, so tests are isolated without truncating the append-only
audit table.
"""

import os
import subprocess
import sys
import uuid

os.environ.setdefault("SF_DATABASE_URL", "postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinelflow_test")
os.environ.setdefault("SF_REDIS_URL", "redis://localhost:6379/1")
os.environ["SF_LOGIN_RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["SF_RATE_LIMIT_PER_MINUTE"] = "100000"

import httpx  # noqa: E402
import pytest  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(__file__))
PASSWORD = "CorrectHorse42battery"


@pytest.fixture(scope="session", autouse=True)
async def _database():
    url = os.environ["SF_DATABASE_URL"].replace("+psycopg", "")
    subprocess.run(["psql", url, "-q", "-c", "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"], check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, check=True)
    from app.db import SessionLocal
    from app.services.bootstrap import seed_reference_data

    async with SessionLocal() as db:
        await seed_reference_data(db)
        await db.commit()
    yield


@pytest.fixture(autouse=True)
async def _reset_redis():
    from app.engine import providers
    from app.services.cache import redis

    await redis.flushdb()
    providers._breakers.clear()
    yield


@pytest.fixture
async def client():
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


class Org:
    def __init__(self, client: httpx.AsyncClient, org_id: str, admin_token: str, admin_id: str):
        self.client, self.id, self.admin_token, self.admin_id = client, org_id, admin_token, admin_id
        self.tokens: dict[str, str] = {"org_admin": admin_token}
        self.user_ids: dict[str, str] = {"org_admin": admin_id}

    def h(self, role: str = "org_admin") -> dict:
        return {"Authorization": f"Bearer {self.tokens[role]}"}

    async def add_member(self, role: str, name: str | None = None) -> str:
        email = f"{role}.{uuid.uuid4().hex[:8]}@test.example"
        r = await self.client.post("/api/v1/users", headers=self.h(), json={"email": email, "full_name": name or role.title(), "role": role, "temporary_password": PASSWORD})
        assert r.status_code == 201, r.text
        tok = await self.client.post("/api/v1/auth/token", json={"email": email, "password": PASSWORD, "organization_id": self.id})
        key = role if role not in self.tokens else f"{role}:{email}"
        self.tokens[key], self.user_ids[key] = tok.json()["access_token"], r.json()["id"]
        return key

    async def fast_providers(self, **sim) -> None:
        for key in ("sim_idv", "sim_kyb", "sim_watchlist"):
            r = await self.client.patch(f"/api/v1/providers/{key}", headers=self.h(),
                                        json={"timeout_ms": 200, "max_retries": 2, "simulation": {"latency_ms": 1, "failure_rate": 0, "timeout_rate": 0, **sim}})
            assert r.status_code == 200, r.text

    async def verify(self, role: str = "org_admin", **kw) -> httpx.Response:
        return await self.client.post("/api/v1/verifications", headers=self.h(role), json=individual(**kw))


@pytest.fixture
async def make_org(client):
    async def factory(name: str = "Test Org") -> Org:
        email = f"admin.{uuid.uuid4().hex[:10]}@test.example"
        r = await client.post("/api/v1/auth/register", json={"email": email, "full_name": "Admin User", "password": PASSWORD, "organization_name": name})
        assert r.status_code == 201, r.text
        body = r.json()
        client.cookies.clear()
        tok = (await client.post("/api/v1/auth/token", json={"email": email, "password": PASSWORD})).json()["access_token"]
        org = Org(client, body["organization"]["id"], tok, body["user"]["id"])
        await org.fast_providers()
        return org

    return factory


@pytest.fixture
async def org(make_org):
    return await make_org()


def individual(name: str = "Amara Okafor", doc: str = "P12345678", ref: str | None = None, **overrides) -> dict:
    subject = {"full_name": name, "dob": "1990-04-12", "country": "GB", "document_type": "passport", "document_number": doc,
               "email": "amara@example.com"}
    subject.update(overrides.pop("subject", {}))
    return {"external_ref": ref or f"ref-{uuid.uuid4().hex[:12]}", "verification_type": "individual", "subject": subject, **overrides}


def business(company: str = "Brightline Logistics Ltd", reg: str = "RC123456", ref: str | None = None) -> dict:
    return {"external_ref": ref or f"biz-{uuid.uuid4().hex[:12]}", "verification_type": "business",
            "subject": {"company_name": company, "registration_number": reg, "country": "DE", "email": "ops@example.com"}}
