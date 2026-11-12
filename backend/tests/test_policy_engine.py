import pytest
from pydantic import ValidationError

from app.engine.policy import PolicyDefinition, PolicySnapshot, evaluate
from app.services.bootstrap import DEFAULT_POLICIES

PASS3 = ["pass", "pass"]


def snap(name, priority, when, action, reason="because"):
    return PolicySnapshot(name, name, 1, priority, {"when": when, "action": action, "reason": reason})


def ctx(score=10.0, category="low", **extra):
    base = {"risk": {"score": score, "category": category}, "providers": {"watchlist": {"outcome": "pass", "confidence": 0.0}},
            "signals": {"missing_mandatory_count": 0, "inconsistency_count": 0}, "customer": {"country": "GB", "has_email": True}}
    base.update(extra)
    return base


@pytest.mark.parametrize("op,actual,expected,result", [
    ("eq", 5, 5, True), ("neq", 5, 4, True), ("gt", 5, 4, True), ("gte", 5, 5, True), ("lt", 3, 4, True), ("lte", 4, 4, True),
    ("in", "GB", ["GB", "DE"], True), ("not_in", "GB", ["DE"], True), ("contains", ["a", "b"], "a", True),
    ("gt", 3, 4, False), ("in", "FR", ["GB"], False),
])
def test_operators(op, actual, expected, result):
    d = evaluate([snap("p", 1, {"field": "risk.score", "op": op, "value": expected}, "reject")], ctx(score=actual), PASS3)
    assert (d.action == "reject") is result


def test_exists_missing_and_type_mismatch_never_raise():
    c = ctx()
    assert evaluate([snap("p", 1, {"field": "providers.business.outcome", "op": "missing"}, "reject")], c, PASS3).action == "reject"
    assert evaluate([snap("p", 1, {"field": "providers.watchlist.outcome", "op": "exists"}, "reject")], c, PASS3).action == "reject"
    # numeric comparison against a string must not raise and must not match
    c["risk"]["score"] = "high"
    assert evaluate([snap("p", 1, {"field": "risk.score", "op": "gt", "value": 5}, "reject")], c, PASS3).decided_by == "risk_fallback"


def test_and_or_groups():
    when = {"all": [{"field": "risk.score", "op": "gte", "value": 50},
                    {"any": [{"field": "customer.country", "op": "eq", "value": "XX"}, {"field": "customer.has_email", "op": "eq", "value": False}]}]}
    p = [snap("p", 1, when, "escalate")]
    assert evaluate(p, ctx(score=60, customer={"country": "XX", "has_email": True}), PASS3).action == "escalate"
    assert evaluate(p, ctx(score=60, customer={"country": "GB", "has_email": False}), PASS3).action == "escalate"
    assert evaluate(p, ctx(score=60, customer={"country": "GB", "has_email": True}), PASS3).decided_by == "risk_fallback"
    assert evaluate(p, ctx(score=40, customer={"country": "XX", "has_email": True}), PASS3).decided_by == "risk_fallback"


def test_priority_first_terminal_match_wins_and_trace_is_complete():
    pols = [snap("late-reject", 50, {"field": "risk.score", "op": "gte", "value": 0}, "reject"),
            snap("early-review", 10, {"field": "risk.score", "op": "gte", "value": 0}, "manual_review")]
    d = evaluate(pols, ctx(), PASS3)
    assert d.action == "manual_review" and d.decided_by == "early-review"
    assert [t["policy_name"] for t in d.trace] == ["early-review", "late-reject"]
    assert [t["applied"] for t in d.trace] == [True, False] and all(t["matched"] for t in d.trace)


def test_flag_is_non_terminal():
    pols = [snap("flag", 1, {"field": "customer.has_email", "op": "eq", "value": True}, "flag", "note"),
            snap("rej", 2, {"field": "risk.score", "op": "gte", "value": 0}, "reject")]
    d = evaluate(pols, ctx(), PASS3)
    assert d.action == "reject" and d.flags == ["note"]


@pytest.mark.parametrize("category,action", [("low", "approve"), ("medium", "manual_review"), ("high", "escalate"), ("critical", "escalate")])
def test_risk_band_fallback(category, action):
    assert evaluate([], ctx(category=category), PASS3).action == action


def test_system_guard_blocks_auto_approval_with_non_passing_checks():
    approve_all = [snap("approve", 1, {"field": "risk.score", "op": "gte", "value": 0}, "approve")]
    assert evaluate(approve_all, ctx(), ["pass", "pass"]).action == "approve"
    d = evaluate(approve_all, ctx(), ["pass", "review"])
    assert d.action == "manual_review" and d.decided_by == "system_guard"
    assert evaluate([], ctx(), ["skipped", "error"]).decided_by == "system_guard"


def test_deterministic():
    pols = [snap(p["name"], p["priority"], p["definition"]["when"], p["definition"]["action"]) for p in DEFAULT_POLICIES]
    c = ctx(score=80, category="critical")
    assert len({str(evaluate(pols, c, PASS3).to_dict()) for _ in range(20)}) == 1


@pytest.mark.parametrize("bad", [
    {"when": {"field": "risk.score", "op": "drop table", "value": 1}, "action": "reject", "reason": "xxx"},
    {"when": {"field": "risk.score; DROP", "op": "eq", "value": 1}, "action": "reject", "reason": "xxx"},
    {"when": {"all": []}, "action": "reject", "reason": "xxx"},
    {"when": {"all": [{"field": "a", "op": "eq", "value": 1}], "any": [{"field": "a", "op": "eq", "value": 1}]}, "action": "reject", "reason": "xxx"},
    {"when": {"field": "risk.score", "op": "in", "value": 5}, "action": "reject", "reason": "xxx"},
    {"when": {"field": "risk.score", "op": "gt", "value": "5"}, "action": "reject", "reason": "xxx"},
    {"when": {"field": "risk.score", "op": "eq", "value": 1}, "action": "delete_customer", "reason": "xxx"},
    {"when": {"field": "risk.score", "op": "eq", "value": 1}, "action": "reject", "reason": "xxx", "extra": 1},
])
def test_invalid_definitions_rejected(bad):
    with pytest.raises(ValidationError):
        PolicyDefinition.model_validate(bad)


def test_depth_limit():
    node = {"field": "risk.score", "op": "eq", "value": 1}
    for _ in range(7):
        node = {"all": [node]}
    with pytest.raises(ValidationError):
        PolicyDefinition.model_validate({"when": node, "action": "reject", "reason": "deep"})


def test_default_policies_are_valid():
    for p in DEFAULT_POLICIES:
        PolicyDefinition.model_validate(p["definition"])
