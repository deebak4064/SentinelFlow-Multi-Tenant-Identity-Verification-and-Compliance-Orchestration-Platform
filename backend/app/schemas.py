"""Request/response schemas. Every inbound model forbids unknown fields."""

import re
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.engine.policy import PolicyDefinition

T = TypeVar("T")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------- auth
class RegisterIn(Strict):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=12, max_length=128)
    organization_name: str = Field(min_length=2, max_length=200)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("password must contain letters and digits")
        return v


class LoginIn(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    organization_id: UUID | None = None


class SwitchOrgIn(Strict):
    organization_id: UUID


# --------------------------------------------------------------------------- verification
ISO2 = Field(pattern=r"^[A-Z]{2}$")


class SubjectIn(Strict):
    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    dob: date | None = None
    country: str = ISO2
    document_type: Literal["passport", "national_id", "drivers_license"] | None = None
    document_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{4,30}$")
    company_name: str | None = Field(default=None, max_length=200)
    registration_number: str | None = Field(default=None, pattern=r"^[A-Za-z0-9-]{2,40}$")

    @field_validator("dob")
    @classmethod
    def _dob(cls, v: date | None):
        if v and not (date(1900, 1, 1) <= v <= date.today()):
            raise ValueError("dob out of range")
        return v


class VerificationIn(Strict):
    external_ref: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    verification_type: Literal["individual", "business"]
    subject: SubjectIn
    execution_mode: Literal["inline", "queue"] | None = None

    @model_validator(mode="after")
    def _identity_present(self):
        s = self.subject
        if self.verification_type == "individual" and not s.full_name:
            raise ValueError("subject.full_name is required for individual verification")
        if self.verification_type == "business" and not s.company_name:
            raise ValueError("subject.company_name is required for business verification")
        return self


class ReviewIn(Strict):
    decision: Literal["approve", "reject"]
    notes: str = Field(min_length=3, max_length=4000)


# --------------------------------------------------------------------------- policies
class PolicyIn(Strict):
    name: str = Field(min_length=3, max_length=120)
    description: str = Field(default="", max_length=1000)
    priority: int = Field(default=100, ge=0, le=10000)
    is_active: bool = True
    definition: PolicyDefinition


class PolicyUpdateIn(Strict):
    description: str | None = Field(default=None, max_length=1000)
    priority: int | None = Field(default=None, ge=0, le=10000)
    definition: PolicyDefinition | None = None
    change_note: str = Field(default="", max_length=500)


class PolicySimulateIn(Strict):
    definition: PolicyDefinition | None = None
    verification_id: UUID | None = None
    context: dict[str, Any] | None = None


# --------------------------------------------------------------------------- cases
class CaseAssignIn(Strict):
    assignee_id: UUID | None


class CaseCommentIn(Strict):
    body: str = Field(min_length=1, max_length=4000)
    evidence_refs: list[dict[str, str]] = Field(default_factory=list, max_length=20)


class CaseEscalateIn(Strict):
    reason: str = Field(min_length=3, max_length=1000)


# --------------------------------------------------------------------------- admin
class ApiKeyIn(Strict):
    name: str = Field(min_length=2, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=20)
    expires_in_days: int | None = Field(default=None, ge=1, le=730)


class MemberIn(Strict):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    role: str
    temporary_password: str = Field(min_length=12, max_length=128)


class RoleChangeIn(Strict):
    role: str


class ProviderConfigIn(Strict):
    enabled: bool | None = None
    timeout_ms: int | None = Field(default=None, ge=50, le=10000)
    max_retries: int | None = Field(default=None, ge=0, le=5)
    simulation: dict[str, float] | None = None

    @field_validator("simulation")
    @classmethod
    def _sim(cls, v):
        if v is None:
            return v
        allowed = {"latency_ms": (0, 5000), "failure_rate": (0, 1), "timeout_rate": (0, 1)}
        for k, val in v.items():
            if k not in allowed or not (allowed[k][0] <= val <= allowed[k][1]):
                raise ValueError(f"invalid simulation setting {k}={val}")
        return v


class SettingsIn(Strict):
    risk_weights: dict[str, float] | None = None
    risk_thresholds: dict[str, float] | None = None
    ai_enabled: bool | None = None
    ai_external_llm_allowed: bool | None = None
    default_execution_mode: Literal["inline", "queue"] | None = None

    @field_validator("risk_weights")
    @classmethod
    def _weights(cls, v):
        from app.engine.risk import DEFAULT_WEIGHTS

        if v is not None:
            for k, val in v.items():
                if k not in DEFAULT_WEIGHTS or not (0 <= val <= 100):
                    raise ValueError(f"invalid weight {k}")
        return v

    @field_validator("risk_thresholds")
    @classmethod
    def _thresholds(cls, v):
        if v is not None:
            if set(v) != {"medium", "high", "critical"} or not (0 < v["medium"] < v["high"] < v["critical"] <= 100):
                raise ValueError("thresholds must be 0 < medium < high < critical <= 100")
        return v


class OrgStatusIn(Strict):
    status: Literal["active", "suspended"]


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AuditOut(Out):
    id: int
    actor_type: str
    actor_id: UUID | None
    actor_label: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    ip: str | None
    request_id: str | None
    details: dict
    created_at: datetime
