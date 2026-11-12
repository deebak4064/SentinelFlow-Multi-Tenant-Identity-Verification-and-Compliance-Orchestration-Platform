"""Reference data (roles/permissions) and per-organization provisioning."""

import re
import secrets

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.providers import REGISTRY
from app.models import (
    Organization,
    Permission,
    Policy,
    PolicyVersion,
    ProviderConfig,
    Role,
    RolePermission,
)
from app.security.permissions import PERMISSIONS, ROLES

_UNCERTAIN = ["review", "error"]

DEFAULT_POLICIES = [
    {
        "name": "Reject missing mandatory fields",
        "priority": 10,
        "description": "Requests lacking fields required for the verification type are rejected outright.",
        "definition": {"when": {"field": "signals.missing_mandatory_count", "op": "gt", "value": 0},
                       "action": "reject", "reason": "Mandatory verification fields are missing"},
    },
    {
        "name": "Block restricted jurisdictions",
        "priority": 20,
        "description": "Example organization policy: jurisdictions this organization does not onboard (synthetic config).",
        "definition": {"when": {"field": "customer.country", "op": "in", "value": ["XX", "ZZ"]},
                       "action": "reject", "reason": "Customer jurisdiction is restricted by organization policy"},
    },
    {
        "name": "Escalate strong watchlist match",
        "priority": 30,
        "description": "Strong screening hits always go to a senior analyst.",
        "definition": {"when": {"all": [{"field": "providers.watchlist.outcome", "op": "eq", "value": "fail"},
                                        {"field": "providers.watchlist.confidence", "op": "gte", "value": 0.9}]},
                       "action": "escalate", "reason": "Strong watchlist match requires senior review"},
    },
    {
        "name": "Escalate critical risk",
        "priority": 40,
        "description": "Any critical risk score is escalated.",
        "definition": {"when": {"field": "risk.score", "op": "gte", "value": 75},
                       "action": "escalate", "reason": "Critical risk score"},
    },
    {
        "name": "Reject failed document authenticity",
        "priority": 50,
        "description": "Documents that fail authenticity checks are rejected.",
        "definition": {"when": {"field": "providers.identity.signals.document_authentic", "op": "eq", "value": False},
                       "action": "reject", "reason": "Identity document failed authenticity checks"},
    },
    {
        "name": "Manual review for uncertain or conflicting results",
        "priority": 60,
        "description": "Ambiguous, failed or conflicting provider responses require a human.",
        "definition": {"when": {"any": [
            {"field": "providers.identity.outcome", "op": "in", "value": _UNCERTAIN},
            {"field": "providers.business.outcome", "op": "in", "value": _UNCERTAIN},
            {"field": "providers.watchlist.outcome", "op": "in", "value": _UNCERTAIN},
            {"field": "signals.conflicting_results", "op": "eq", "value": True},
        ]}, "action": "manual_review", "reason": "Uncertain or conflicting provider responses"},
    },
    {
        "name": "Review mismatched identity information",
        "priority": 70,
        "description": "Submitted data that disagrees with document or registry data is reviewed.",
        "definition": {"when": {"field": "signals.inconsistency_count", "op": "gt", "value": 0},
                       "action": "manual_review", "reason": "Submitted information is inconsistent with verified data"},
    },
    {
        "name": "Flag missing contact email",
        "priority": 90,
        "description": "Informational flag; does not change the decision.",
        "definition": {"when": {"field": "customer.has_email", "op": "eq", "value": False},
                       "action": "flag", "reason": "No contact email on file"},
    },
]

DEFAULT_SETTINGS = {"ai_enabled": True, "ai_external_llm_allowed": False, "default_execution_mode": "inline"}


async def seed_reference_data(db: AsyncSession) -> None:
    for key, desc in PERMISSIONS.items():
        await db.execute(pg_insert(Permission).values(key=key, description=desc).on_conflict_do_update(index_elements=["key"], set_={"description": desc}))
    for key, r in ROLES.items():
        vals = dict(name=r["name"], description=r["description"], rank=r["rank"])
        await db.execute(pg_insert(Role).values(key=key, **vals).on_conflict_do_update(index_elements=["key"], set_=vals))
    await db.execute(RolePermission.__table__.delete())
    for key, r in ROLES.items():
        for perm in r["permissions"]:
            db.add(RolePermission(role_key=key, permission_key=perm))
    await db.flush()


def slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{base}-{secrets.token_hex(3)}"


async def provision_org(db: AsyncSession, name: str, created_by=None) -> Organization:
    org = Organization(name=name, slug=slugify(name), settings=dict(DEFAULT_SETTINGS))
    db.add(org)
    await db.flush()
    for key in REGISTRY:
        db.add(ProviderConfig(org_id=org.id, provider_key=key, enabled=True, timeout_ms=800, max_retries=2, simulation={"latency_ms": 40}))
    for p in DEFAULT_POLICIES:
        pol = Policy(org_id=org.id, name=p["name"], description=p["description"], priority=p["priority"], is_active=True,
                     current_version=1, created_by=created_by)
        db.add(pol)
        await db.flush()
        db.add(PolicyVersion(org_id=org.id, policy_id=pol.id, version=1, definition=p["definition"],
                             change_note="Initial default policy", created_by=created_by))
    await db.flush()
    return org


async def roles_exist(db: AsyncSession) -> bool:
    return (await db.scalar(select(Role.key).limit(1))) is not None
