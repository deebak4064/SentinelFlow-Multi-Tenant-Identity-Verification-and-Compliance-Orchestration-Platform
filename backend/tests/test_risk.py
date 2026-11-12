import pytest

from app.engine.risk import DEFAULT_WEIGHTS, categorize, compute_risk

CLEAN = {"missing_fields": [], "expected_field_count": 6, "inconsistencies": []}


def r(outcome, conf=None):
    out = {"outcome": outcome, "signals": {}}
    if conf is not None:
        out["signals"]["match_confidence"] = conf
    return out


def test_clean_individual_scores_zero():
    res = compute_risk({"identity": r("pass"), "watchlist": r("pass", 0.0)}, CLEAN, {})
    assert res.score == 0 and res.category == "low"
    assert {b["factor"] for b in res.breakdown} == {"identity", "watchlist", "missing_information", "inconsistencies", "history"}


def test_business_factor_excluded_for_individuals():
    res = compute_risk({"identity": r("review"), "watchlist": r("pass", 0.0)}, CLEAN, {})
    total_w = sum(DEFAULT_WEIGHTS[f] for f in ("identity", "watchlist", "missing_information", "inconsistencies", "history"))
    assert res.score == pytest.approx(100 * 30 * 0.5 / total_w, abs=0.01)
    assert "business" not in {b["factor"] for b in res.breakdown}


def test_breakdown_points_sum_to_score_without_floor():
    res = compute_risk({"identity": r("review"), "watchlist": r("review", 0.8)},
                       {"missing_fields": ["email"], "expected_field_count": 6, "inconsistencies": ["x"]}, {"failed_verifications": 1})
    assert sum(b["points"] for b in res.breakdown) == pytest.approx(res.score, abs=0.05)
    assert res.breakdown == sorted(res.breakdown, key=lambda b: -b["points"])


def test_strong_watchlist_floor_makes_it_critical():
    res = compute_risk({"identity": r("pass"), "watchlist": r("fail", 0.95)}, CLEAN, {})
    assert res.score == 80 and res.category == "critical"
    assert res.breakdown[-1]["factor"] == "floor"


def test_failed_identity_floor():
    res = compute_risk({"identity": r("fail"), "watchlist": r("pass", 0)}, CLEAN, {})
    assert res.score == 55 and res.category == "high"


def test_custom_weights_and_thresholds():
    base = compute_risk({"identity": r("review"), "watchlist": r("pass", 0)}, CLEAN, {})
    heavier = compute_risk({"identity": r("review"), "watchlist": r("pass", 0)}, CLEAN, {}, {"identity": 90})
    assert heavier.score > base.score
    assert compute_risk({"identity": r("review"), "watchlist": r("pass", 0)}, CLEAN, {}, None, {"medium": 5, "high": 10, "critical": 90}).category == "high"


def test_history_and_score_bounds():
    worst = compute_risk({"identity": r("fail"), "business": r("fail"), "watchlist": r("fail", 1.0)},
                         {"missing_fields": list("abcdef"), "expected_field_count": 6, "inconsistencies": ["a", "b", "c"]},
                         {"failed_verifications": 9})
    assert worst.score == 100.0 and worst.category == "critical"


@pytest.mark.parametrize("score,cat", [(0, "low"), (24.99, "low"), (25, "medium"), (50, "high"), (75, "critical"), (100, "critical")])
def test_categorize_boundaries(score, cat):
    assert categorize(score, {"medium": 25, "high": 50, "critical": 75}) == cat
