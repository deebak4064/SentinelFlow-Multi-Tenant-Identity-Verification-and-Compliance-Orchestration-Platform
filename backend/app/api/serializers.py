"""Response shaping. PII is masked here unless the caller explicitly asked to reveal and is allowed to."""

from app.models import (
    ComplianceCase,
    Customer,
    ProviderResult,
    RiskAssessment,
    VerificationRequest,
    WorkflowExecution,
)
from app.security.crypto import (
    decrypt_field,
    mask_dob,
    mask_document,
    mask_email,
    mask_name,
)


def customer(c: Customer, reveal: bool = False) -> dict:
    dob = decrypt_field(c.dob_enc)
    personal = c.customer_type == "individual"
    return {
        "id": c.id,
        "external_ref": c.external_ref,
        "customer_type": c.customer_type,
        "full_name": c.full_name if (reveal or not personal) else mask_name(c.full_name),
        "email": c.email if reveal else mask_email(c.email),
        "dob": dob if reveal else mask_dob(dob),
        "country": c.country,
        "document_type": c.document_type,
        "document_number": decrypt_field(c.document_number_enc) if reveal else mask_document(c.document_last4),
        "company_name": c.company_name,
        "registration_number": c.registration_number,
        "status": c.status,
        "latest_risk_score": c.latest_risk_score,
        "latest_risk_category": c.latest_risk_category,
        "pii_masked": not reveal,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def verification_summary(v: VerificationRequest, c: Customer | None = None, risk: RiskAssessment | None = None) -> dict:
    out = {
        "id": v.id,
        "customer_id": v.customer_id,
        "external_ref": v.input_snapshot.get("external_ref"),
        "verification_type": v.verification_type,
        "status": v.status,
        "decision": v.decision,
        "automated_decision": v.automated_decision,
        "decision_source": v.decision_source,
        "latency_ms": v.latency_ms,
        "created_at": v.created_at,
        "completed_at": v.completed_at,
    }
    if c is not None:
        out["customer_name"] = c.full_name if c.customer_type == "business" else mask_name(c.full_name)
        out["country"] = c.country
    if risk is not None:
        out["risk_score"], out["risk_category"] = risk.score, risk.category
    return out


def provider_result(r: ProviderResult) -> dict:
    return {
        "id": r.id, "provider_key": r.provider_key, "check_type": r.check_type, "outcome": r.outcome,
        "confidence": r.confidence, "attempts": r.attempts, "latency_ms": r.latency_ms, "error_code": r.error_code,
        "cache_hit": r.cache_hit, "normalized": r.normalized, "raw_response": r.raw_response, "created_at": r.created_at,
    }


def risk(r: RiskAssessment) -> dict:
    return {"id": r.id, "verification_id": r.verification_id, "score": r.score, "category": r.category,
            "breakdown": r.breakdown, "weights": r.weights, "model_version": r.model_version, "created_at": r.created_at}


def workflow(w: WorkflowExecution) -> dict:
    return {"id": w.id, "status": w.status, "execution_mode": w.execution_mode, "steps": w.steps,
            "started_at": w.started_at, "finished_at": w.finished_at, "duration_ms": w.duration_ms, "error": w.error}


def case(c: ComplianceCase) -> dict:
    return {
        "id": c.id, "verification_id": c.verification_id, "customer_id": c.customer_id, "status": c.status,
        "priority": c.priority, "reason": c.reason, "assignee_id": c.assignee_id, "sla_due_at": c.sla_due_at,
        "decision_notes": c.decision_notes, "decided_by": c.decided_by, "decided_at": c.decided_at,
        "created_at": c.created_at, "updated_at": c.updated_at,
    }
