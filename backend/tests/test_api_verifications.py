import asyncio
import uuid

from sqlalchemy import select

from app.db import SessionLocal
from app.engine.orchestrator import claim, run_workflow
from app.models import Customer, ProviderResult, VerificationRequest
from tests.conftest import business, individual

API = "/api/v1/verifications"


def providers_by_check(body):
    return {r["check_type"]: r for r in body["provider_results"]}


async def test_clean_individual_is_auto_approved(org):
    r = await org.verify()
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "completed" and b["decision"] == "approved" and b["decision_source"] == "policy"
    assert b["risk_assessment"]["category"] == "low" and b["case"] is None
    assert {s["name"] for s in b["workflow"]["steps"]} >= {"load_context", "select_providers", "provider:sim_idv", "provider:sim_watchlist",
                                                         "risk_scoring", "policy_evaluation", "persist"}
    assert b["customer"]["full_name"] == "A*** O***" and b["customer"]["document_number"] == "*****5678"


async def test_business_verification(org):
    r = await org.client.post(API, headers=org.h(), json=business())
    b = r.json()
    assert r.status_code == 201 and b["decision"] == "approved"
    assert set(providers_by_check(b)) == {"business", "watchlist"}


async def test_dissolved_company_is_reviewed_or_rejected(org):
    b = (await org.client.post(API, headers=org.h(), json=business(reg="DISS4321"))).json()
    assert b["decision"] in ("manual_review", "escalated", "rejected")
    assert providers_by_check(b)["business"]["outcome"] == "fail"


async def test_forged_document_is_rejected(org):
    b = (await org.verify(doc="P1230000")).json()
    assert b["decision"] == "rejected"
    assert b["policy_trace"]["reason"] in ("Identity document failed authenticity checks", "Uncertain or conflicting provider responses")


async def test_high_risk_customer_strong_watchlist_is_escalated(org):
    b = (await org.verify(name="Viktor Sanctionov")).json()
    assert b["decision"] == "escalated"
    assert b["risk_assessment"]["category"] == "critical"
    assert b["case"]["status"] == "escalated" and b["case"]["priority"] == "critical"
    assert any(f["factor"] == "floor" for f in b["risk_assessment"]["breakdown"])


async def test_ambiguous_result_creates_manual_review_case(org):
    b = (await org.verify(doc="P1235555")).json()
    assert b["decision"] == "manual_review" and b["case"]["status"] == "open"


async def test_conflicting_responses_require_review(org):
    # identity passes, watchlist fails → conflicting; strong watchlist escalates first by priority
    b = (await org.verify(name="Dmitri Flaggedson")).json()
    ctx = b["policy_trace"]["context"]["signals"]
    assert ctx["conflicting_results"] is True and b["decision"] in ("escalated", "manual_review")


async def test_mismatched_identity_is_flagged_for_review(org):
    b = (await org.verify(doc="P1231111")).json()
    assert "Document name does not match submitted name" in b["policy_trace"]["context"]["signals"]["inconsistencies"]
    assert b["decision"] == "manual_review"


async def test_missing_mandatory_field_is_rejected_without_calling_provider(org):
    p = individual()
    del p["subject"]["document_number"]
    b = (await org.client.post(API, headers=org.h(), json=p)).json()
    assert b["decision"] == "rejected" and b["policy_trace"]["reason"] == "Mandatory verification fields are missing"
    idv = providers_by_check(b)["identity"]
    assert idv["error_code"] == "insufficient_input" and idv["attempts"] == 0


async def test_provider_timeout_retries_then_routes_to_review(org):
    b = (await org.verify(name="Tim Timeout")).json()
    idv = providers_by_check(b)["identity"]
    assert idv["outcome"] == "error" and idv["error_code"] == "timeout" and idv["attempts"] == 3
    assert b["decision"] == "manual_review"


async def test_provider_unavailable_routes_to_review(org):
    b = (await org.verify(name="Una Unavailable")).json()
    assert providers_by_check(b)["identity"]["error_code"] == "unavailable"
    assert b["decision"] == "manual_review" and b["status"] == "completed"


async def test_disabled_provider_is_skipped(org):
    await org.client.patch("/api/v1/providers/sim_watchlist", headers=org.h(), json={"enabled": False})
    b = (await org.verify()).json()
    assert providers_by_check(b)["watchlist"]["outcome"] == "skipped"
    assert "watchlist" not in {f["factor"] for f in b["risk_assessment"]["breakdown"]}


async def test_duplicate_request_idempotent_replay(org):
    p, key = individual(), f"idem-{uuid.uuid4().hex}"
    first = await org.client.post(API, headers={**org.h(), "Idempotency-Key": key}, json=p)
    second = await org.client.post(API, headers={**org.h(), "Idempotency-Key": key}, json=p)
    assert first.status_code == 201 and second.status_code == 200
    assert second.headers["Idempotent-Replayed"] == "true" and second.json()["id"] == first.json()["id"]
    async with SessionLocal() as db:
        n = len((await db.scalars(select(VerificationRequest).where(VerificationRequest.idempotency_key == key))).all())
    assert n == 1


async def test_idempotency_key_reuse_with_different_body_conflicts(org):
    key = f"idem-{uuid.uuid4().hex}"
    await org.client.post(API, headers={**org.h(), "Idempotency-Key": key}, json=individual())
    r = await org.client.post(API, headers={**org.h(), "Idempotency-Key": key}, json=individual(name="Someone Else"))
    assert r.status_code == 409 and r.json()["error"]["code"] == "idempotency_conflict"


