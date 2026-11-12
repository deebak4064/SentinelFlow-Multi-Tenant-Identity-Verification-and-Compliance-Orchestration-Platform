# Production deployment guide

SentinelFlow is a modular monolith: one stateless API image, one worker image (same code), PostgreSQL
and Redis. This guide covers what changes between the local Compose stack and a production
deployment. It is a reference architecture; the project has only been run locally and in CI.

## Topology

```
Internet → TLS load balancer → web (nginx, static SPA + /api proxy) → api (N replicas)
                                                                    ↘ Redis (queue, rate limits, cache)
                                         worker (M replicas) ←──────┘
                                         api + worker → PostgreSQL (primary; read replica optional)
```

- **api**: stateless; scale horizontally. Size `SF_DB_POOL_SIZE` so that
  `replicas × workers × (pool_size + max_overflow)` stays under Postgres `max_connections`
  (or put PgBouncer in transaction mode in front).
- **worker**: consumes `execution_mode=queue` requests. Claims are atomic, so any number of
  replicas is safe. A reaper re-queues requests stuck in flight for more than 5 minutes.
- **migrate**: run `alembic upgrade head` as a one-off job before rolling out a new API version.
  Migrations are additive; never run them from every API replica at boot.

## Required configuration

| Variable | Production value |
|---|---|
| `SF_ENV` | `production` (startup fails without an encryption key) |
| `SF_FIELD_ENCRYPTION_KEY` | 32 random bytes, urlsafe base64, from a secret manager. Rotating it needs a re-encryption job; losing it makes stored PII unrecoverable. |
| `SF_DATABASE_URL` | Managed Postgres 16+ with TLS (`?sslmode=require`) and a non-superuser role |
| `SF_REDIS_URL` | Managed Redis with AUTH/TLS (`rediss://`) |
| `SF_COOKIE_SECURE` | `true` (Secure cookies + HSTS) |
| `SF_CORS_ORIGINS` | Only the exact web origin(s); with same-origin nginx this can be empty |
| `SF_RATE_LIMIT_PER_MINUTE` | Per principal; tune per plan |
| `SF_LLM_API_KEY` | Optional. Organizations must still opt in under Settings. |

Never bake secrets into images; inject them as environment variables or mounted secrets.

## Security checklist

- Terminate TLS at the load balancer; keep the API reachable **only** through nginx/the LB
  (uvicorn trusts `X-Forwarded-For` from its peers for audit IPs and per-IP login limits).
- Restrict database network access to the api/worker/migrate workloads.
- The audit table rejects UPDATE/DELETE/TRUNCATE via trigger. For stronger guarantees, also
  stream audit rows to write-once storage (e.g. object storage with retention lock).
- Disable demo accounts in the UI build: `VITE_SHOW_DEMO_ACCOUNTS=false`, and never run the seed job.
- Back up Postgres with point-in-time recovery; Redis data is reconstructible (queue items are
  re-queued by the reaper from Postgres state).

## Observability

- `GET /health/live` (process up) and `GET /health/ready` (Postgres + Redis reachable) for probes.
- Every response carries `X-Request-Id`; the same ID is written to audit events and access logs.
- `GET /api/v1/system/metrics` exposes circuit-breaker state, cache hit rates and queue depth
  (platform-wide values only for platform admins). Export these to your metrics system.
- Ship JSON logs from stdout to your log platform; alert on `verification.failed` audit events and
  on open circuit breakers.

## Replacing a simulated provider with a real one

Implement `ProviderAdapter` (`build_request`, `call`, `normalize`, optional `cache_key`) in
`backend/app/engine/providers.py`, raise `ProviderUnavailable` for retryable failures and
`ProviderBadRequest` for permanent ones, and register it in `REGISTRY` / `WORKFLOWS`. Timeouts,
retries, circuit breaking, caching, risk and policy evaluation need no changes.
