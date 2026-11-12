"""Synthetic demo data: `python -m app.seed [--verifications N]`.

Every verification is pushed through the REAL orchestrator (providers, risk, policy, cases), so
the dashboard shows genuinely computed results. Only `created_at` timestamps are spread over the
last 14 days afterwards so that time-series charts have history. All people, companies and
documents are fictional.
"""

import argparse
import asyncio
import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update

from app.db import SessionLocal
from app.engine import orchestrator
from app.models import (
    ApiKey,
    ComplianceCase,
    OrganizationMembership,
    ProviderConfig,
    User,
    VerificationRequest,
)
from app.security.crypto import hash_password, new_token, sha256
from app.security.permissions import API_KEY_GRANTABLE, Principal, role_permissions
from app.services import cases as case_service
from app.services.bootstrap import provision_org, seed_reference_data

DEMO_PASSWORD = "SentinelDemo2026!"  # local demo only — documented in README
FIRST = ["Amara", "Liam", "Sofia", "Kenji", "Priya", "Mateo", "Elena", "Noah", "Aisha", "Lucas", "Mei", "Omar", "Hanna", "Ravi", "Chloe", "Diego"]
LAST = ["Okafor", "Lindqvist", "Moreau", "Tanaka", "Raman", "Silva", "Novak", "Fischer", "Haddad", "Costa", "Chen", "Nasser", "Berg", "Iyer", "Dubois", "Reyes"]
COMPANIES = ["Brightline Logistics", "Copperleaf Analytics", "Harbor & Pine Foods", "Lumen Grid Energy", "Northgate Textiles",
             "Quartzfield Robotics", "Saltmarsh Media", "Tidewater Freight", "Verdant Labs", "Willowbank Retail"]
COUNTRIES = ["GB", "DE", "FR", "NL", "SE", "ES", "IN", "SG", "US", "CA", "BR", "JP"]
WATCHLIST_LOOKALIKES = ["Viktor Sanctionov", "Victor Santino", "Mara Blacklistova", "Dmitri Flaggs", "Dimitri Flagson", "Helen Watchwell"]


def synthetic_individual(i: int, rng: random.Random) -> dict:
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    dob = f"{rng.randint(1955, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    doc = f"P{rng.randint(10_000_000, 99_999_999)}"
    roll = rng.random()
    suffix = None
    if roll < 0.05: suffix = "0000"        # forged / failed authenticity
    elif roll < 0.10: suffix = "5555"      # low image quality → review
    elif roll < 0.14: suffix = "1111"      # name mismatch on document
    elif roll < 0.17: suffix = "2222"      # DOB mismatch on document
    elif roll < 0.19: suffix = "3333"      # expired document
    if suffix:
        doc = doc[:-4] + suffix
    elif roll < 0.24:
        name = rng.choice(WATCHLIST_LOOKALIKES)
    subject = {"full_name": name, "dob": dob, "country": rng.choice(COUNTRIES), "document_type": "passport",
               "document_number": doc, "email": f"user{i}@example.com"}
    if roll > 0.97:
        subject.pop("document_number")     # missing mandatory field
    elif roll > 0.93:
        subject.pop("email")
    return {"external_ref": f"cust-ind-{i:05d}", "verification_type": "individual", "subject": subject}


def synthetic_business(i: int, rng: random.Random) -> dict:
    roll = rng.random()
    reg = f"RC{rng.randint(100000, 999999)}"
    if roll < 0.06: reg = "DISS" + reg[2:]
    elif roll < 0.10: reg = "NF" + reg[2:]
    elif roll < 0.14: reg = "UNK" + reg[2:]
    elif roll < 0.18: reg = reg[:-3] + "999"
    name = ("Orion Shellcorp Ltd" if roll < 0.195 else "Orion Shell Ltd") if 0.18 <= roll < 0.21 else f"{rng.choice(COMPANIES)} {rng.choice(['Ltd', 'GmbH', 'BV', 'Inc'])}"
    return {"external_ref": f"cust-biz-{i:05d}", "verification_type": "business",
            "subject": {"company_name": name, "registration_number": reg, "country": rng.choice(COUNTRIES), "email": f"ops{i}@example.com"}}


async def _user(db, email, name, org_id, role):
    u = await db.scalar(select(User).where(User.email == email))
    if not u:
        u = User(email=email, full_name=name, password_hash=hash_password(DEMO_PASSWORD))
        db.add(u)
        await db.flush()
    db.add(OrganizationMembership(org_id=org_id, user_id=u.id, role_key=role))
    return u


