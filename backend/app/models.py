"""ORM models.

Tenant ownership is enforced in the database, not just in application code: every tenant-owned
child table carries `org_id` and references its parent through a composite (org_id, id) foreign
key, so a row can never point at a parent that belongs to another organization.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Timestamps:
    # Fetch server-generated timestamps via RETURNING so they are never lazily loaded after commit
    # (lazy IO is not possible under asyncio).
    __mapper_args__ = {"eager_defaults": True}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)


# --------------------------------------------------------------------------- identity & tenancy
class Organization(Timestamps, Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class User(Timestamps, Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Role(Base):
    __tablename__ = "roles"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    rank: Mapped[int] = mapped_column(Integer)  # higher rank = more privileged; used for escalation checks


class Permission(Base):
    __tablename__ = "permissions"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    description: Mapped[str] = mapped_column(Text, default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_key: Mapped[str] = mapped_column(ForeignKey("roles.key", ondelete="CASCADE"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(ForeignKey("permissions.key", ondelete="CASCADE"), primary_key=True)


class OrganizationMembership(Timestamps, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_membership_org_user"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_key: Mapped[str] = mapped_column(ForeignKey("roles.key"))


class UserSession(Base):
    __tablename__ = "user_sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[uuid.UUID] = org_fk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(20))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    rotated_from_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- customers & verifications
class Customer(Timestamps, Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_customers_org_id"),
        UniqueConstraint("org_id", "external_ref", name="uq_customers_org_ref"),
        Index("ix_customers_org_created", "org_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    external_ref: Mapped[str] = mapped_column(String(120))
    customer_type: Mapped[str] = mapped_column(String(20))  # individual | business
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    country: Mapped[str] = mapped_column(String(2))
    # PII encrypted at rest with AES-GCM (see security/crypto.py). Only last4 kept in clear for display.
    dob_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    document_type: Mapped[str | None] = mapped_column(String(30))
    document_number_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    document_last4: Mapped[str | None] = mapped_column(String(4))
    company_name: Mapped[str | None] = mapped_column(String(200))
    registration_number: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|verified|rejected|in_review
    latest_risk_score: Mapped[float | None] = mapped_column(Float)
    latest_risk_category: Mapped[str | None] = mapped_column(String(20))


class VerificationRequest(Timestamps, Base):
    __tablename__ = "verification_requests"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_verifications_org_id"),
        ForeignKeyConstraint(["org_id", "customer_id"], ["customers.org_id", "customers.id"], ondelete="CASCADE"),
        Index("ix_verifications_org_created", "org_id", "created_at"),
        Index("ix_verifications_org_status", "org_id", "status"),
        Index("ix_verifications_org_decision", "org_id", "decision"),
        Index("ix_verifications_customer", "customer_id", "created_at"),
        Index(
            "uq_verifications_idempotency",
            "org_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    verification_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|completed|failed
    decision: Mapped[str | None] = mapped_column(String(20))  # approved|rejected|manual_review|escalated
    automated_decision: Mapped[str | None] = mapped_column(String(20))
    decision_source: Mapped[str | None] = mapped_column(String(20))  # policy|manual
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    idempotency_key: Mapped[str | None] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)  # non-PII request metadata only
    policy_trace: Mapped[dict | None] = mapped_column(JSONB)
    submitted_by_type: Mapped[str] = mapped_column(String(20))
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProviderResult(Base):
    __tablename__ = "provider_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "verification_id"], ["verification_requests.org_id", "verification_requests.id"], ondelete="CASCADE"
        ),
        Index("ix_provider_results_verification", "verification_id"),
        Index("ix_provider_results_org_provider_created", "org_id", "provider_key", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    verification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    provider_key: Mapped[str] = mapped_column(String(40))
    check_type: Mapped[str] = mapped_column(String(30))
    outcome: Mapped[str] = mapped_column(String(20))  # pass|fail|review|error
    confidence: Mapped[float] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[float] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(40))
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    normalized: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "verification_id"], ["verification_requests.org_id", "verification_requests.id"], ondelete="CASCADE"
        ),
        UniqueConstraint("verification_id", name="uq_risk_assessment_verification"),
        Index("ix_risk_org_category", "org_id", "category"),
        Index("ix_risk_org_score", "org_id", "score"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    verification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    score: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(20))
    breakdown: Mapped[list] = mapped_column(JSONB)
    weights: Mapped[dict] = mapped_column(JSONB)
    model_version: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "verification_id"], ["verification_requests.org_id", "verification_requests.id"], ondelete="CASCADE"
        ),
        Index("ix_workflow_org_started", "org_id", "started_at"),
        Index("ix_workflow_verification", "verification_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    verification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20))  # running|completed|failed
    execution_mode: Mapped[str] = mapped_column(String(10))
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)


class ProviderConfig(Base):
    __tablename__ = "provider_configs"
    __table_args__ = (UniqueConstraint("org_id", "provider_key", name="uq_provider_config"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    provider_key: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=800)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    # Simulation knobs: {"latency_ms": 40, "failure_rate": 0.0, "timeout_rate": 0.0}
    simulation: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# --------------------------------------------------------------------------- policies
class Policy(Timestamps, Base):
    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_policies_org_id"),
        UniqueConstraint("org_id", "name", name="uq_policies_org_name"),
        Index("ix_policies_org_active_priority", "org_id", "is_active", "priority"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "policy_id"], ["policies.org_id", "policies.id"], ondelete="CASCADE"),
        UniqueConstraint("policy_id", "version", name="uq_policy_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    policy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict] = mapped_column(JSONB)
    change_note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- case management
class ComplianceCase(Timestamps, Base):
    __tablename__ = "compliance_cases"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_cases_org_id"),
        UniqueConstraint("verification_id", name="uq_cases_verification"),
        ForeignKeyConstraint(
            ["org_id", "verification_id"], ["verification_requests.org_id", "verification_requests.id"], ondelete="CASCADE"
        ),
        ForeignKeyConstraint(["org_id", "customer_id"], ["customers.org_id", "customers.id"], ondelete="CASCADE"),
        # Assignee must be a member of the SAME organization. PG15+ column-list SET NULL keeps org_id intact.
        ForeignKeyConstraint(
            ["org_id", "assignee_id"],
            ["organization_memberships.org_id", "organization_memberships.user_id"],
            ondelete="SET NULL (assignee_id)",
            name="fk_cases_assignee_membership",
        ),
        Index("ix_cases_org_status", "org_id", "status", "created_at"),
        Index("ix_cases_org_assignee", "org_id", "assignee_id"),
        Index("ix_cases_org_sla", "org_id", "sla_due_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    verification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|in_review|escalated|approved|rejected
    priority: Mapped[str] = mapped_column(String(10), default="normal")  # low|normal|high|critical
    reason: Mapped[str] = mapped_column(Text)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_notes: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CaseComment(Base):
    __tablename__ = "case_comments"
    __table_args__ = (
        ForeignKeyConstraint(["org_id", "case_id"], ["compliance_cases.org_id", "compliance_cases.id"], ondelete="CASCADE"),
        Index("ix_case_comments_case", "case_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- audit
class AuditEvent(Base):
    """Append-only. UPDATE/DELETE are rejected by a database trigger (see migration)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_org_created", "org_id", "created_at"),
        Index("ix_audit_org_resource", "org_id", "resource_type", "resource_id"),
        Index("ix_audit_org_actor", "org_id", "actor_id"),
        Index("ix_audit_org_action", "org_id", "action"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"))
    actor_type: Mapped[str] = mapped_column(String(20))  # user|api_key|system
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str | None] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(80))
    resource_type: Mapped[str | None] = mapped_column(String(40))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
