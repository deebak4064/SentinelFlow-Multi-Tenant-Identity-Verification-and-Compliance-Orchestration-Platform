export type Decision = "approved" | "rejected" | "manual_review" | "escalated";
export type RiskCategory = "low" | "medium" | "high" | "critical";
export type Outcome = "pass" | "fail" | "review" | "error" | "skipped";

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }

export interface Me {
  user: { id: string; email: string; full_name: string; last_login_at: string | null };
  organization: { id: string; name: string; slug: string; status: string };
  role: string;
  role_name: string;
  permissions: string[];
  memberships: { organization_id: string; organization_name: string; role: string }[];
}

export interface VerificationSummary {
  id: string; customer_id: string; external_ref: string; verification_type: "individual" | "business";
  status: string; decision: Decision | null; automated_decision: Decision | null; decision_source: string | null;
  latency_ms: number | null; created_at: string; completed_at: string | null;
  customer_name?: string; country?: string; risk_score?: number; risk_category?: RiskCategory;
}

export interface Customer {
  id: string; external_ref: string; customer_type: string; full_name: string; email: string | null; dob: string | null;
  country: string; document_type: string | null; document_number: string | null; company_name: string | null;
  registration_number: string | null; status: string; latest_risk_score: number | null; latest_risk_category: RiskCategory | null;
  pii_masked: boolean; created_at: string; updated_at: string;
}

export interface ProviderResult {
  id: string; provider_key: string; check_type: string; outcome: Outcome; confidence: number; attempts: number;
  latency_ms: number; error_code: string | null; cache_hit: boolean;
  normalized: { reasons: string[]; signals: Record<string, unknown>; extracted: Record<string, unknown> };
  raw_response: unknown; created_at: string;
}

export interface RiskFactor { factor: string; weight: number; severity: number; points: number; explanation: string }
export interface RiskAssessment { id: string; verification_id: string; score: number; category: RiskCategory; breakdown: RiskFactor[]; model_version: string; created_at: string }

export interface ConditionTrace { field: string; op: string; expected: unknown; actual: unknown; matched: boolean }
export interface PolicyTraceItem {
  policy_id: string; policy_name: string; version: number; priority: number; action: string; matched: boolean; applied: boolean; reason: string;
  conditions: ConditionTrace[];
}
export interface PolicyTrace { action: string; decided_by: string; reason: string; flags: string[]; trace: PolicyTraceItem[]; context: Record<string, any> }

export interface WorkflowStep { name: string; status: string; offset_ms: number; duration_ms: number | null; attempts?: number; cache_hit?: boolean; error_code?: string | null; [k: string]: unknown }

export interface Case {
  id: string; verification_id: string; customer_id: string; status: string; priority: string; reason: string;
  assignee_id: string | null; sla_due_at: string | null; decision_notes: string | null; decided_by: string | null;
  decided_at: string | null; created_at: string; updated_at: string;
  customer_name?: string; customer_type?: string; risk_category?: RiskCategory; risk_score?: number; assignee_name?: string | null;
}

export interface VerificationDetail extends VerificationSummary {
  error: string | null; input_snapshot: Record<string, unknown>; customer: Customer; provider_results: ProviderResult[];
  risk_assessment: RiskAssessment | null; policy_trace: PolicyTrace | null;
  workflow: { id: string; status: string; execution_mode: string; steps: WorkflowStep[]; duration_ms: number | null } | null;
  case: Case | null;
}

export interface PolicyCondition { field: string; op: string; value?: unknown }
export interface PolicyGroup { all?: PolicyNode[]; any?: PolicyNode[] }
export type PolicyNode = PolicyCondition | PolicyGroup;
export interface PolicyDefinition { when: PolicyNode; action: string; reason: string }
export interface Policy {
  id: string; name: string; description: string; priority: number; is_active: boolean; current_version: number;
  definition: PolicyDefinition; created_at: string; updated_at: string;
  versions?: { version: number; definition: PolicyDefinition; change_note: string; created_at: string }[];
}

export interface AuditEvent {
  id: number; actor_type: string; actor_id: string | null; actor_label: string | null; action: string;
  resource_type: string | null; resource_id: string | null; ip: string | null; request_id: string | null; details: Record<string, unknown>; created_at: string;
}