async def main(n: int) -> None:
    rng = random.Random(42)
    async with SessionLocal() as db:
        if await db.scalar(text("SELECT count(*) FROM organizations")):
            print("Database already seeded; skipping.")
            return
        await seed_reference_data(db)
        north = await provision_org(db, "Northwind Payments (Demo)")
        contoso = await provision_org(db, "Contoso Lending (Demo)")
        users = {r: await _user(db, f"{r.replace('_', '.')}@northwind.example", n_, north.id, r) for r, n_ in [
            ("org_admin", "Nadia Admin"), ("compliance_analyst", "Arjun Analyst"), ("reviewer", "Rosa Reviewer"), ("auditor", "Omar Auditor")]}
        await _user(db, "reviewer2@northwind.example", "Ken Reviewer", north.id, "reviewer")
        await _user(db, "platform@sentinelflow.example", "Sam Platform", north.id, "super_admin")
        await _user(db, "admin@contoso.example", "Clara Contoso", contoso.id, "org_admin")
        raw = new_token("sf_live_")
        key = ApiKey(org_id=north.id, name="Onboarding service (demo)", prefix=raw[:14], key_hash=sha256(raw),
                     scopes=sorted(API_KEY_GRANTABLE), created_by=users["org_admin"].id)
        db.add(key)
        # A little chaos so the seed contains retries, recoveries and provider errors.
        await db.execute(update(ProviderConfig).where(ProviderConfig.org_id == north.id)
                         .values(simulation={"latency_ms": 5, "failure_rate": 0.06, "timeout_rate": 0.02}, timeout_ms=300, max_retries=2))
        await db.commit()

    principal = Principal("api_key", north.id, frozenset(API_KEY_GRANTABLE), api_key_id=key.id, label="seed")
    payloads = [synthetic_business(i, rng) if rng.random() < 0.25 else synthetic_individual(i, rng) for i in range(n)]

    async def one(pl):
        async with SessionLocal() as db:
            vr, _ = await orchestrator.submit(db, principal, pl, None, "inline")
        await orchestrator.run_workflow(vr.id, "inline")

    for i in range(0, n, 25):
        await asyncio.gather(*[one(pl) for pl in payloads[i:i + 25]])
        print(f"  seeded {min(i + 25, n)}/{n}")

    async with SessionLocal() as db:
        now = datetime.now(UTC)
        ids = (await db.scalars(select(VerificationRequest.id).where(VerificationRequest.org_id == north.id))).all()
        for vid in ids:  # spread history over 14 days, weighted toward recent days
            ts = now - timedelta(days=min(rng.expovariate(0.18), 13.9), minutes=rng.randint(0, 600))
            await db.execute(text("UPDATE verification_requests SET created_at=:t, completed_at=:t + (latency_ms || ' ms')::interval WHERE id=:i"), {"t": ts, "i": vid})
            for tbl in ("provider_results", "risk_assessments", "workflow_executions"):
                col = "started_at" if tbl == "workflow_executions" else "created_at"
                await db.execute(text(f"UPDATE {tbl} SET {col}=:t WHERE verification_id=:i"), {"t": ts, "i": vid})
            await db.execute(text("UPDATE compliance_cases SET created_at=:t, sla_due_at=:t + interval '72 hours' WHERE verification_id=:i"), {"t": ts, "i": vid})
        await db.execute(update(ProviderConfig).where(ProviderConfig.org_id == north.id)
                         .values(simulation={"latency_ms": 40}, timeout_ms=800, max_retries=2))
        await db.commit()

        # Work some cases like a real team would.
        rev = Principal("user", north.id, role_permissions("compliance_analyst"),
                        user_id=users["compliance_analyst"].id, role="compliance_analyst", label="seed-analyst")
        open_cases = (await db.scalars(select(ComplianceCase).where(ComplianceCase.org_id == north.id).order_by(ComplianceCase.created_at))).all()
        for idx, c in enumerate(open_cases):
            if idx % 3 == 0:
                c.assignee_id, c.status = users["reviewer"].id, ("in_review" if c.status == "open" else c.status)
        await db.commit()
        for c in open_cases[: len(open_cases) // 3]:
            await case_service.decide(db, rev, c.id, rng.choice(["approve", "approve", "reject"]), "Reviewed supporting evidence (seed data).")
    print(f"Seeded {n} verifications. Demo password for all users: {DEMO_PASSWORD}")
    print(f"Demo API key (Northwind, shown once): {raw}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verifications", type=int, default=400)
    asyncio.run(main(ap.parse_args().verifications))
