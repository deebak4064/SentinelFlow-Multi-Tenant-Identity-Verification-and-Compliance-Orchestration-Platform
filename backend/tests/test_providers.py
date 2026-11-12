import asyncio

import pytest

from app.engine.providers import (
    REGISTRY,
    CircuitBreaker,
    ProviderAdapter,
    ProviderBadRequest,
    ProviderUnavailable,
    execute_with_resilience,
)

SIM = {"latency_ms": 1}
IND = {"full_name": "Amara Okafor", "screen_name": "Amara Okafor", "dob": "1990-04-12", "country": "GB",
       "document_type": "passport", "document_number": "P12345678"}


async def run(key, subject, **cfg):
    a = REGISTRY[key]
    out = await execute_with_resilience(a, a.build_request(subject), {"timeout_ms": 200, "max_retries": 2, "simulation": SIM, **cfg}, CircuitBreaker())
    return out, (a.normalize(out.raw, subject) if out.raw else None)


@pytest.mark.parametrize("doc,outcome,signal", [
    ("P12345678", "pass", None), ("P1230000", "fail", ("document_authentic", False)), ("P1235555", "review", ("image_quality", "low")),
    ("P1233333", "fail", ("document_expired", True)), ("P1232222", "pass", ("dob_match", False)),
])
async def test_identity_provider_outcomes(doc, outcome, signal):
    _, n = await run("sim_idv", {**IND, "document_number": doc})
    assert n.outcome == outcome
    if signal:
        assert n.signals[signal[0]] == signal[1]


async def test_identity_name_mismatch_is_reported():
    _, n = await run("sim_idv", {**IND, "document_number": "P1231111"})
    assert n.signals["name_similarity"] < 0.85


@pytest.mark.parametrize("reg,outcome,status", [
    ("RC123456", "pass", "active"), ("DISS1234", "fail", "dissolved"), ("NF123456", "fail", "not_found"), ("UNK12345", "review", "unknown"),
])
async def test_business_provider_outcomes(reg, outcome, status):
    s = {"company_name": "Brightline Logistics Ltd", "registration_number": reg, "country": "DE"}
    _, n = await run("sim_kyb", s)
    assert n.outcome == outcome and n.signals["registry_status"] == status


@pytest.mark.parametrize("name,outcome", [("Amara Okafor", "pass"), ("Viktor Sanctionov", "fail"), ("Victor Santino", "review")])
async def test_watchlist_outcomes(name, outcome):
    _, n = await run("sim_watchlist", {"screen_name": name, "dob": None, "country": "GB"})
    assert n.outcome == outcome


async def test_timeout_is_retried_then_reported():
    out, n = await run("sim_idv", {**IND, "full_name": "Tim Timeout"}, timeout_ms=50, max_retries=2)
    assert n is None and out.error_code == "timeout" and out.attempts == 3


async def test_unavailable_is_retried_then_reported():
    out, _ = await run("sim_idv", {**IND, "full_name": "Una Unavailable"}, max_retries=1)
    assert out.error_code == "unavailable" and out.attempts == 2


class Flaky(ProviderAdapter):
    key, check_type, display_name, description, required_fields = "flaky", "identity", "Flaky", "test", ()

    def __init__(self, failures, exc=ProviderUnavailable):
        self.failures, self.exc, self.calls = failures, exc, 0

    def build_request(self, s):
        return {}

    async def call(self, payload, sim):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc("boom")
        return {"ok": True}

    def normalize(self, raw, s):
        raise NotImplementedError


async def test_transient_failure_recovers():
    a = Flaky(failures=2)
    out = await execute_with_resilience(a, {}, {"timeout_ms": 100, "max_retries": 2}, CircuitBreaker())
    assert out.raw == {"ok": True} and out.attempts == 3 and out.recovered


async def test_non_retryable_errors_are_not_retried():
    a = Flaky(failures=5, exc=ProviderBadRequest)
    out = await execute_with_resilience(a, {}, {"timeout_ms": 100, "max_retries": 3}, CircuitBreaker())
    assert out.error_code == "bad_request" and a.calls == 1


async def test_circuit_breaker_opens_and_half_opens():
    br = CircuitBreaker(threshold=3, cooldown_s=0.05)
    a = Flaky(failures=100)
    await execute_with_resilience(a, {}, {"timeout_ms": 100, "max_retries": 5}, br)
    assert br.state == "open" and a.calls == 3  # stops retrying once the breaker opens
    out = await execute_with_resilience(a, {}, {"timeout_ms": 100, "max_retries": 0}, br)
    assert out.error_code == "circuit_open" and a.calls == 3  # short-circuited, upstream not called
    await asyncio.sleep(0.06)
    assert br.state == "half_open"
    a.failures = 0
    out = await execute_with_resilience(a, {}, {"timeout_ms": 100, "max_retries": 0}, br)
    assert out.raw and br.state == "closed"
