"""AI-assisted compliance analyst (advisory only).

Guarantees:
  * The model never decides. Its output schema has no decision field, decision language is
    stripped, and nothing it returns is read by the policy engine or case workflow.
  * Authorization happens before this module is called; it only sees evidence for one case the
    caller can already read.
  * Only redacted, structured evidence is sent (no names, DOB, document numbers, emails), and
    only when BOTH an API key is configured AND the organization opted in to external LLMs.
  * Output is validated against a strict schema; every claim must cite evidence IDs that exist.
    Invalid citations are dropped (hallucination guard). Any failure falls back to a
    deterministic analysis built from the same evidence.
"""

import json
import logging
import re
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import get_settings

log = logging.getLogger("sentinelflow.ai")
ADVISORY = "Advisory output. Decisions are made by deterministic policy and human reviewers, not by this assistant."


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SignalExplanation(_S):
    signal: str = Field(max_length=120)
    explanation: str = Field(max_length=600)
    evidence_refs: list[str] = Field(min_length=1, max_length=6)


class Inconsistency(_S):
    description: str = Field(max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=6)


class AnalystOutput(_S):
    summary: str = Field(max_length=1500)
    risk_signals: list[SignalExplanation] = Field(max_length=10)
    inconsistencies: list[Inconsistency] = Field(max_length=10)
    reviewer_questions: list[str] = Field(max_length=8)
    draft_report: str = Field(max_length=5000)
    confidence: float = Field(ge=0, le=1)


# --------------------------------------------------------------------------- evidence
def build_evidence(detail: dict) -> list[dict]:
    ev: list[dict] = []
    for r in detail["provider_results"]:
        n = r["normalized"]
        ev.append({"id": f"PR:{r['check_type']}", "type": "provider_result", "ref_id": str(r["id"]),
                   "summary": f"{r['check_type']} check: {r['outcome']} (confidence {r['confidence']:.2f}); {'; '.join(n.get('reasons', []))}",
                   "data": {"outcome": r["outcome"], "confidence": r["confidence"], "signals": n.get("signals", {}),
                            "attempts": r["attempts"], "error_code": r["error_code"]}})
    risk = detail.get("risk_assessment")
    if risk:
        ev.append({"id": "RISK:score", "type": "risk_assessment", "ref_id": str(risk["id"]),
                   "summary": f"Risk score {risk['score']} ({risk['category']})", "data": {"score": risk["score"], "category": risk["category"]}})
        for b in risk["breakdown"]:
            if b["points"] > 0:
                ev.append({"id": f"RISK:{b['factor']}", "type": "risk_factor", "ref_id": str(risk["id"]),
                           "summary": f"{b['factor']}: +{b['points']} points — {b['explanation']}", "data": b})
    trace = detail.get("policy_trace") or {}
    for i, t in enumerate(x for x in trace.get("trace", []) if x["matched"]):
        ev.append({"id": f"POLICY:{i + 1}", "type": "policy_match", "ref_id": t["policy_id"],
                   "summary": f"Policy '{t['policy_name']}' v{t['version']} matched → {t['action']}: {t['reason']}",
                   "data": {"action": t["action"], "applied": t["applied"]}})
    sig = (trace.get("context") or {}).get("signals", {})
    for i, s in enumerate(sig.get("inconsistencies", [])):
        ev.append({"id": f"SIG:inconsistency:{i + 1}", "type": "signal", "ref_id": str(detail["id"]), "summary": s, "data": {}})
    for f in sig.get("missing_fields", []):
        ev.append({"id": f"SIG:missing:{f}", "type": "signal", "ref_id": str(detail["id"]), "summary": f"Field '{f}' not provided", "data": {}})
    return ev


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_LONG_DIGITS = re.compile(r"\b[\w-]*\d{4,}[\w-]*\b")


def redact(text: str) -> str:
    """Defense in depth: evidence is already PII-free, but scrub anything that looks like an
    email or identifier before it leaves the system."""
    return _LONG_DIGITS.sub("[REDACTED_ID]", _EMAIL.sub("[REDACTED_EMAIL]", text))


# --------------------------------------------------------------------------- deterministic
_QUESTIONS = {
    "watchlist": "Can additional identifiers (full date of birth, nationality, address history) be obtained to rule the watchlist match in or out?",
    "identity": "Should the customer be asked to resubmit a clearer, unexpired identity document?",
    "business": "Can the company provide a recent certificate of good standing from the registry?",
    "inconsistency": "Is there a documented reason for the discrepancy (e.g. name change, transliteration, data-entry error)?",
    "error": "Should the failed provider check be re-run once the provider is available before a decision is made?",
    "missing": "Can the missing information be requested from the customer?",
}


