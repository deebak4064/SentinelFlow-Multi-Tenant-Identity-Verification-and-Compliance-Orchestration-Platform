# Provider simulation reference

SentinelFlow ships with three **simulated** providers. They never contact a real vendor, and their
results are not legal compliance outcomes. Outcomes are deterministic functions of the synthetic
input, so tests and demos are reproducible. Each organization can also add random latency, outages
and timeouts from the Providers screen (or `PATCH /api/v1/providers/{key}`).

## Identity verification (`sim_idv`)

| Input | Result |
|---|---|
| Document number ending `0000` | Document fails authenticity → `fail` |
| Document number ending `3333` | Document expired → `fail` |
| Document number ending `5555` | Low image quality / inconclusive liveness → `review` |
| Document number ending `1111` | Name extracted from the document differs from the submitted name (inconsistency) |
| Document number ending `2222` | Date of birth on the document differs from the submitted one (inconsistency) |
| Full name containing `Timeout` | Provider never answers → per-attempt timeout, retried, then `error` |
| Full name containing `Unavailable` | Provider returns an outage → retried, then `error` |
| Anything else | `pass` |

## Business registry (`sim_kyb`)

| Registration number | Result |
|---|---|
| Starts with `NF` | Not found → `fail` |
| Starts with `DISS` | Dissolved → `fail` |
| Starts with `UNK` | Registry status unknown → `review` |
| Ends with `999` | Registered legal name differs from the submitted name (inconsistency) |
| Anything else | Active → `pass` |

## Watchlist screening (`sim_watchlist`)

Fuzzy name matching (`difflib` ratio, +0.08 when date of birth also matches) against a short,
entirely fictional list: `Viktor Sanctionov`, `Mara Blacklistova`, `Orion Shellcorp Ltd`,
`Dmitri Flaggedson`, `Helena Watchwell`. List names are prefixed `SIM-` and are not derived from
any real sanctions or PEP list.

| Best match score | Result |
|---|---|
| ≥ 0.90 | Strong match → `fail` |
| 0.72 – 0.90 | Potential match → `review` (try `Victor Santino`) |
| < 0.72 | `pass` |

Screening responses are cached per organization for one hour (key: normalized name, DOB, country).

## Chaos settings

| Setting | Effect |
|---|---|
| `latency_ms` | Base latency, randomized ±50% per call |
| `failure_rate` | Probability of a simulated outage per attempt (retryable) |
| `timeout_rate` | Probability an attempt hangs past the timeout (retryable) |
| `timeout_ms`, `max_retries` | Per-attempt timeout and retry budget (exponential backoff with jitter) |

Five consecutive failures open a per-organization circuit breaker for 30 seconds; calls during that
window fail fast with `circuit_open` and are routed to manual review by policy.