async def test_concurrent_duplicates_create_one_request(org):
    p, key = individual(), f"idem-{uuid.uuid4().hex}"
    rs = await asyncio.gather(*[org.client.post(API, headers={**org.h(), "Idempotency-Key": key}, json=p) for _ in range(5)])
    assert sorted(r.status_code for r in rs).count(201) == 1
    assert all(r.status_code in (200, 201, 409) for r in rs)
    async with SessionLocal() as db:
        assert len((await db.scalars(select(VerificationRequest.id).where(VerificationRequest.idempotency_key == key))).all()) == 1


async def test_repeat_customer_upserts_and_counts_history(org):
    ref = f"ref-{uuid.uuid4().hex[:8]}"
    await org.verify(doc="P1230000", ref=ref)  # rejected
    b = (await org.verify(ref=ref)).json()
    assert b["policy_trace"]["context"]["history"]["failed_verifications"] == 1
    async with SessionLocal() as db:
        assert len((await db.scalars(select(Customer).where(Customer.external_ref == ref))).all()) == 1


async def test_malformed_requests_are_rejected(org):
    bad = [
        {"external_ref": "x", "verification_type": "individual", "subject": {"full_name": "A B", "country": "gb"}},
        {"external_ref": "x", "verification_type": "alien", "subject": {"full_name": "A B", "country": "GB"}},
        {"external_ref": "x", "verification_type": "individual", "subject": {"full_name": "A B", "country": "GB", "dob": "2999-01-01"}},
        {"external_ref": "x y", "verification_type": "individual", "subject": {"full_name": "A B", "country": "GB"}},
        {"external_ref": "x", "verification_type": "individual", "subject": {"full_name": "A B", "country": "GB", "is_admin": True}},
        {"external_ref": "x", "verification_type": "individual", "subject": {"country": "GB"}},
        {"external_ref": "x", "verification_type": "individual", "subject": {"full_name": "A", "country": "GB", "document_number": "'; DROP TABLE x;--"}},
    ]
    for p in bad:
        r = await org.client.post(API, headers=org.h(), json=p)
        assert r.status_code == 422, p
        assert r.json()["error"]["code"] == "validation_error" and r.json()["error"]["request_id"]
    r = await org.client.post(API, headers={**org.h(), "content-type": "application/json"}, content=b"{not json")
    assert r.status_code == 422


async def test_oversized_body_rejected(org):
    r = await org.client.post(API, headers={**org.h(), "content-type": "application/json"}, content=b'{"a":"' + b"x" * 300_000 + b'"}')
    assert r.status_code == 413


async def test_queue_mode_returns_202_and_worker_completes(org):
    r = await org.verify(execution_mode="queue")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    vid = uuid.UUID(r.json()["id"])
    assert await claim(vid) is True
    assert await claim(vid) is False  # a second worker cannot claim it
    await run_workflow(vid, "queue")
    b = (await org.client.get(f"{API}/{vid}", headers=org.h())).json()
    assert b["status"] == "completed" and b["workflow"]["execution_mode"] == "queue"


async def test_raw_provider_payloads_are_stored_without_clear_pii(org):
    b = (await org.verify(name="Amara Okafor")).json()
    async with SessionLocal() as db:
        rows = (await db.scalars(select(ProviderResult).where(ProviderResult.verification_id == uuid.UUID(b["id"])))).all()
    blob = str([r.raw_response for r in rows]) + str([r.normalized for r in rows])
    assert "Amara Okafor" not in blob and "1990-04-12" not in blob


async def test_pii_encrypted_at_rest(org):
    b = (await org.verify(doc="P99887766")).json()
    async with SessionLocal() as db:
        c = await db.get(Customer, uuid.UUID(b["customer_id"]))
    assert b"P99887766" not in c.document_number_enc and b"1990-04-12" not in c.dob_enc


async def test_list_filters_sort_and_pagination(org):
    for _ in range(3):
        await org.verify()
    await org.verify(doc="P1230000")
    r = (await org.client.get(f"{API}?decision=rejected", headers=org.h())).json()
    assert r["total"] == 1 and r["items"][0]["decision"] == "rejected"
    r = (await org.client.get(f"{API}?page_size=2&page=2&sort=created_at", headers=org.h())).json()
    assert r["total"] == 4 and len(r["items"]) == 2
    assert (await org.client.get(f"{API}?sort=password_hash", headers=org.h())).status_code == 400
    assert (await org.client.get(f"{API}?q=%25", headers=org.h())).json()["total"] == 0  # wildcard is escaped


async def test_manual_review_endpoint(org):
    b = (await org.verify(doc="P1235555")).json()
    r = await org.client.post(f"{API}/{b['id']}/review", headers=org.h(), json={"decision": "approve", "notes": "Clear resubmitted image"})
    assert r.status_code == 200 and r.json()["decision"] == "approved" and r.json()["decision_source"] == "manual"
    assert r.json()["automated_decision"] == "manual_review"
    again = await org.client.post(f"{API}/{b['id']}/review", headers=org.h(), json={"decision": "reject", "notes": "changed mind"})
    assert again.status_code == 409
    auto = (await org.verify()).json()
    assert (await org.client.post(f"{API}/{auto['id']}/review", headers=org.h(), json={"decision": "reject", "notes": "nope"})).status_code == 409
