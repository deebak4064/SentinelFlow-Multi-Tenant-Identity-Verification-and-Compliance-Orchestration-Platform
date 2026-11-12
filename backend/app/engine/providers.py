"""Provider adapter layer.

A provider adapter knows three things: how to build its request from the verification subject,
how to call the upstream API, and how to normalize the upstream's response shape into a
`NormalizedResult`. The orchestrator only ever talks to `ProviderAdapter`, so a real vendor can be
added by writing one class and registering it in `REGISTRY`.

The three adapters here are SIMULATIONS. Their outcomes are deterministic functions of the
synthetic input ("magic values", documented in docs/simulation.md) plus optional chaos knobs
(latency, failure rate, timeout rate) configured per organization.
"""

import asyncio
import difflib
import hashlib
import random
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field

OUTCOMES = ("pass", "fail", "review", "error", "skipped")


class ProviderError(Exception):
    code = "provider_error"
    retryable = True


class ProviderUnavailable(ProviderError):
    code = "unavailable"


class ProviderBadRequest(ProviderError):
    code = "bad_request"
    retryable = False


@dataclass
class NormalizedResult:
    provider_key: str
    check_type: str
    outcome: str
    confidence: float
    signals: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    extracted: dict = field(default_factory=dict)
    error_code: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class ProviderAdapter(ABC):
    key: str
    check_type: str
    display_name: str
    description: str
    required_fields: tuple[str, ...]

    @abstractmethod
    def build_request(self, subject: dict) -> dict: ...

    @abstractmethod
    async def call(self, payload: dict, sim: dict) -> dict: ...

    @abstractmethod
    def normalize(self, raw: dict, subject: dict) -> NormalizedResult: ...

    def cache_key(self, payload: dict) -> str | None:
        """Return a cache key if responses for this payload are safe to reuse."""
        return None


async def _simulate_network(sim: dict, payload_hint: str) -> None:
    """Latency + chaos. Magic values in the payload force deterministic failures."""
    hint = payload_hint.lower()
    if "unavailable" in hint:
        raise ProviderUnavailable("simulated outage")
    if "timeout" in hint:
        await asyncio.sleep(30)  # longer than any configured timeout
    if sim.get("failure_rate", 0) and random.random() < sim["failure_rate"]:
        raise ProviderUnavailable("simulated random outage")
    if sim.get("timeout_rate", 0) and random.random() < sim["timeout_rate"]:
        await asyncio.sleep(30)
    base = sim.get("latency_ms", 40) / 1000
    await asyncio.sleep(base * random.uniform(0.5, 1.5))


# --------------------------------------------------------------------------- identity
class IdentityVerificationProvider(ProviderAdapter):
    key = "sim_idv"
    check_type = "identity"
    display_name = "Simulated Identity Verification"
    description = "Document authenticity, data extraction and selfie liveness (synthetic)."
    required_fields = ("full_name", "dob", "document_type", "document_number", "country")

    def build_request(self, s: dict) -> dict:
        return {
            "applicant": {"name": s["full_name"], "birth_date": s["dob"], "nationality": s["country"]},
            "document": {"type": s["document_type"], "number": s["document_number"]},
        }

    async def call(self, payload: dict, sim: dict) -> dict:
        await _simulate_network(sim, payload["applicant"]["name"])
        doc = payload["document"]["number"]
        name, dob = payload["applicant"]["name"], payload["applicant"]["birth_date"]
        extracted_name, extracted_dob = name, dob
        if doc.endswith("1111"):
            extracted_name = name.split()[0] + " Doe-Alternate"
        if doc.endswith("2222"):
            extracted_dob = f"{int(dob[:4]) - 7}{dob[4:]}"
        authentic = not doc.endswith("0000")
        quality = "LOW" if doc.endswith("5555") else "HIGH"
        status = "REJECTED" if not authentic else ("INCONCLUSIVE" if quality == "LOW" else "VERIFIED")
        return {
            "status": status,
            "document_checks": {"authentic": authentic, "image_quality": quality, "expired": doc.endswith("3333")},
            "extracted_data": {"name": extracted_name, "birth_date": extracted_dob},
            "liveness": {"score": 0.42 if quality == "LOW" else 0.97},
            "reference": "SIMIDV-" + hashlib.sha1(doc.encode()).hexdigest()[:10].upper(),
        }

    def normalize(self, raw: dict, s: dict) -> NormalizedResult:
        checks, ext = raw["document_checks"], raw["extracted_data"]
        name_sim = difflib.SequenceMatcher(None, s["full_name"].lower(), ext["name"].lower()).ratio()
        dob_match = ext["birth_date"] == s["dob"]
        reasons, signals = [], {
            "document_authentic": checks["authentic"],
            "document_expired": checks["expired"],
            "image_quality": checks["image_quality"].lower(),
            "liveness_score": raw["liveness"]["score"],
            "name_similarity": round(name_sim, 3),
            "dob_match": dob_match,
        }
        if not checks["authentic"]:
            outcome, conf = "fail", 0.95
            reasons.append("Document failed authenticity checks")
        elif checks["expired"]:
            outcome, conf = "fail", 0.9
            reasons.append("Document is expired")
        elif raw["status"] == "INCONCLUSIVE":
            outcome, conf = "review", 0.45
            reasons.append("Low image quality / liveness inconclusive")
        else:
            outcome, conf = "pass", round(min(raw["liveness"]["score"], 0.5 + name_sim / 2), 3)
            reasons.append("Document verified and liveness passed")
        return NormalizedResult(self.key, self.check_type, outcome, conf, signals, reasons, {"name": ext["name"], "dob": ext["birth_date"]})


