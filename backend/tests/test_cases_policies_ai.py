import json
import subprocess
import sys

import httpx
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import VerificationRequest
from app.services import ai_analyst
from tests.conftest import BACKEND


# --------------------------------------------------------------------------- case management
async def test_case_lifecycle_with_comments_evidence_and_history(org):
    rev = await org.add_member("reviewer")
    v = (await org.verify(doc="P1235555")).json()
    case, url = v["case"], f"/api/v1/cases/{v['case']['id']}"
    pr_id = v["provider_results"][0]["id"]
    r = await org.client.post(f"{url}/assign", headers=org.h(), json={"assignee_id": org.user_ids[rev]})
    assert r.json()["status"] == "in_review"
    r = await org.client.post(f"{url}/comments", headers=org.h(rev), json={"body": "Image blurry", "evidence_refs": [{"type": "provider_result", "id": pr_id}]})
    assert r.status_code == 201
    bad = await org.client.post(f"{url}/comments", headers=org.h(rev), json={"body": "x", "evidence_refs": [{"type": "provider_result", "id": case["id"]}]})
    assert bad.status_code == 422
    r = await org.client.post(f"{url}/escalate", headers=org.h(rev), json={"reason": "Needs senior eyes"})
    assert r.json()["status"] == "escalated" and r.json()["assignee_id"] is None
    detail = (await org.client.get(url, headers=org.h())).json()
    assert [h["action"] for h in detail["history"]] == ["case.created", "case.assigned", "case.commented", "case.escalated"]
    assert detail["can_decide"] is True and len(detail["comments"]) == 2
    await org.client.post(f"{url}/decision", headers=org.h(), json={"decision": "reject", "notes": "Could not verify"})
    cust = (await org.client.get(f"/api/v1/customers/{v['customer_id']}", headers=org.h())).json()
    assert cust["status"] == "rejected"
    assert (await org.client.post(f"{url}/assign", headers=org.h(), json={"assignee_id": None})).status_code == 409


async def test_case_filters(org):
    await org.verify(doc="P1235555")
    await org.verify(name="Viktor Sanctionov")
    assert (await org.client.get("/api/v1/cases?status=escalated", headers=org.h())).json()["total"] == 1
    assert (await org.client.get("/api/v1/cases?open_only=true&assigned=unassigned", headers=org.h())).json()["total"] == 2
    assert (await org.client.get("/api/v1/cases?assigned=me", headers=org.h())).json()["total"] == 0


# --------------------------------------------------------------------------- policies API
DEF = {"when": {"field": "customer.country", "op": "eq", "value": "FR"}, "action": "reject", "reason": "No FR onboarding"}


async def test_policy_versioning_activation_and_effect(org):
    r = await org.client.post("/api/v1/policies", headers=org.h(), json={"name": "No France", "priority": 5, "definition": DEF})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert (await org.verify(subject={"country": "FR"})).json()["decision"] == "rejected"
    r = await org.client.patch(f"/api/v1/policies/{pid}", headers=org.h(),
                               json={"definition": {**DEF, "action": "manual_review"}, "change_note": "soften"})
    assert r.json()["current_version"] == 2
    assert (await org.verify(subject={"country": "FR"})).json()["decision"] == "manual_review"
    await org.client.post(f"/api/v1/policies/{pid}/deactivate", headers=org.h())
    assert (await org.verify(subject={"country": "FR"})).json()["decision"] == "approved"
    await org.client.post(f"/api/v1/policies/{pid}/activate", headers=org.h())
    r = await org.client.post(f"/api/v1/policies/{pid}/versions/1/restore", headers=org.h())
    assert r.json()["current_version"] == 3 and r.json()["definition"]["action"] == "reject"
    detail = (await org.client.get(f"/api/v1/policies/{pid}", headers=org.h())).json()
    assert [v["version"] for v in detail["versions"]] == [3, 2, 1]
    # decision trace records the exact version used
    v = (await org.verify(subject={"country": "FR"})).json()
    applied = next(t for t in v["policy_trace"]["trace"] if t["applied"])
    assert applied["policy_name"] == "No France" and applied["version"] == 3


async def test_policy_validation_and_duplicates(org):
    assert (await org.client.post("/api/v1/policies", headers=org.h(), json={"name": "Bad", "definition": {**DEF, "action": "nuke"}})).status_code == 422
    await org.client.post("/api/v1/policies", headers=org.h(), json={"name": "Dup policy", "definition": DEF})
    assert (await org.client.post("/api/v1/policies", headers=org.h(), json={"name": "Dup policy", "definition": DEF})).status_code == 409


async def test_policy_simulation(org):
    v = (await org.verify()).json()
    r = (await org.client.post("/api/v1/policies/simulate", headers=org.h(), json={"verification_id": v["id"], "definition": {
        "when": {"field": "risk.score", "op": "gte", "value": 0}, "action": "escalate", "reason": "simulate"}})).json()
    assert r["action"] == "escalate" and r["trace"][0]["matched"]
    r = (await org.client.post("/api/v1/policies/simulate", headers=org.h(), json={"verification_id": v["id"]})).json()
    assert r["action"] == "approve"  # replaying the active set reproduces the stored decision


# --------------------------------------------------------------------------- AI analyst
async def test_ai_deterministic_fallback_without_key(org):
    case = (await org.verify(doc="P1231111")).json()["case"]
    r = await org.client.post(f"/api/v1/cases/{case['id']}/ai-analysis", headers=org.h())
    b = r.json()
    assert r.status_code == 200 and b["source"] == "deterministic" and b["redaction_applied"]
    ids = {e["id"] for e in b["evidence"]}
    assert all(set(s["evidence_refs"]) <= ids for s in b["analysis"]["risk_signals"] + b["analysis"]["inconsistencies"])
    assert b["analysis"]["inconsistencies"] and b["analysis"]["reviewer_questions"]
    assert "decision" not in b["analysis"] and "Amara" not in json.dumps(b)


