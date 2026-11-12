"""Transparent weighted risk scoring.

score = 100 * sum(weight_i * severity_i) / sum(weight_i over applicable factors)

Each severity is in [0, 1]. A weighted average alone lets one decisive red flag be diluted by
clean factors, so explicit, documented floors apply on top (a strong watchlist match can never be
"medium"); a floor that applies is listed in the breakdown as factor "floor".
Each severity is in [0, 1]. A factor whose check did not run (e.g. business verification for an
individual) is excluded from both numerator and denominator, so scores stay comparable across
verification types. Every factor's contribution is returned in the breakdown.
"""

from dataclasses import dataclass

MODEL_VERSION = "risk-v1"

DEFAULT_WEIGHTS = {
    "identity": 30,
    "business": 25,
    "watchlist": 35,
    "missing_information": 10,
    "inconsistencies": 15,
    "history": 10,
}
DEFAULT_THRESHOLDS = {"medium": 25, "high": 50, "critical": 75}
# (condition description, minimum score) — evaluated in compute_risk
FLOORS = {"strong_watchlist_match": 80.0, "identity_failed": 55.0, "business_failed": 55.0}

OUTCOME_SEVERITY = {"pass": 0.0, "review": 0.5, "error": 0.6, "fail": 1.0}


@dataclass
class RiskResult:
    score: float
    category: str
    breakdown: list[dict]


def categorize(score: float, thresholds: dict) -> str:
    if score >= thresholds["critical"]:
        return "critical"
    if score >= thresholds["high"]:
        return "high"
    if score >= thresholds["medium"]:
        return "medium"
    return "low"


def compute_risk(results: dict[str, dict], signals: dict, history: dict, weights: dict | None = None, thresholds: dict | None = None) -> RiskResult:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    factors: list[tuple[str, float, str]] = []  # (factor, severity, explanation)

    for check in ("identity", "business"):
        r = results.get(check)
        if r and r["outcome"] != "skipped":
            sev = OUTCOME_SEVERITY.get(r["outcome"], 0.6)
            factors.append((check, sev, f"{check} check outcome '{r['outcome']}'"))

    wl = results.get("watchlist")
    if wl and wl["outcome"] != "skipped":
        if wl["outcome"] == "error":
            factors.append(("watchlist", 0.6, "Watchlist screening unavailable"))
        else:
            conf = float(wl.get("signals", {}).get("match_confidence", 0.0))
            sev = 1.0 if conf >= 0.9 else conf
            factors.append(("watchlist", sev, f"Top watchlist match confidence {conf:.2f}"))

    missing = signals.get("missing_fields", [])
    expected = max(signals.get("expected_field_count", 1), 1)
    factors.append(("missing_information", min(len(missing) / expected * 2, 1.0),
                    f"{len(missing)} of {expected} expected fields missing" + (f": {', '.join(missing)}" if missing else "")))

    inc = signals.get("inconsistencies", [])
    factors.append(("inconsistencies", min(len(inc) / 2, 1.0), f"{len(inc)} inconsistencies" + (f": {'; '.join(inc)}" if inc else "")))

    failed = history.get("failed_verifications", 0)
    factors.append(("history", min(failed / 3, 1.0), f"{failed} previously rejected verifications"))

    total_w = sum(w[f] for f, _, _ in factors)
    breakdown, raw = [], 0.0
    for f, sev, why in factors:
        points = 100 * w[f] * sev / total_w if total_w else 0.0
        raw += points
        breakdown.append({"factor": f, "weight": w[f], "severity": round(sev, 3), "points": round(points, 2), "explanation": why})
    score = round(min(max(raw, 0.0), 100.0), 2)
    breakdown.sort(key=lambda b: -b["points"])

    triggered = []
    if wl and wl["outcome"] == "fail":
        triggered.append(("strong_watchlist_match", "Strong watchlist match"))
    for check in ("identity", "business"):
        if results.get(check, {}).get("outcome") == "fail":
            triggered.append((f"{check}_failed", f"{check.title()} check failed"))
    if triggered:
        name, label = max(triggered, key=lambda x: FLOORS[x[0]])
        if FLOORS[name] > score:
            breakdown.append({"factor": "floor", "weight": 0, "severity": 1.0, "points": round(FLOORS[name] - score, 2),
                              "explanation": f"{label}: minimum score {FLOORS[name]:.0f} applied"})
            score = FLOORS[name]
    return RiskResult(score, categorize(score, t), breakdown)