# --------------------------------------------------------------------------- business
class BusinessVerificationProvider(ProviderAdapter):
    key = "sim_kyb"
    check_type = "business"
    display_name = "Simulated Business Registry"
    description = "Company registry lookup: status, registered name, incorporation (synthetic)."
    required_fields = ("company_name", "registration_number", "country")

    def build_request(self, s: dict) -> dict:
        return {"q": {"company": s["company_name"], "reg_no": s["registration_number"], "jurisdiction": s["country"]}}

    async def call(self, payload: dict, sim: dict) -> dict:
        q = payload["q"]
        await _simulate_network(sim, q["company"])
        reg = q["reg_no"].upper()
        if reg.startswith("NF"):
            return {"match": False, "records": []}
        registered_name = q["company"] if not reg.endswith("999") else q["company"] + " Trading Group"
        state = "DISSOLVED" if reg.startswith("DISS") else ("UNKNOWN" if reg.startswith("UNK") else "ACTIVE")
        return {"match": True, "records": [{"legal_name": registered_name, "state": state, "reg_no": reg, "jurisdiction": q["jurisdiction"]}]}

    def normalize(self, raw: dict, s: dict) -> NormalizedResult:
        if not raw["match"]:
            return NormalizedResult(self.key, self.check_type, "fail", 0.85, {"registry_status": "not_found"}, ["Company not found in registry"])
        rec = raw["records"][0]
        sim = difflib.SequenceMatcher(None, s["company_name"].lower(), rec["legal_name"].lower()).ratio()
        signals = {"registry_status": rec["state"].lower(), "name_similarity": round(sim, 3)}
        if rec["state"] == "DISSOLVED":
            return NormalizedResult(self.key, self.check_type, "fail", 0.95, signals, ["Company is dissolved"], {"legal_name": rec["legal_name"]})
        if rec["state"] == "UNKNOWN":
            return NormalizedResult(self.key, self.check_type, "review", 0.4, signals, ["Registry status could not be determined"], {"legal_name": rec["legal_name"]})
        return NormalizedResult(self.key, self.check_type, "pass", round(sim, 3), signals, ["Active registry record found"], {"legal_name": rec["legal_name"]})


# --------------------------------------------------------------------------- watchlist
# Entirely fictional entries. Not derived from any real sanctions or PEP list.
SYNTHETIC_WATCHLIST = [
    {"name": "Viktor Sanctionov", "dob": "1971-03-14", "list": "SIM-SANCTIONS-A", "country": "XX"},
    {"name": "Mara Blacklistova", "dob": "1984-11-02", "list": "SIM-SANCTIONS-B", "country": "XX"},
    {"name": "Orion Shellcorp Ltd", "dob": None, "list": "SIM-ENTITY-LIST", "country": "XX"},
    {"name": "Dmitri Flaggedson", "dob": "1990-06-30", "list": "SIM-PEP", "country": "XX"},
    {"name": "Helena Watchwell", "dob": "1979-01-21", "list": "SIM-ADVERSE-MEDIA", "country": "XX"},
]