async def test_ai_requires_permission(org):
    await org.add_member("auditor")
    case = (await org.verify(doc="P1235555")).json()["case"]
    assert (await org.client.post(f"/api/v1/cases/{case['id']}/ai-analysis", headers=org.h("auditor"))).status_code == 403


def _llm_transport(payload: dict | str, status: int = 200, seen: list | None = None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(json.loads(req.content))
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return httpx.Response(status, json={"content": [{"type": "text", "text": text}]})
    return httpx.MockTransport(handler)


async def _detail(org):
    v = (await org.verify(doc="P1231111", subject={"email": "secret.person@example.com"})).json()
    return v


async def test_llm_output_validated_hallucinations_dropped_and_redacted(org, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    detail = await _detail(org)
    good = {"summary": "Name mismatch between document and submission. We should approve this customer.",
            "risk_signals": [{"signal": "identity", "explanation": "x", "evidence_refs": ["PR:identity"]},
                             {"signal": "made_up", "explanation": "invented", "evidence_refs": ["PR:credit_bureau"]}],
            "inconsistencies": [{"description": "name", "evidence_refs": ["SIG:inconsistency:1"]}],
            "reviewer_questions": ["Why the difference?"], "draft_report": "Report", "confidence": 0.7}
    seen: list = []
    client = httpx.AsyncClient(transport=_llm_transport(good, seen=seen))
    out = await ai_analyst.analyze(detail, {"ai_external_llm_allowed": True}, client)
    assert out["source"] == "llm"
    assert [s["signal"] for s in out["analysis"]["risk_signals"]] == ["identity"]  # uncited claim dropped
    assert "approve" not in out["analysis"]["summary"].lower()  # decision language stripped
    assert any("Dropped uncited claim" in w for w in out["warnings"])
    sent = json.dumps(seen[0])
    assert "secret.person@example.com" not in sent and "Amara" not in sent and "P1231111" not in sent


async def test_llm_invalid_output_falls_back(org, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    detail = await _detail(org)
    for transport in (_llm_transport("not json at all"), _llm_transport({"summary": "x", "approve": True}), _llm_transport({}, status=503)):
        out = await ai_analyst.analyze(detail, {"ai_external_llm_allowed": True}, httpx.AsyncClient(transport=transport))
        assert out["source"] == "deterministic" and out["warnings"]


async def test_llm_not_called_unless_org_opts_in(org, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_api_key", "test-key")
    seen: list = []
    out = await ai_analyst.analyze(await _detail(org), {"ai_external_llm_allowed": False}, httpx.AsyncClient(transport=_llm_transport({}, seen=seen)))
    assert out["source"] == "deterministic" and seen == []


# --------------------------------------------------------------------------- dashboard & data
async def test_dashboard_matches_database(org):
    for doc in ("P11112222", "P1230000", "P1235555", "P99990001"):
        await org.verify(doc=doc)
    d = (await org.client.get("/api/v1/dashboard/overview", headers=org.h())).json()
    async with SessionLocal() as db:
        rows = dict((await db.execute(select(VerificationRequest.decision, func.count()).where(VerificationRequest.org_id == org.id)
                                      .group_by(VerificationRequest.decision))).all())
    assert d["total_verifications"] == 4
    assert {k: v for k, v in d["decision_distribution"].items() if v} == rows
    assert d["throughput"][-1]["total"] == 4 and d["p95_processing_latency_ms"] > 0
    assert d["workflow_health"]["completed"] == 4 and d["provider_success_rate"] == 1.0


async def test_settings_update_changes_scoring(org):
    assert (await org.client.patch("/api/v1/settings", headers=org.h(), json={"risk_thresholds": {"medium": 50, "high": 40, "critical": 90}})).status_code == 422
    base = (await org.verify(doc="P1235555")).json()["risk_assessment"]["score"]
    await org.client.patch("/api/v1/settings", headers=org.h(), json={"risk_weights": {"identity": 90}})
    assert (await org.verify(doc="P1235555")).json()["risk_assessment"]["score"] > base


async def test_provider_test_endpoint_and_cache(org):
    r = (await org.client.post("/api/v1/providers/sim_kyb/test", headers=org.h())).json()
    assert r["ok"] and r["normalized"]["outcome"] == "pass"
    await org.verify(name="Repeat Person")
    second = (await org.verify(name="Repeat Person")).json()
    wl = next(p for p in second["provider_results"] if p["check_type"] == "watchlist")
    assert wl["cache_hit"] is True
    providers = (await org.client.get("/api/v1/providers", headers=org.h())).json()
    assert next(p for p in providers if p["key"] == "sim_watchlist")["stats_24h"]["cache_hits"] == 1


async def test_watchlist_cache_is_tenant_scoped(make_org):
    a, b = await make_org(), await make_org()
    await a.verify(name="Shared Name")
    wl = next(p for p in (await b.verify(name="Shared Name")).json()["provider_results"] if p["check_type"] == "watchlist")
    assert wl["cache_hit"] is False


def test_migrations_match_models():
    """`alembic check` fails if the ORM models drift from the migration history."""
    r = subprocess.run([sys.executable, "-m", "alembic", "check"], cwd=BACKEND, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


async def test_health(client):
    assert (await client.get("/health/live")).json() == {"status": "ok"}
    r = await client.get("/health/ready")
    assert r.status_code == 200 and r.json()["checks"] == {"database": "ok", "redis": "ok"}