def deterministic(detail: dict, evidence: list[dict]) -> AnalystOutput:
    ids = {e["id"]: e for e in evidence}
    risk = detail.get("risk_assessment") or {}
    decision = detail.get("decision") or detail.get("status")
    signals, questions, inconsistencies = [], [], []
    for e in evidence:
        if e["type"] == "risk_factor" and e["data"]["factor"] != "floor":
            signals.append(SignalExplanation(signal=e["data"]["factor"], explanation=e["summary"], evidence_refs=[e["id"]]))
        if e["type"] == "provider_result" and e["data"]["outcome"] in ("fail", "review", "error"):
            check = e["id"].split(":")[1]
            refs = [e["id"]] + ([f"RISK:{check}"] if f"RISK:{check}" in ids else [])
            signals.append(SignalExplanation(signal=f"{check}_{e['data']['outcome']}", explanation=e["summary"], evidence_refs=refs))
            questions.append(_QUESTIONS["error" if e["data"]["outcome"] == "error" else check])
        if e["id"].startswith("SIG:inconsistency"):
            inconsistencies.append(Inconsistency(description=e["summary"], evidence_refs=[e["id"]]))
            questions.append(_QUESTIONS["inconsistency"])
        if e["id"].startswith("SIG:missing"):
            questions.append(_QUESTIONS["missing"])
    questions = list(dict.fromkeys(questions))[:8]
    executed = [e for e in evidence if e["type"] == "provider_result"]
    complete = executed and all(e["data"]["outcome"] != "error" for e in executed)
    summary = (f"{detail['verification_type'].title()} verification for a subject in {detail.get('country', 'an unknown country')}. "
               f"Automated outcome: {decision}. Risk score {risk.get('score', 'n/a')} ({risk.get('category', 'n/a')}). "
               f"{len([s for s in signals if s.signal.endswith(('_fail', '_review', '_error'))])} provider checks need attention; "
               f"{len(inconsistencies)} data inconsistencies detected.")
    lines = ["# Draft review report", "", "## Summary", summary, "", "## Evidence"]
    lines += [f"- [{e['id']}] {e['summary']}" for e in evidence]
    lines += ["", "## Open questions for the reviewer"] + [f"- {q}" for q in questions] + ["", f"_{ADVISORY}_"]
    return AnalystOutput(summary=summary, risk_signals=signals[:10], inconsistencies=inconsistencies[:10],
                         reviewer_questions=questions, draft_report="\n".join(lines)[:5000], confidence=0.9 if complete else 0.6)


# --------------------------------------------------------------------------- LLM
_DECISION_LANG = re.compile(r"\b(approve[sd]?|approval|reject(s|ed|ion)?|onboard the customer)\b", re.I)

SYSTEM_PROMPT = (
    "You are assisting a compliance reviewer. You must not recommend approving or rejecting the customer. "
    "Use ONLY the evidence provided; every risk signal and inconsistency must cite evidence ids from the list. "
    "Respond with a single JSON object matching this schema and nothing else: "
    + json.dumps(AnalystOutput.model_json_schema())
)


def _guard(out: AnalystOutput, evidence_ids: set[str], warnings: list[str]) -> AnalystOutput:
    def keep(items):
        kept = []
        for it in items:
            refs = [r for r in it.evidence_refs if r in evidence_ids]
            if refs:
                it.evidence_refs = refs
                kept.append(it)
            else:
                warnings.append(f"Dropped uncited claim: {getattr(it, 'signal', None) or it.description[:60]}")
        return kept

    out.risk_signals, out.inconsistencies = keep(out.risk_signals), keep(out.inconsistencies)
    for f in ("summary", "draft_report"):
        val = getattr(out, f)
        if _DECISION_LANG.search(val):
            warnings.append(f"Decision language removed from {f}")
            setattr(out, f, _DECISION_LANG.sub("[decision language removed]", val))
    return out


async def call_llm(evidence: list[dict], detail: dict, client: httpx.AsyncClient | None = None) -> AnalystOutput:
    s = get_settings()
    prompt = redact(json.dumps({"verification_type": detail["verification_type"], "country": detail.get("country"),
                                "automated_outcome": detail.get("decision"), "evidence": evidence}, default=str))
    body = {"model": s.llm_model, "max_tokens": 2000, "system": SYSTEM_PROMPT, "messages": [{"role": "user", "content": prompt}]}
    headers = {"x-api-key": s.llm_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    async with (client or httpx.AsyncClient(timeout=s.llm_timeout_seconds)) as c:
        r = await c.post(f"{s.llm_base_url}/v1/messages", json=body, headers=headers)
        r.raise_for_status()
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
    text = re.sub(r"^```(json)?|```$", "", text).strip()
    return AnalystOutput.model_validate_json(text)


async def analyze(detail: dict, org_settings: dict, client: httpx.AsyncClient | None = None) -> dict:
    s = get_settings()
    evidence = build_evidence(detail)
    warnings: list[str] = []
    source, model = "deterministic", None
    use_llm = bool(s.llm_api_key) and org_settings.get("ai_external_llm_allowed", False)
    out = None
    if use_llm:
        try:
            out = _guard(await call_llm(evidence, detail, client), {e["id"] for e in evidence}, warnings)
            source, model = "llm", s.llm_model
        except (httpx.HTTPError, ValidationError, ValueError, KeyError) as e:
            log.warning("LLM analysis failed: %s", type(e).__name__)
            warnings.append(f"LLM unavailable or returned invalid output ({type(e).__name__}); deterministic fallback used")
    elif s.llm_api_key:
        warnings.append("External LLM disabled for this organization; deterministic analysis used")
    if out is None:
        out = deterministic(detail, evidence)
    return {"analysis": out.model_dump(), "source": source, "model": model, "redaction_applied": True,
            "evidence": [{k: e[k] for k in ("id", "type", "ref_id", "summary")} for e in evidence],
            "warnings": warnings, "advisory_notice": ADVISORY, "generated_at": datetime.now(UTC)}
