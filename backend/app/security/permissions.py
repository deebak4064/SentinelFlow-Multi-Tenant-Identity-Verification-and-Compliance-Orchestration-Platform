"""Centralized authorization model.

Every route declares the permission it needs via `require(...)` (see api/deps.py). Roles map to
permission sets here; the same table is seeded into the `roles`/`permissions`/`role_permissions`
tables so the matrix is visible and queryable, and this module is the single source of truth.
"""

from dataclasses import dataclass, field
from uuid import UUID

PERMISSIONS: dict[str, str] = {
    "verifications:create": "Submit verification requests",
    "verifications:read": "View verification requests and results",
    "verifications:review": "Record manual review decisions",
    "customers:read": "View customer profiles (masked)",
    "customers:read_pii": "Reveal unmasked customer PII (audited)",
    "cases:read": "View compliance cases",
    "cases:write": "Comment on and update cases",
    "cases:assign": "Assign cases to reviewers",
    "cases:decide": "Approve or reject assigned cases",
    "cases:decide_any": "Decide escalated or unassigned cases",
    "policies:read": "View policies",
    "policies:write": "Create, version, activate policies",
    "providers:read": "View provider integrations",
    "providers:write": "Configure and test provider integrations",
    "users:read": "View organization members",
    "users:write": "Invite members and change roles",
    "api_keys:manage": "Create, rotate and revoke API keys",
    "audit:read": "Read the audit log",
    "settings:write": "Change organization settings",
    "ai:use": "Use the AI-assisted analyst",
    "platform:manage": "Manage organizations across the platform (no tenant data access)",
}

_ALL_TENANT = {p for p in PERMISSIONS if p != "platform:manage"}

ROLES: dict[str, dict] = {
    "super_admin": {
        "name": "Super Admin",
        "rank": 100,
        "description": "Platform operator. Manages organizations; full rights inside orgs they belong to.",
        "permissions": _ALL_TENANT | {"platform:manage"},
    },
    "org_admin": {
        "name": "Organization Admin",
        "rank": 80,
        "description": "Full control of a single organization.",
        "permissions": set(_ALL_TENANT),
    },
    "compliance_analyst": {
        "name": "Compliance Analyst",
        "rank": 60,
        "description": "Runs verifications, tunes policies, triages and decides escalated cases.",
        "permissions": {
            "verifications:create", "verifications:read", "verifications:review",
            "customers:read", "customers:read_pii",
            "cases:read", "cases:write", "cases:assign", "cases:decide", "cases:decide_any",
            "policies:read", "policies:write", "providers:read", "users:read", "ai:use",
        },
    },
    "reviewer": {
        "name": "Reviewer",
        "rank": 40,
        "description": "Works cases assigned to them.",
        "permissions": {
            "verifications:read", "verifications:review", "customers:read", "customers:read_pii",
            "cases:read", "cases:write", "cases:decide", "policies:read", "ai:use",
        },
    },
    "auditor": {
        "name": "Read-only Auditor",
        "rank": 20,
        "description": "Read-only access with masked PII and full audit log visibility.",
        "permissions": {
            "verifications:read", "customers:read", "cases:read", "policies:read",
            "providers:read", "users:read", "audit:read",
        },
    },
}


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" | "api_key"
    org_id: UUID
    permissions: frozenset[str]
    user_id: UUID | None = None
    api_key_id: UUID | None = None
    role: str | None = None
    label: str = ""
    session_id: UUID | None = None
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def actor_id(self) -> UUID | None:
        return self.user_id or self.api_key_id

    def can(self, perm: str) -> bool:
        return perm in self.permissions


def role_permissions(role_key: str) -> frozenset[str]:
    return frozenset(ROLES[role_key]["permissions"]) if role_key in ROLES else frozenset()


def can_assign_role(actor_role: str, target_role: str) -> bool:
    """No one can grant a role more privileged than their own; only super admins grant super admin."""
    if target_role not in ROLES or actor_role not in ROLES:
        return False
    if target_role == "super_admin":
        return actor_role == "super_admin"
    return ROLES[target_role]["rank"] <= ROLES[actor_role]["rank"]


# Permissions an API key may carry. Keys are machine credentials: no user/key/settings management.
API_KEY_GRANTABLE = {
    "verifications:create", "verifications:read", "customers:read", "cases:read", "policies:read", "audit:read",
}