class WatchlistScreeningProvider(ProviderAdapter):
    key = "sim_watchlist"
    check_type = "watchlist"
    display_name = "Simulated Watchlist Screening"
    description = "Fuzzy name screening against a fictional sanctions/PEP list (synthetic)."
    required_fields = ("screen_name", "country")

    def build_request(self, s: dict) -> dict:
        return {"name": s["screen_name"], "dob": s.get("dob"), "country": s["country"]}

    def cache_key(self, payload: dict) -> str | None:
        return f"{payload['name'].strip().lower()}|{payload.get('dob')}|{payload['country']}"

    async def call(self, payload: dict, sim: dict) -> dict:
        await _simulate_network(sim, payload["name"])
        hits = []
        for entry in SYNTHETIC_WATCHLIST:
            score = difflib.SequenceMatcher(None, payload["name"].lower(), entry["name"].lower()).ratio()
            if entry["dob"] and payload.get("dob") and entry["dob"] == payload["dob"]:
                score = min(1.0, score + 0.08)
            if score >= 0.72:
                hits.append({"entry": entry["name"], "list": entry["list"], "score": round(score, 3)})
        return {"screened_at": time.time(), "total_hits": len(hits), "hits": sorted(hits, key=lambda h: -h["score"])}

    def normalize(self, raw: dict, s: dict) -> NormalizedResult:
        if not raw["hits"]:
            return NormalizedResult(self.key, self.check_type, "pass", 0.97, {"match_confidence": 0.0, "hit_count": 0}, ["No watchlist matches"])
        top = raw["hits"][0]
        signals = {"match_confidence": top["score"], "hit_count": raw["total_hits"], "top_list": top["list"]}
        if top["score"] >= 0.9:
            return NormalizedResult(self.key, self.check_type, "fail", top["score"], signals, [f"Strong match on {top['list']}"])
        return NormalizedResult(self.key, self.check_type, "review", top["score"], signals, [f"Potential match on {top['list']} requires review"])


REGISTRY: dict[str, ProviderAdapter] = {
    p.key: p for p in (IdentityVerificationProvider(), BusinessVerificationProvider(), WatchlistScreeningProvider())
}


# --------------------------------------------------------------------------- resilience
class CircuitBreaker:
    """Consecutive-failure breaker. ponytail: per-process state; move to Redis for fleet-wide state."""

    def __init__(self, threshold: int = 5, cooldown_s: float = 30.0):
        self.threshold, self.cooldown_s = threshold, cooldown_s
        self.failures, self.opened_at = 0, None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if time.monotonic() - self.opened_at >= self.cooldown_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record(self, ok: bool) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
        else:
            self.failures += 1
            if self.failures >= self.threshold:
                self.opened_at = time.monotonic()


_breakers: dict[tuple, CircuitBreaker] = {}


def breaker_for(org_id, provider_key: str) -> CircuitBreaker:
    return _breakers.setdefault((str(org_id), provider_key), CircuitBreaker())


@dataclass
class ExecutionOutcome:
    raw: dict | None
    attempts: int
    latency_ms: float
    error_code: str | None = None
    recovered: bool = False  # succeeded after at least one failed attempt


def backoff_s(attempt: int) -> float:
    return min(0.05 * 2 ** (attempt - 1), 1.0) + random.uniform(0, 0.025)


async def execute_with_resilience(adapter: ProviderAdapter, payload: dict, cfg: dict, breaker: CircuitBreaker) -> ExecutionOutcome:
    timeout_s = cfg.get("timeout_ms", 800) / 1000
    max_retries = cfg.get("max_retries", 2)
    sim = cfg.get("simulation", {}) or {}
    start = time.perf_counter()
    if not breaker.allow():
        return ExecutionOutcome(None, 0, 0.0, "circuit_open")
    error = None
    for attempt in range(1, max_retries + 2):
        try:
            raw = await asyncio.wait_for(adapter.call(payload, sim), timeout_s)
            breaker.record(True)
            return ExecutionOutcome(raw, attempt, (time.perf_counter() - start) * 1000, recovered=attempt > 1)
        except TimeoutError:
            error = "timeout"
        except ProviderError as e:
            error = e.code
            if not e.retryable:
                breaker.record(False)
                break
        breaker.record(False)
        if attempt <= max_retries and breaker.allow():
            await asyncio.sleep(backoff_s(attempt))
        else:
            break
    return ExecutionOutcome(None, attempt, (time.perf_counter() - start) * 1000, error)
