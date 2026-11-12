<div align="center">

# SentinelFlow

### Multi-Tenant Identity Verification and Compliance Orchestration Platform

**One API to run KYC/KYB checks across multiple providers, score risk transparently, apply versioned
policies deterministically, route uncertain cases to people, and keep a tamper-resistant record of every step.**

[![CI](https://github.com/deebak4064/SentinelFlow-Multi-Tenant-Identity-Verification-and-Compliance-Orchestration-Platform/actions/workflows/ci.yml/badge.svg)](https://github.com/deebak4064/SentinelFlow-Multi-Tenant-Identity-Verification-and-Compliance-Orchestration-Platform/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009485?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)
![TypeScript](https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-167%20passing-2e9e76)
![Coverage](https://img.shields.io/badge/backend%20coverage-94.2%25-2e9e76)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

</div>

> [!IMPORTANT]
> **Simulation notice.** All verification providers are simulations and all data is synthetic.
> SentinelFlow never collects real identity documents, and its results are not legal compliance certification.

<p align="center"><img src="docs/screenshots/03-verification-detail.png" alt="Verification decision trace: execution waterfall, policy chain, provider results and risk breakdown" width="100%"></p>

> [!TIP]
> **The diagrams on this page are interactive.** GitHub renders them live: hover a diagram and use
> the controls to **zoom, pan and open it full screen**. Sections marked ▸ expand on click.

---

## Contents

| | | |
|---|---|---|
| [1. Overview](#1-project-overview) | [6. Key features](#6-key-features) | [11. Environment](#11-environment-configuration) |
| [2. The problem](#2-the-real-world-problem) | [7. Database](#7-database-architecture) | [12. Running tests](#12-running-tests) |
| [3. Solution](#3-solution) | [8. API](#8-api-documentation) | [13. Benchmarks](#13-benchmark-methodology-and-results) |
| [4. Architecture](#4-architecture) | [9. Security design](#9-security-design) | [14. Screenshots](#14-screenshots) |
| [5. Tech stack](#5-technology-stack) | [10. Installation](#10-installation) | [15. Future work](#15-future-enhancements) |

---

## 1. Project overview

SentinelFlow is a production-style platform that sits between a company's onboarding flow and its
identity-verification vendors. A verification request goes through eight stages:

1. **Validated** against strict schemas, with an idempotency key so retries never double-submit.
2. **Checked** by the right providers **concurrently**, with timeouts, retries, a circuit breaker and a tenant-scoped cache.
3. **Normalized**: three different vendor response formats become one shape.
4. **Interpreted** into signals: missing fields, data inconsistencies, conflicting results.
5. **Scored** on a transparent 0–100 risk scale, with every contributing factor itemized.
6. **Evaluated** against versioned, prioritized policies, recording each condition's expected and actual value.
7. **Decided**: approved, rejected, or routed to a **compliance case** with an SLA.
8. **Recorded** in one database transaction, plus audit events the database itself refuses to alter.

An optional **AI analyst assistant** summarizes cases from redacted, structured evidence. It must cite
evidence for every claim and **can never approve or reject anyone**.

```mermaid
mindmap
  root((SentinelFlow))
    Orchestration
      3 simulated providers
      Concurrent execution
      Timeouts and retries
      Circuit breaker
      Tenant-scoped cache
      Inline or queued workers
    Decisions
      Weighted risk score 0-100
      Versioned policy engine
      AND / OR conditions
      Full decision trace
      Auto-approval guard
    Case management
      Assignment and SLAs
      Escalation
      Evidence-linked comments
      Row-locked decisions
    Security
      Multi-tenant isolation in SQL
      5 roles, 21 permissions
      Sessions, CSRF, API keys
      AES-GCM encrypted PII
      Append-only audit log
    AI assistant
      Schema-validated JSON
      Evidence citations checked
      Redaction before any LLM call
      Deterministic fallback
    Delivery
      React dashboard, 13 screens
      Docker Compose
      GitHub Actions CI
      Locust benchmarks
```

## 2. The real-world problem

Companies that onboard customers in regulated markets typically integrate several verification
vendors, and each speaks a different dialect. Teams end up with:

| Pain | What it looks like in practice |
|---|---|
| **Fragmented, inconsistent results** | `"VERIFIED"`, `{"match": true}` and a `0.93` score mean different things from different vendors |
| **Opaque decisions** | Nobody can say why a customer was rejected six months ago, or which rule version applied |
| **Fragile integrations** | One slow or failing vendor stalls onboarding or, worse, lets a customer through unchecked |
| **Over-broad access** | Analysts see full personal data by default; tenant boundaries exist only in application code |
| **Manual, untracked review** | Spreadsheets and chat threads instead of an auditable case trail |

## 3. Solution

| Problem | SentinelFlow's answer |
|---|---|
| Vendor fragmentation | A provider adapter interface and one `NormalizedResult` shape; a new vendor is one class with no changes to the core |
| Unreliable vendors | Concurrent calls, per-attempt timeouts, exponential backoff with jitter, a circuit breaker; failures route to **manual review, never to approval** |
| Opaque decisions | A deterministic policy engine with a full trace; a per-factor risk breakdown; policy versions stamped on every decision |
| Unsafe automation | A system guard: auto-approval requires **every** executed check to pass. LLM output is advisory and schema-bound |
| Data exposure | AES-GCM encryption at rest for document numbers and dates of birth; masked by default; revealing is permissioned and audited |
| Tenant leakage | Tenant ownership enforced by **composite foreign keys in PostgreSQL**, plus one tenant-scoped loader in the API |
| Untracked review | Case management with assignment, escalation, evidence-linked comments, SLAs and immutable history |

---

## 4. Architecture

SentinelFlow is a **modular monolith**: one stateless API plus a worker built from the same code, so
orchestration can scale on its own. Module boundaries are explicit, so parts can be split out later
if ever needed, without paying for microservices up front.

### 4.1 System context

```mermaid
flowchart LR
    subgraph Clients["Clients"]
        UI["React dashboard<br/>session cookie + CSRF"]
        INT["Customer backends<br/>X-API-Key"]
    end

    subgraph Edge["Edge"]
        NGINX["nginx<br/>static SPA, /api proxy,<br/>security headers"]
    end

    subgraph API["FastAPI application (stateless, N replicas)"]
        direction TB
        MW["Middleware<br/>request ID, body limit, CORS"]
        AUTH["Auth and permission layer<br/>sessions, API keys, RBAC, rate limits"]
        ROUTES["REST routes /api/v1"]
        subgraph Engine["Domain engine (no HTTP)"]
            ORCH["Orchestrator"]
            PROV["Provider adapters"]
            RISK["Risk engine"]
            POL["Policy engine"]
        end
        AI["AI analyst<br/>advisory only"]
    end

    WORKER["Workers (M replicas)<br/>queued workflows + reaper"]
    PG[("PostgreSQL 16<br/>tenant FKs, encrypted PII,<br/>append-only audit")]
    REDIS[("Redis 7<br/>queue, rate limits, cache")]
    LLM["External LLM<br/>redacted evidence,<br/>per-org opt-in"]

    UI --> NGINX
    INT --> NGINX
    NGINX --> MW --> AUTH --> ROUTES
    ROUTES --> ORCH
    ORCH --> PROV & RISK & POL
    ROUTES --> AI -. optional .-> LLM
    ROUTES -- "execution_mode=queue" --> REDIS
    REDIS --> WORKER --> ORCH
    AUTH --> REDIS
    PROV --> REDIS
    ROUTES --> PG
    ORCH --> PG
    WORKER --> PG

    classDef store fill:#173a2e,stroke:#2e9e76,color:#e6eaed
    classDef ext fill:#3a2f1a,stroke:#d4a04a,color:#e6eaed
    class PG,REDIS store
    class LLM ext
```

### 4.2 Request lifecycle

The sequence below is exactly what `POST /api/v1/verifications` does. No database connection is held
while providers are being called.

```mermaid
sequenceDiagram
    autonumber
    actor C as Client
    participant A as API
    participant R as Redis
    participant P as Providers
    participant DB as PostgreSQL

    C->>A: POST /verifications (Idempotency-Key)
    A->>R: rate-limit check (per principal)
    A->>DB: idempotency lookup
    alt key already used
        DB-->>A: existing request
        A-->>C: 200 replay, or 409 if the body differs
    end
    A->>DB: upsert customer (PII encrypted), insert request, audit [COMMIT]
    A->>DB: load request, customer, org, policies, provider config, history [COMMIT, release]
    par identity or business registry
        A->>P: call with timeout, retry, circuit breaker
        P-->>A: vendor-specific response
    and watchlist screening
        A->>R: tenant-scoped cache lookup
        alt cache miss
            A->>P: call with timeout, retry, circuit breaker
            A->>R: cache response (1 h)
        end
    end
    A->>A: normalize, derive signals, score risk, evaluate policies, system guard
    A->>DB: provider results, risk, decision, case, workflow trace, audit [ONE TRANSACTION]
    A-->>C: 201 decision + full evidence
```

### 4.3 How a decision is made

```mermaid
flowchart TD
    IN([Verification request]) --> V{Schema valid?}
    V -- no --> E422[/422 validation_error/]
    V -- yes --> SEL[Select workflow<br/>individual: identity + watchlist<br/>business: registry + watchlist]
    SEL --> REQ{Provider's required<br/>fields present?}
    REQ -- no --> INS[Result: error<br/>insufficient_input<br/>no call made]
    REQ -- yes --> CALL[Call provider<br/>timeout, retry, breaker]
    CALL --> NORM[Normalize to<br/>pass / fail / review / error]
    INS --> SIG
    NORM --> SIG[Derive signals<br/>missing fields, inconsistencies,<br/>conflicts, provider errors, history]
    SIG --> RISK[Risk score 0-100<br/>+ per-factor breakdown<br/>+ documented floors]
    RISK --> POL[Evaluate active policies<br/>in priority order]
    POL --> M{First terminal<br/>policy matched?}
    M -- yes --> ACT[Its action]
    M -- no --> FB[Risk-band default<br/>low: approve, medium: review,<br/>high or critical: escalate]
    ACT --> G{Action is approve?}
    FB --> G
    G -- "yes, but a check<br/>didn't pass" --> GUARD[System guard:<br/>downgrade to manual review]
    G -- "yes, all passed" --> APP([Approved])
    G -- no --> D{Action}
    GUARD --> CASE
    D -- reject --> REJ([Rejected])
    D -- manual_review --> CASE([Case opened<br/>normal or high priority])
    D -- escalate --> ESC([Escalated case<br/>high or critical priority])

    classDef good fill:#173a2e,stroke:#2e9e76,color:#e6eaed
    classDef bad fill:#3a1f1d,stroke:#c46a62,color:#e6eaed
    classDef warn fill:#3a2f1a,stroke:#d4a04a,color:#e6eaed
    class APP good
    class REJ,E422 bad
    class CASE,ESC,GUARD warn
```

<details>
<summary><b>▸ Provider resilience: timeouts, retries and the circuit breaker</b></summary>

<br>

Each provider call runs this loop (`engine/providers.py`, `execute_with_resilience`). Retryable
failures (timeouts, outages) back off exponentially (50 ms × 2ⁿ, capped at 1 s, plus jitter).
Permanent failures (bad request) are not retried. Five consecutive failures open a per-organization
breaker for 30 seconds; during that window calls fail fast as `circuit_open`, and policy routes the
verification to manual review.

```mermaid
flowchart TD
    S([Provider call]) --> B{Breaker open?}
    B -- yes --> CO[/error: circuit_open/]
    B -- no --> T[Attempt n<br/>asyncio.wait_for timeout]
    T --> R{Result}
    R -- success --> OK[Breaker reset] --> DONE([Raw response])
    R -- "bad request" --> NR[Record failure] --> PERM[/error: bad_request<br/>no retry/]
    R -- "timeout / unavailable" --> F[Record failure]
    F --> L{Retries left and<br/>breaker still closed?}
    L -- yes --> W[Backoff<br/>50ms x 2^n-1 + jitter] --> T
    L -- no --> ERR[/error: timeout or unavailable/]
```

```mermaid
stateDiagram-v2
    [*] --> Closed
    Closed --> Closed: success resets the count
    Closed --> Open: 5 consecutive failures
    Open --> HalfOpen: 30 s cooldown elapsed
    HalfOpen --> Closed: trial call succeeds
    HalfOpen --> Open: trial call fails
```

</details>

<details>
<summary><b>▸ Policy evaluation semantics</b></summary>

<br>

Policies are JSON documents with nested `all` / `any` groups (max depth 6) and 11 operators:
`eq, neq, gt, gte, lt, lte, in, not_in, contains, exists, missing`. They are validated on save
(operator whitelist, field-path pattern, value types) and stored as **immutable versions**.

```mermaid
flowchart LR
    A[Active policies] --> S[Sort by priority, then name]
    S --> N{Next policy}
    N --> C[Evaluate every condition<br/>record expected vs actual]
    C --> MT{Matched?}
    MT -- no --> T1[Trace: not matched] --> N
    MT -- yes --> K{Action}
    K -- flag --> FL[Add flag, keep going] --> N
    K -- "terminal and no<br/>decision yet" --> DEC[This policy decides]
    K -- "terminal, already<br/>decided" --> SUP[Trace: matched, superseded] --> N
    DEC --> N
    N -- "none left" --> OUT([Decision + complete trace])
```

The eight starter policies created for every new organization:

| Priority | Policy | Action |
|---:|---|---|
| 10 | Reject missing mandatory fields | Reject |
| 20 | Block restricted jurisdictions (example config) | Reject |
| 30 | Escalate strong watchlist match | Escalate |
| 40 | Escalate critical risk (score ≥ 75) | Escalate |
| 50 | Reject failed document authenticity | Reject |
| 60 | Manual review for uncertain or conflicting results | Manual review |
| 70 | Review mismatched identity information | Manual review |
| 90 | Flag missing contact email | Flag (non-terminal) |

Example definition:

```json
{
  "when": { "all": [
    { "field": "providers.watchlist.outcome", "op": "eq", "value": "fail" },
    { "field": "providers.watchlist.confidence", "op": "gte", "value": 0.9 }
  ]},
  "action": "escalate",
  "reason": "Strong watchlist match requires senior review"
}
```

</details>

<details>
<summary><b>▸ Risk scoring model</b></summary>

<br>

```
score = 100 × Σ(weightᵢ × severityᵢ) / Σ(weightᵢ over factors that applied)
```

A check that didn't run (for example business registry for an individual) is excluded from both
sums, so scores stay comparable across verification types. Because a weighted average can dilute one
decisive red flag, documented **floors** apply on top, and they appear in the breakdown.

```mermaid
flowchart LR
    subgraph Factors["Factor (default weight) → severity 0..1"]
        I["Identity (30)<br/>pass 0, review 0.5,<br/>error 0.6, fail 1"]
        B["Business (25)<br/>same scale"]
        W["Watchlist (35)<br/>match confidence"]
        M["Missing info (10)<br/>share of expected fields"]
        X["Inconsistencies (15)<br/>count / 2, max 1"]
        H["History (10)<br/>prior rejections / 3"]
    end
    Factors --> WA[Weighted average<br/>0-100]
    WA --> FLOOR{Floors}
    FLOOR -- "strong watchlist match" --> F80[min 80]
    FLOOR -- "identity or registry failed" --> F55[min 55]
    FLOOR -- none --> KEEP[score unchanged]
    F80 & F55 & KEEP --> CAT{Category}
    CAT --> LOW[Low < 25] & MED[Medium 25-49] & HIGH[High 50-74] & CRIT[Critical ≥ 75]
```

Weights and thresholds are configurable per organization in **Settings**.

</details>

<details>
<summary><b>▸ Lifecycles: verification requests and compliance cases</b></summary>

<br>

```mermaid
stateDiagram-v2
    direction LR
    [*] --> processing: inline submit
    [*] --> queued: execution_mode=queue
    queued --> processing: worker claims atomically
    processing --> completed: decision stored
    processing --> failed: unexpected error, audited
    queued --> queued: reaper re-queues if stale > 5 min
    processing --> queued: reaper re-queues if stale > 5 min
    completed --> [*]
    failed --> [*]
```

```mermaid
stateDiagram-v2
    [*] --> open: policy routes to manual review
    [*] --> escalated: policy escalates
    open --> in_review: assigned to a reviewer
    open --> escalated: escalate
    in_review --> escalated: escalate (unassigns, raises priority)
    open --> approved: decide
    open --> rejected: decide
    in_review --> approved: assignee decides
    in_review --> rejected: assignee decides
    escalated --> approved: analyst or admin decides
    escalated --> rejected: analyst or admin decides
    approved --> [*]
    rejected --> [*]
```

Decisions take a row lock on the case, so two reviewers deciding at once produce exactly one `200`
and one `409`.

</details>

<details>
<summary><b>▸ Provider adapter interface (how to plug in a real vendor)</b></summary>

<br>

```mermaid
classDiagram
    class ProviderAdapter {
        <<abstract>>
        +key: str
        +check_type: str
        +required_fields: tuple
        +build_request(subject) dict
        +call(payload, sim) dict
        +normalize(raw, subject) NormalizedResult
        +cache_key(payload) str
    }
    class NormalizedResult {
        +provider_key: str
        +check_type: str
        +outcome: str
        +confidence: float
        +signals: dict
        +reasons: list
    }
    class IdentityVerificationProvider {
        key = sim_idv
    }
    class BusinessVerificationProvider {
        key = sim_kyb
    }
    class WatchlistScreeningProvider {
        key = sim_watchlist
    }
    class CircuitBreaker {
        +state: str
        +allow() bool
        +record(ok)
    }
    ProviderAdapter <|-- IdentityVerificationProvider
    ProviderAdapter <|-- BusinessVerificationProvider
    ProviderAdapter <|-- WatchlistScreeningProvider
    ProviderAdapter ..> NormalizedResult : produces
    CircuitBreaker ..> ProviderAdapter : guards
```

To add a real vendor, implement `ProviderAdapter`, raise `ProviderUnavailable` for retryable errors
and `ProviderBadRequest` for permanent ones, and register it. Timeouts, retries, circuit breaking,
caching, risk and policy need no changes.

</details>

### 4.4 Code layout

```
backend/app/
├── api/routes/     HTTP only: validation, permissions, response shaping
├── api/deps.py     authentication, require(permission), tenant-scoped get_owned(), pagination
├── engine/         orchestrator, provider adapters + resilience, policy engine, risk engine
├── security/       permission matrix, argon2id, AES-GCM, masking
├── services/       audit, cache/queue, case decisions, AI analyst, org provisioning
├── models.py       SQLAlchemy models with tenant-ownership constraints
└── worker.py       queue consumer + stale-request reaper
frontend/src/
├── pages/          13 screens
├── components/     shared UI primitives and domain components
└── lib/            API client (CSRF), auth context, formatting, types
benchmarks/         Locust load profile + reproducible benchmark runner
docs/               screenshots, coverage report, simulation reference, deployment guide
```

## 5. Technology stack

| Layer | Choice |
|---|---|
| API | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic, psycopg 3 |
| Data | PostgreSQL 16, Redis 7 |
| Security | argon2id, AES-256-GCM (`cryptography`), SHA-256 token hashing |
| Async execution | asyncio inline, or Redis-backed workers with atomic claims and a reaper |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS v4, shadcn/ui-style components on Radix, Framer Motion, Recharts, TanStack Query, React Hook Form + Zod |
| Testing | Pytest (real Postgres + Redis), Vitest + Testing Library, Playwright |
| Load testing | Locust, psutil, matplotlib |
| Delivery | Docker, Docker Compose, nginx, GitHub Actions |

Celery was deliberately not used. The workload needs one queue with at-least-once delivery and
idempotent claims, which about 70 lines of `worker.py` provide.

---

## 6. Key features

<details open>
<summary><b>▸ Identity and organization management</b></summary>

- Registration creates an isolated organization with eight starter policies and provider configs.
- Five roles: **Super Admin, Organization Admin, Compliance Analyst, Reviewer, Read-only Auditor**.
- Users can belong to several organizations and switch between them.
- Sessions can be listed and revoked; accounts lock after repeated failed logins.
- API keys support scopes, expiry, rotation (which revokes the old key) and revocation.
- A user activity trail lives in the audit log.

</details>

<details>
<summary><b>▸ Verification orchestration</b></summary>

- **Individual** workflow: identity verification + watchlist screening. **Business** workflow: registry lookup + watchlist screening.
- **Inline** mode returns the decision in the response; **queue** mode returns `202` and workers process it.
- Idempotency keys are race-safe: concurrent duplicates create exactly one request; reusing a key with a different body returns `409`.
- Each simulated provider can be configured for latency, outage rate and timeout rate to exercise the resilience path ([simulation reference](docs/simulation.md)).

</details>

<details>
<summary><b>▸ Policy engine, risk engine and case management</b></summary>

- Policies: AND/OR groups, 11 operators, priorities, non-terminal flags, activation, immutable versions with one-click restore, and dry-run simulation against any stored verification.
- Risk: six factors, per-org weights and thresholds, floors, and a full breakdown on every assessment.
- Cases: assignment limited to eligible members of the same organization, SLAs by priority, escalation, evidence-linked comments, history and row-locked decisions.

</details>

<details>
<summary><b>▸ AI analyst assistant (optional)</b></summary>

```mermaid
flowchart LR
    CASE[Case] --> EV[Build evidence<br/>provider results, risk factors,<br/>matched policies, signals]
    EV --> RED[Redact<br/>no names, DOB, document<br/>numbers or emails]
    RED --> Q{LLM key set AND<br/>org opted in?}
    Q -- no --> DET[Deterministic summary<br/>from the same evidence]
    Q -- yes --> LLM[LLM call<br/>strict JSON schema]
    LLM --> VAL{Valid JSON<br/>for the schema?}
    VAL -- no --> DET
    VAL -- yes --> CITE[Drop claims citing<br/>unknown evidence IDs]
    CITE --> STRIP[Strip approve or<br/>reject language]
    STRIP --> OUT([Advisory analysis<br/>+ warnings + evidence list])
    DET --> OUT
```

Outputs: case summary, risk-signal explanations, inconsistencies, questions for the reviewer, draft
report, confidence. The output schema has no decision field, and nothing it returns is read by the
policy engine or case workflow.

</details>

<details>
<summary><b>▸ Frontend: 13 screens</b></summary>

Login and organization onboarding, executive overview, verification requests, verification detail
(execution waterfall + policy chain), customer profile, risk workspace, policy studio (visual
condition builder, versions, test runs), cases, case workspace, provider integrations, members and
permission matrix, API keys, audit log, settings. Every chart reads backend data. Every screen has
loading, empty and error states; consequential actions need confirmation; layouts are responsive and
keyboard-accessible.

</details>

---

## 7. Database architecture

19 tables. Every tenant-owned table carries `org_id`, and child tables reference their parent through
a **composite `(org_id, id)` foreign key**, so a row physically cannot point at another tenant's
parent, whatever the application code does. A case's assignee must be a member of the **same**
organization.

```mermaid
erDiagram
    organizations ||--o{ organization_memberships : has
    users ||--o{ organization_memberships : joins
    roles ||--o{ organization_memberships : grants
    roles ||--o{ role_permissions : maps
    permissions ||--o{ role_permissions : maps
    users ||--o{ user_sessions : opens
    organizations ||--o{ api_keys : issues
    organizations ||--o{ customers : owns
    customers ||--o{ verification_requests : "org_id, customer_id"
    verification_requests ||--o{ provider_results : "org_id, verification_id"
    verification_requests ||--|| risk_assessments : scored_by
    verification_requests ||--o{ workflow_executions : traced_by
    verification_requests ||--o| compliance_cases : escalates_to
    compliance_cases ||--o{ case_comments : "org_id, case_id"
    organization_memberships ||--o{ compliance_cases : "assignee org_id, user_id"
    organizations ||--o{ policies : defines
    policies ||--o{ policy_versions : "org_id, policy_id"
    organizations ||--o{ provider_configs : configures
    organizations ||--o{ audit_events : append_only

    customers {
        uuid id PK
        uuid org_id FK
        string external_ref "unique per org"
        bytes dob_enc "AES-GCM"
        bytes document_number_enc "AES-GCM"
        string document_last4
    }
    verification_requests {
        uuid id PK
        uuid org_id FK
        uuid customer_id FK
        string status
        string decision
        string idempotency_key "unique per org"
        jsonb policy_trace
    }
    audit_events {
        bigint id PK
        uuid org_id FK
        string action
        string request_id
        jsonb details
    }
```

| Concern | Implementation |
|---|---|
| Integrity | Unique `(org_id, external_ref)`; partial unique index on `(org_id, idempotency_key)`; one risk assessment and one case per verification; unique `(policy_id, version)` |
| Indexes | Match the access paths: tenant + time for lists, tenant + status/decision/category filters, case SLA queue, risk by score, audit by resource, actor and action. `alembic check` in the test suite guards against model/migration drift |
| Immutability | Migration `0002` installs triggers rejecting `UPDATE`, `DELETE` and `TRUNCATE` on `audit_events`, and the tests prove it |
| PII at rest | `dob_enc` and `document_number_enc` are AES-256-GCM ciphertext; only the document's last 4 digits are in clear. Provider payloads are scrubbed of names and DOB before storage |
| Transactions | Submission, final results and case decisions each commit atomically with their audit events |
| Migrations | `backend/alembic/versions/0001…0003`; downgrade and upgrade round-trip cleanly |

---

## 8. API documentation

Interactive OpenAPI docs are served at **`/docs`** (Swagger UI) and **`/openapi.json`**. All
endpoints are versioned under `/api/v1`.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login` (cookie), `POST /auth/token` (bearer), `POST /auth/logout`, `GET /auth/me`, `POST /auth/switch-org`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}` |
| Verifications | `POST /verifications`, `GET /verifications`, `GET /verifications/{id}`, `POST /verifications/{id}/review` |
| Customers | `GET /customers`, `GET /customers/{id}?reveal=true` |
| Risk | `GET /risk-assessments`, `GET /risk-assessments/summary`, `GET /risk-assessments/{id}` |
| Policies | `GET/POST /policies`, `GET/PATCH /policies/{id}`, `POST /policies/{id}/activate`, `POST /policies/{id}/deactivate`, `POST /policies/{id}/versions/{v}/restore`, `POST /policies/simulate` |
| Cases | `GET /cases`, `GET /cases/{id}`, `POST /cases/{id}/assign`, `POST /cases/{id}/comments`, `POST /cases/{id}/escalate`, `POST /cases/{id}/decision`, `POST /cases/{id}/ai-analysis` |
| Providers | `GET /providers`, `PATCH /providers/{key}`, `POST /providers/{provider_id}/test` |
| Administration | `GET /roles`, `GET/POST /users`, `PATCH /users/{id}/role`, `DELETE /users/{id}`, `GET/POST /api-keys`, `POST /api-keys/{id}/revoke`, `POST /api-keys/{id}/rotate`, `GET/PATCH /settings`, `GET /system/metrics` |
| Insights | `GET /dashboard/overview?days=14`, `GET /audit-events` |
| Platform | `GET /admin/organizations`, `PATCH /admin/organizations/{id}` (metadata only, never tenant data) |
| Health | `GET /health/live`, `GET /health/ready` |

**Conventions.** Lists accept `page`, `page_size` (≤ 200), `sort` (whitelisted fields; `-` for
descending) and endpoint-specific filters. Every error uses one shape:

```json
{ "error": { "code": "idempotency_conflict",
             "message": "Idempotency-Key was already used with a different request body",
             "details": { "verification_id": "…" }, "request_id": "5f0c…" } }
```

<details>
<summary><b>▸ Example: submit a verification with an API key</b></summary>

```bash
curl -X POST http://localhost:8080/api/v1/verifications \
  -H "X-API-Key: $SF_API_KEY" -H "Idempotency-Key: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{
        "external_ref": "cust-1042",
        "verification_type": "individual",
        "subject": { "full_name": "Amara Okafor", "dob": "1990-04-12", "country": "GB",
                     "document_type": "passport", "document_number": "P12345678",
                     "email": "amara@example.com" }
      }'
```

The response contains:

- the decision and the masked customer;
- normalized provider results, with attempts, latency and cache hits;
- the risk breakdown;
- the policy trace, with every condition's expected and actual values;
- workflow step timings, and the case if one was opened.

Synthetic "magic values" that trigger each outcome (for example, a document number ending `0000`
fails authenticity, and the name `Viktor Sanctionov` is a strong watchlist match) are listed in
[`docs/simulation.md`](docs/simulation.md).

</details>

---

## 9. Security design

Every request passes through the same gates, in this order:

```mermaid
flowchart TD
    R([Incoming request]) --> SZ{Body ≤ 256 KB?}
    SZ -- no --> X413[/413/]
    SZ -- yes --> K{Credential type}
    K -- "X-API-Key" --> AK{Key hash found,<br/>not revoked or expired?}
    K -- "Bearer or cookie" --> SS{Session hash found,<br/>not expired or revoked,<br/>user active, still a member?}
    K -- none --> X401[/401/]
    AK -- no --> X401
    SS -- no --> X401
    AK -- yes --> ORG{Organization active?}
    SS -- yes --> CS{Cookie auth and<br/>unsafe method?}
    CS -- yes --> CSRF{X-CSRF-Token matches<br/>the session secret?}
    CSRF -- no --> X403C[/403 csrf_failed/]
    CSRF -- yes --> ORG
    CS -- no --> ORG
    ORG -- suspended --> X403S[/403/]
    ORG -- active --> RL{Within per-principal<br/>rate limit?}
    RL -- no --> X429[/429 + Retry-After/]
    RL -- yes --> PERM{"require(permission)"}
    PERM -- missing --> AUD[Audit authz.denied] --> X403[/403/]
    PERM -- granted --> TEN{"get_owned(): row's org_id<br/>= caller's org_id?"}
    TEN -- no --> X404[/"404 (same as missing,<br/>so IDs can't be probed)"/]
    TEN -- yes --> RES{Resource rule<br/>e.g. reviewer is assignee?}
    RES -- no --> X403R[/403/]
    RES -- yes --> OK([Handler runs])

    classDef deny fill:#3a1f1d,stroke:#c46a62,color:#e6eaed
    classDef allow fill:#173a2e,stroke:#2e9e76,color:#e6eaed
    class X413,X401,X403C,X403S,X429,X403,X404,X403R deny
    class OK allow
```

<details>
<summary><b>▸ Role → permission matrix</b></summary>

| Permission | Super Admin | Org Admin | Compliance Analyst | Reviewer | Auditor |
|---|:-:|:-:|:-:|:-:|:-:|
| Submit verifications | ✓ | ✓ | ✓ | | |
| View verifications, customers (masked), cases, policies | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reveal unmasked PII (audited) | ✓ | ✓ | ✓ | ✓ | |
| Comment on cases, decide **assigned** cases | ✓ | ✓ | ✓ | ✓ | |
| Assign cases, decide **escalated / any** case | ✓ | ✓ | ✓ | | |
| Create and version policies | ✓ | ✓ | ✓ | | |
| Configure and test providers | ✓ | ✓ | | | |
| Manage members, API keys, settings | ✓ | ✓ | | | |
| Read audit log | ✓ | ✓ | | | ✓ |
| Use AI assistant | ✓ | ✓ | ✓ | ✓ | |
| Manage organizations platform-wide (no tenant data) | ✓ | | | | |

API keys can carry only a machine-safe subset (`verifications:create/read`, `customers:read`,
`cases:read`, `policies:read`, `audit:read`), and never more than their creator holds.

</details>

| Control | Implementation |
|---|---|
| Centralized authorization | `require("perm")` on every route; one role → permission matrix mirrored into the DB; denials are audited |
| Tenant isolation / IDOR | `get_owned()` always filters by the caller's `org_id`; other tenants' IDs return **404**; composite FKs make cross-tenant references impossible in SQL; caches are keyed by tenant |
| Privilege escalation | No one grants or changes a role above their own; only super admins create super admins; no self role changes; last-admin protection under row locks |
| Sessions | Opaque 256-bit tokens, only SHA-256 stored; HttpOnly + SameSite=Lax cookie; expiry, logout, per-session revocation; removing a membership invalidates sessions immediately |
| CSRF | Cookie-authenticated writes need `X-CSRF-Token` matching a per-session secret; bearer and API-key calls are exempt (browsers don't attach them automatically) |
| Passwords | argon2id; 12+ characters with letters and digits; constant-time path for unknown emails (no user enumeration); lockout after 5 failures |
| Input validation | Pydantic with `extra="forbid"`; regex-constrained identifiers; 256 KB body limit; validated policy definitions |
| SQL injection | Parameterized SQL only; whitelisted sort fields; escaped `ILIKE` wildcards; tested with injection payloads |
| Rate limiting | Per user / API key, and per IP + email on login, in Redis with `Retry-After` |
| PII protection | AES-GCM at rest; masked by default with fixed-width masks (no length leakage); audited reveal; scrubbed provider payloads |
| Audit | Append-only (DB triggers); request ID, IP, actor and details on every security-relevant action |
| Headers / CORS | CSP, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS when secure; explicit origin allowlist; sanitized request IDs |
| Secrets | Environment variables only; `.env` git-ignored; production refuses to start without an encryption key |

**37 dedicated security tests** (`backend/tests/test_security.py`) cover:

- cross-tenant reads and writes on every resource type, and DB-level foreign-key rejection;
- the role matrix and escalation attempts;
- revoked, rotated and expired API keys, and forged or malformed tokens;
- CSRF, cookie flags, user enumeration, lockout and both rate limiters;
- PII masking, assignment-based decisions, concurrent decisions and audit immutability;
- suspended organizations, security headers, CORS and injection payloads.

---

## 10. Installation

```mermaid
flowchart LR
    A["git clone"] --> B["cp .env.example .env<br/>set POSTGRES_PASSWORD"]
    B --> C["docker compose up -d --build"]
    C --> D{Want demo data?}
    D -- yes --> E["docker compose --profile demo<br/>run --rm seed"]
    D -- no --> F
    E --> F(["Open http://localhost:8080"])
```

### Option A: Docker Compose (recommended)

```bash
git clone https://github.com/deebak4064/SentinelFlow-Multi-Tenant-Identity-Verification-and-Compliance-Orchestration-Platform.git sentinelflow
cd sentinelflow
cp .env.example .env            # set POSTGRES_PASSWORD (and SF_FIELD_ENCRYPTION_KEY)
docker compose up -d --build    # postgres, redis, migrate, api, worker, web
docker compose --profile demo run --rm seed   # optional: 400 synthetic verifications + demo users
# App: http://localhost:8080    API docs: http://localhost:8080/docs
```

Scale orchestration workers with `WORKER_REPLICAS=3 docker compose up -d worker`.

<details>
<summary><b>▸ Deployment topology (Docker Compose)</b></summary>

```mermaid
flowchart TB
    subgraph host["Docker host"]
        web["web<br/>nginx :8080<br/>(only published port)"]
        api["api<br/>uvicorn, 2 workers"]
        worker["worker x WORKER_REPLICAS"]
        migrate["migrate<br/>alembic upgrade head<br/>(one-shot)"]
        seed["seed<br/>(profile: demo)"]
        pg[("postgres:16<br/>volume pgdata")]
        rd[("redis:7<br/>volume redisdata")]
    end
    user((Browser / API client)) --> web
    web -- "/api, /docs, /health" --> api
    api --> pg & rd
    worker --> pg & rd
    migrate --> pg
    seed --> pg
    migrate -. "must finish before" .-> api & worker
```

The API isn't published directly. It's reachable only through nginx, which makes trusting
`X-Forwarded-For` safe. Production guidance is in [`docs/deployment.md`](docs/deployment.md).

</details>

### Option B: Local development

Requirements: Python 3.13, Node 22, PostgreSQL 16, Redis 7.

```bash
# database
createuser -s sentinel && psql -c "ALTER USER sentinel PASSWORD 'sentinel'"
createdb -O sentinel sentinelflow && createdb -O sentinel sentinelflow_test

# backend
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python -m app.seed                     # optional demo data
uvicorn app.main:app --reload --port 8000
python -m app.worker                   # separate terminal; only for execution_mode=queue

# frontend
cd ../frontend
npm ci
npm run dev                            # http://localhost:5173 (proxies /api to :8000)
```

**Demo accounts** (seed data only, password `SentinelDemo2026!`):

| Email | Role |
|---|---|
| `org.admin@northwind.example` | Organization Admin |
| `compliance.analyst@northwind.example` | Compliance Analyst |
| `reviewer@northwind.example` | Reviewer |
| `auditor@northwind.example` | Read-only Auditor |
| `platform@sentinelflow.example` | Super Admin |
| `admin@contoso.example` | Admin of a second tenant (try cross-tenant access) |

## 11. Environment configuration

All settings are environment variables prefixed `SF_` (see `backend/app/config.py` and `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `SF_ENV` | `development` | `production` enforces an encryption key |
| `SF_DATABASE_URL` | local Postgres | SQLAlchemy URL (`postgresql+psycopg://…`) |
| `SF_REDIS_URL` | `redis://localhost:6379/0` | Queue, rate limits, cache |
| `SF_FIELD_ENCRYPTION_KEY` | dev-only derived key | 32-byte urlsafe-base64 AES key for PII at rest |
| `SF_COOKIE_SECURE` | `false` | Secure cookies + HSTS (set `true` behind HTTPS) |
| `SF_CORS_ORIGINS` | Vite dev origins | JSON list of allowed origins |
| `SF_SESSION_TTL_MINUTES` | `480` | Absolute session lifetime |
| `SF_RATE_LIMIT_PER_MINUTE` | `600` | Per user / API key |
| `SF_LOGIN_RATE_LIMIT_PER_MINUTE` | `10` | Per IP and per email |
| `SF_MAX_FAILED_LOGINS` / `SF_LOCKOUT_MINUTES` | `5` / `15` | Account lockout |
| `SF_DEFAULT_EXECUTION_MODE` | `inline` | `inline` or `queue` (orgs can override) |
| `SF_DB_POOL_SIZE` / `SF_DB_MAX_OVERFLOW` | `20` / `20` | Connection pool per process |
| `SF_LLM_API_KEY`, `SF_LLM_MODEL`, `SF_LLM_BASE_URL` | empty, `claude-sonnet-5-5`, Anthropic API | Optional external LLM for the analyst assistant |
| `VITE_SHOW_DEMO_ACCOUNTS` (frontend build) | `true` | Hide demo accounts on the login page in production |

---

## 12. Running tests

```bash
cd backend  && pytest --cov=app               # 147 tests, real PostgreSQL + Redis
cd frontend && npm test                       # 12 component tests
cd frontend && npx playwright test            # 8 end-to-end tests against a running stack
```

| Suite | Result (last run, executed) |
|---|---|
| Backend (pytest) | **147 passed**, line coverage **94.2%** ([HTML report](docs/coverage/index.html)) |
| Frontend (Vitest) | **12 passed** |
| End-to-end (Playwright, Chromium) | **8 passed** |
| `alembic check` (models vs migrations) | no drift |
| Lint (ruff), typecheck (tsc), production build | clean |

<details>
<summary><b>▸ What the tests cover</b></summary>

- **Policy engine:** every operator, AND/OR nesting, priority, flags, risk-band fallback, the system guard, determinism, definition validation and the depth limit.
- **Risk engine:** weights, factor exclusion, floors, category boundaries, score bounds.
- **Providers:** every simulated outcome, timeout, outage, transient recovery, non-retryable errors, and the breaker's open, half-open and closed states.
- **Workflows:** approve, reject, escalate, manual review, conflicting responses, missing fields, disabled provider, idempotent replay and conflict, concurrent duplicates, queue mode, PII never stored in clear.
- **Security:** the 37-test suite described in §9.
- **Cases, policies, AI:** case lifecycle, policy versioning and simulation, AI fallback, hallucinated-citation and decision-language guards, redaction before LLM calls.
- **Operations:** dashboard figures checked against SQL, the worker reaper, Redis outage behavior, session management.

</details>

### Continuous integration

```mermaid
flowchart LR
    P([push / pull request]) --> BE["backend<br/>ruff + pytest<br/>coverage gate 90%"]
    P --> FE["frontend<br/>typecheck + vitest + build"]
    BE --> E2E["e2e<br/>seeded stack + Playwright"]
    FE --> E2E
    BE --> DK["docker<br/>build all images, compose up,<br/>smoke-test inline + queued<br/>verifications through nginx"]
    FE --> DK
```

---

## 13. Benchmark methodology and results

Reproduce with `python benchmarks/run_benchmarks.py --levels 10 50 100 200 --duration 60`.

The suite does five things:

1. Starts its own API and creates a fresh organization with **provider chaos on** (5% outages, 1% timeouts, 40 ms ±50% simulated latency).
2. Drives Locust with API-key clients, using the traffic mix below.
3. Resets statistics after ramp-up and measures each level over 60 s of steady state.
4. Samples CPU and memory.
5. Derives recovery and latency figures from stored rows, micro-benchmarks the policy engine, and times representative queries.

```mermaid
pie showData title Load-test traffic mix (%)
    "POST /verifications (full workflow)" : 60
    "GET /verifications (list)" : 20
    "GET /verifications/{id}" : 15
    "GET /dashboard/overview" : 5
```

> [!NOTE]
> **These are local measurements, not production capacity.** API, PostgreSQL, Redis and the load
> generator all shared one sandbox VM with **1.9 effective CPU cores** (measured by a parallel
> CPU-bound probe). Throughput plateaus once that budget is saturated; past that point, more
> clients only add queueing latency.

```mermaid
xychart-beta
    title "Throughput by concurrency (req/s)"
    x-axis "Concurrent clients" [10, 50, 100, 200]
    y-axis "Requests per second" 0 --> 90
    bar [43.7, 77.5, 70.3, 70.3]
    line [27.1, 45.8, 41.8, 41.8]
```

<sub>Bars: all requests per second. Line: verification submissions per second.</sub>

| Clients | Total req/s | Submissions/s | Submit p50 | Submit p95 | Submit p99 | Errors |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 43.7 | 27.1 | 100 ms | 170 ms | 450 ms | 0 / 2,623 |
| 50 | 77.5 | 45.8 | 650 ms | 940 ms | 1,100 ms | 0 / 4,659 |
| 100 | 70.3 | 41.8 | 1,700 ms | 2,300 ms | 2,600 ms | 0 / 4,276 |
| 200 | 70.3 | 41.8 | 3,500 ms | 6,200 ms | 7,600 ms | 0 / 4,277 |

| Metric (measured) | Value |
|---|---|
| API error rate under load | **0 failed of 15,835 requests** across all four levels |
| Provider failure recovery rate | **100%**: all 681 provider calls that hit a simulated outage or timeout recovered on retry (of 20,430 calls) |
| Policy evaluation | 0.09 ms p50 inside live workflows; **16 µs** per 8-policy evaluation in isolation |
| Watchlist cache hit rate | **80.6%** (400-identity pool, tenant-scoped, 1 h TTL); dashboard cache 96% |
| DB query latency (idle, 47k rows) | lookup by id 0.7 ms p50; list page with joins 4.6 ms; detail children 1.1 ms; dashboard aggregates 18.4 ms |
| Memory | API ≈ 205–232 MB (PSS) for 2 worker processes |

![Benchmark charts](benchmarks/performance_charts.png)

Full outputs: [`benchmark_results.json`](benchmarks/benchmark_results.json),
[`benchmark_report.md`](benchmarks/benchmark_report.md),
[`performance_charts.png`](benchmarks/performance_charts.png).

<details>
<summary><b>▸ How the bottleneck was diagnosed</b></summary>

```mermaid
flowchart TD
    A["Throughput plateaus at ~80 req/s"] --> B["py-spy: one worker is CPU-bound<br/>in SQLAlchemy + psycopg"]
    B --> C["Add workers 1 → 8"]
    C --> D{"Throughput rises?"}
    D -- no --> E["Postgres sessions mostly<br/>'idle in transaction', waiting on the client"]
    E --> F["Rule out: pool size, WAL fsync,<br/>event-loop blocking, load generator"]
    F --> G["In-process load without HTTP<br/>hits the same ceiling"]
    G --> H["Parallel CPU probe:<br/>only ~1.9 effective cores"]
    H --> I(["Conclusion: host CPU limit,<br/>not application contention"])
```

Along the way the investigation removed per-checkout `pool_pre_ping` round trips and collapsed
query counts (workflow context 3 → 1, detail endpoint 6 → 3). These are sound changes, but on this
CPU-bound host they made no measurable throughput difference (83.3 req/s before, 81.3 after at 50
clients, within noise). The baseline is kept in `benchmarks/baseline/`.

</details>

---

## 14. Screenshots

| | |
|:-:|:-:|
| ![Login](docs/screenshots/00-login.png) **Login and onboarding** | ![Overview](docs/screenshots/01-overview.png) **Executive overview** |
| ![Verifications](docs/screenshots/02-verifications.png) **Verification requests** | ![Customer](docs/screenshots/04-customer-profile.png) **Customer profile (masked PII)** |
| ![Risk](docs/screenshots/05-risk-workspace.png) **Risk workspace** | ![Policies](docs/screenshots/06-policy-studio.png) **Policy configuration studio** |
| ![Cases](docs/screenshots/07-cases.png) **Case queue** | ![Case](docs/screenshots/08-case-detail.png) **Case workspace + analyst assistant** |
| ![Providers](docs/screenshots/09-providers.png) **Provider integrations** | ![Members](docs/screenshots/10-members-roles.png) **Members and permission matrix** |
| ![API keys](docs/screenshots/11-api-keys.png) **API keys** | ![Audit](docs/screenshots/12-audit-log.png) **Audit log explorer** |
| ![Settings](docs/screenshots/13-settings.png) **System settings** | ![Mobile](docs/screenshots/14-mobile.png) **Mobile layout** |

---

## 15. Future enhancements

- **PostgreSQL row-level security** as a second isolation layer under the composite FKs.
- **Hash-chained audit log** streamed to write-once storage.
- **Invitations with acceptance** and password reset by email.
- **Signed webhooks** for decision and case events.
- **Fleet-wide circuit-breaker state** in Redis (currently per process) and a sliding-window rate limiter.
- **Real provider adapters** behind the existing interface, with contract tests against vendor sandboxes.
- **Encryption key rotation** with key IDs on ciphertext and a background re-encryption job.
- **Per-organization workflow configuration.**
- **OpenTelemetry traces** from the existing step timings, and Prometheus metrics.
- **Benchmarks on dedicated hardware** with a separate load generator.

## Known limitations

- Providers are simulations; scores and decisions are illustrative, not regulatory outcomes.
- Circuit-breaker state is per API process (rate-limit counters are shared in Redis); queue delivery is at-least-once, and idempotent claims make duplicates harmless.
- The Docker stack is built and smoke-tested by the CI `docker` job; the original build sandbox had no registry access.
- The frontend's main bundle is ~224 KB gzipped (pages and charts are code-split).

## License

Released under the [MIT License](LICENSE).
