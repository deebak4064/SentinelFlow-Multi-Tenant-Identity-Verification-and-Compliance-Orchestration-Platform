"""Reproducible local benchmark suite.

    python benchmarks/run_benchmarks.py [--levels 10 50 100 200] [--duration 60] [--api-workers 4]

What it does:
  1. Starts its own API server (uvicorn, N workers) on port 8100 against the configured database,
     with the per-principal rate limit raised so the limiter is not what gets measured.
  2. Creates a fresh benchmark organization + API key; enables provider chaos (5% outages,
     1% timeouts, 40 ms simulated upstream latency) so retries and recovery are exercised.
  3. Runs Locust headless at each concurrency level (stats reset after ramp-up), sampling CPU and
     memory of the API, Postgres and Redis processes once per second.
  4. Derives provider recovery, policy-evaluation and database query latency from stored data,
     micro-benchmarks the policy engine, reads cache stats from Redis and coverage from docs/.
  5. Writes benchmark_results.json, benchmark_report.md and performance_charts.png.

All numbers are measurements of THIS machine running everything locally (API, Postgres, Redis
and the load generator share the same host). They are not production capacity claims.
"""

import argparse
import asyncio
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
import timeit
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
PORT = 8100
BASE = f"http://127.0.0.1:{PORT}"
PASSWORD = "BenchmarkPassword2026"


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 3)


def _spin(n: int) -> float:
    t = time.perf_counter()
    x = 0
    for i in range(n):
        x += i * i
    return time.perf_counter() - t


def effective_cores(k: int = 8, n: int = 6_000_000) -> float:
    """Visible vCPUs can overstate what a VM/container actually gets. Measure it."""
    import multiprocessing as mp

    single = _spin(n)
    with mp.Pool(k) as pool:
        t = time.perf_counter()
        pool.map(_spin, [n] * k)
        wall = time.perf_counter() - t
    return round(k * single / wall, 1)


def start_api(workers: int) -> subprocess.Popen:
    env = {**os.environ, "SF_RATE_LIMIT_PER_MINUTE": "100000000", "SF_LOGIN_RATE_LIMIT_PER_MINUTE": "1000"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT), "--workers", str(workers), "--log-level", "warning", "--no-access-log"],
        cwd=BACKEND, env=env, stdout=subprocess.DEVNULL, stderr=open(HERE / "raw" / "api.log", "w"),
    )
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/health/ready", timeout=1).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError("API did not become ready")


def setup_org() -> tuple[str, str]:
    c = httpx.Client(base_url=BASE, timeout=10)
    email = f"bench.{int(time.time())}@example.com"
    r = c.post("/api/v1/auth/register", json={"email": email, "full_name": "Bench Admin", "password": PASSWORD, "organization_name": f"Benchmark {datetime.now():%Y%m%d-%H%M%S}"})
    r.raise_for_status()
    org_id = r.json()["organization"]["id"]
    tok = c.post("/api/v1/auth/token", json={"email": email, "password": PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for key in ("sim_idv", "sim_watchlist", "sim_kyb"):
        c.patch(f"/api/v1/providers/{key}", headers=h, json={"timeout_ms": 300, "max_retries": 2,
                "simulation": {"latency_ms": 40, "failure_rate": 0.05, "timeout_rate": 0.01}}).raise_for_status()
    k = c.post("/api/v1/api-keys", headers=h, json={"name": "benchmark", "scopes": ["verifications:create", "verifications:read"]})
    k.raise_for_status()
    return org_id, k.json()["secret"]


class Sampler(threading.Thread):
    """1 Hz CPU/RSS sampling of the API workers, Postgres and Redis."""

    def __init__(self, api_pid: int):
        super().__init__(daemon=True)
        self.api_pid, self.stop_evt, self.samples = api_pid, threading.Event(), []
        self._cache: dict[int, psutil.Process] = {}  # cpu_percent needs the SAME Process object across calls

    def _procs(self):
        groups = {"api": [], "postgres": [], "redis": []}
        found = []
        try:
            parent = psutil.Process(self.api_pid)
            found += [("api", p) for p in [parent, *parent.children(recursive=True)]]
        except psutil.NoSuchProcess:
            pass
        for p in psutil.process_iter(["name"]):
            n = (p.info["name"] or "").lower()
            if n.startswith("postgres"):
                found.append(("postgres", p))
            elif n.startswith("redis-server"):
                found.append(("redis", p))
        for g, p in found:
            if p.pid not in self._cache:
                self._cache[p.pid] = p
                try:
                    p.cpu_percent(None)  # prime
                except psutil.Error:
                    pass
            groups[g].append(self._cache[p.pid])
        return groups

    def run(self):
        self._procs()
        while not self.stop_evt.wait(1.0):
            groups = self._procs()
            row = {"t": time.time()}
            for g, ps in groups.items():
                cpu = rss = 0.0
                for p in ps:
                    try:
                        cpu += p.cpu_percent(None)
                        # PSS splits shared pages (Postgres shared_buffers) fairly; RSS would count them per process.
                        rss += p.memory_full_info().pss
                    except psutil.Error:
                        pass
                row[f"{g}_cpu"], row[f"{g}_rss_mb"] = cpu, rss / 2**20
            self.samples.append(row)

    def summary(self):
        s = self.samples[3:] or self.samples  # skip the first seconds of ramp
        out = {}
        for g in ("api", "postgres", "redis"):
            cpus = [x[f"{g}_cpu"] for x in s]
            mems = [x[f"{g}_rss_mb"] for x in s]
            out[g] = {"cpu_percent_avg": round(statistics.mean(cpus), 1) if cpus else None, "cpu_percent_max": round(max(cpus), 1) if cpus else None,
                      "rss_mb_avg": round(statistics.mean(mems), 1) if mems else None, "rss_mb_max": round(max(mems), 1) if mems else None}
        return out


def read_locust_csv(prefix: Path) -> dict:
    rows = {r["Name"]: r for r in csv.DictReader(open(f"{prefix}_stats.csv"))}

    def conv(r):
        n = int(r["Request Count"])
        return {"requests": n, "failures": int(r["Failure Count"]), "error_rate": round(int(r["Failure Count"]) / n, 5) if n else 0,
                "rps": round(float(r["Requests/s"]), 2), "avg_ms": round(float(r["Average Response Time"]), 2),
                "p50_ms": float(r["50%"]), "p95_ms": float(r["95%"]), "p99_ms": float(r["99%"]), "max_ms": round(float(r["Max Response Time"]), 1)}

    return {"aggregate": conv(rows["Aggregated"]), "endpoints": {k: conv(v) for k, v in rows.items() if k != "Aggregated"}}


def run_level(users: int, duration: int, api_pid: int, api_key: str) -> dict:
    prefix = HERE / "raw" / f"u{users}"
    sampler = Sampler(api_pid)
    sampler.start()
    spawn = max(users // 5, 1)
    cmd = [sys.executable, "-m", "locust", "-f", str(HERE / "locustfile.py"), "--headless", "-u", str(users), "-r", str(spawn),
           "-t", f"{duration + users // spawn}s", "--host", BASE, "--csv", str(prefix), "--reset-stats", "--only-summary", "--loglevel", "WARNING"]
    if users >= 100:
        cmd += ["--processes", "4"]  # a single Locust process saturates one core before the API does
    subprocess.run(cmd, env={**os.environ, "SF_BENCH_API_KEY": api_key}, cwd=HERE, check=False, stdout=subprocess.DEVNULL, stderr=open(f"{prefix}.log", "w"))
    sampler.stop_evt.set()
    sampler.join()
    res = read_locust_csv(prefix)
    res["resources"] = sampler.summary()
    res["users"] = users
    return res


async def db_metrics(org_id: str) -> dict:
    from sqlalchemy import func, select, text

    from app.db import SessionLocal, engine
    from app.models import Customer, ProviderResult, RiskAssessment, VerificationRequest

    out = {}
    async with SessionLocal() as db:
        rows = (await db.execute(text(
            "SELECT attempts, outcome, error_code FROM provider_results WHERE org_id = :o AND outcome <> 'skipped'"), {"o": org_id})).all()
        transient = {"timeout", "unavailable", "circuit_open"}
        had_failure = [r for r in rows if r.attempts > 1 or (r.outcome == "error" and r.error_code in transient)]
        recovered = [r for r in had_failure if r.outcome != "error"]
        out["provider_calls"] = len(rows)
        out["provider_calls_with_transient_failure"] = len(had_failure)
        out["provider_calls_recovered_by_retry"] = len(recovered)
        out["provider_failure_recovery_rate"] = round(len(recovered) / len(had_failure), 4) if had_failure else None
        out["provider_final_error_rate"] = round(sum(r.outcome == "error" for r in rows) / len(rows), 5) if rows else None

        steps = (await db.execute(text(
            "SELECT s->>'name' AS n, (s->>'duration_ms')::float AS d FROM workflow_executions w, jsonb_array_elements(w.steps) s "
            "WHERE w.org_id = :o AND s->>'duration_ms' IS NOT NULL"), {"o": org_id})).all()
        by = {}
        for n, d in steps:
            by.setdefault(n, []).append(d)
        out["in_workflow_step_latency_ms"] = {k: {"p50": pct(v, 0.5), "p95": pct(v, 0.95), "p99": pct(v, 0.99), "n": len(v)}
                                              for k, v in by.items() if k in ("policy_evaluation", "risk_scoring", "load_context", "provider:sim_idv", "provider:sim_watchlist")}
        wf = (await db.execute(text("SELECT duration_ms FROM workflow_executions WHERE org_id = :o AND status='completed'"), {"o": org_id})).scalars().all()
        out["workflow_end_to_end_ms"] = {"p50": pct(wf, 0.5), "p95": pct(wf, 0.95), "p99": pct(wf, 0.99), "n": len(wf)}
        out["verifications_stored"] = await db.scalar(select(func.count()).select_from(VerificationRequest).where(VerificationRequest.org_id == org_id))
        out["total_verification_rows_in_db"] = await db.scalar(select(func.count()).select_from(VerificationRequest))

    # Representative read queries, executed serially on an idle system.
    async def timed(fn, n=200):
        samples = []
        for _ in range(n):
            t = time.perf_counter()
            await fn()
            samples.append((time.perf_counter() - t) * 1000)
        return {"p50": pct(samples, 0.5), "p95": pct(samples, 0.95), "p99": pct(samples, 0.99), "n": n}

    async with SessionLocal() as db:
        vid = await db.scalar(select(VerificationRequest.id).where(VerificationRequest.org_id == org_id).limit(1))

        async def list_page():
            await db.execute(select(VerificationRequest, Customer, RiskAssessment).join(Customer, Customer.id == VerificationRequest.customer_id)
                             .outerjoin(RiskAssessment, RiskAssessment.verification_id == VerificationRequest.id)
                             .where(VerificationRequest.org_id == org_id).order_by(VerificationRequest.created_at.desc()).limit(25))

        async def detail():
            await db.execute(select(ProviderResult).where(ProviderResult.verification_id == vid))
            await db.execute(select(RiskAssessment).where(RiskAssessment.verification_id == vid))

        async def dashboard_agg():
            await db.execute(text("SELECT decision, count(*) FROM verification_requests WHERE org_id=:o AND created_at > now() - interval '14 days' GROUP BY 1"), {"o": org_id})
            await db.execute(text("SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) FROM workflow_executions WHERE org_id=:o"), {"o": org_id})

        async def tenant_lookup():
            await db.execute(select(VerificationRequest).where(VerificationRequest.id == vid, VerificationRequest.org_id == org_id))

        out["db_query_latency_ms"] = {"tenant_scoped_lookup_by_id": await timed(tenant_lookup), "verification_list_page_join": await timed(list_page),
                                      "verification_detail_children": await timed(detail), "dashboard_aggregates": await timed(dashboard_agg, 50)}
    await engine.dispose()
    return out


def policy_microbench() -> dict:
    from app.engine.policy import PolicySnapshot, evaluate
    from app.services.bootstrap import DEFAULT_POLICIES

    snaps = [PolicySnapshot(str(i), p["name"], 1, p["priority"], p["definition"]) for i, p in enumerate(DEFAULT_POLICIES)]
    ctx = {"risk": {"score": 32.5, "category": "medium"}, "customer": {"country": "GB", "has_email": True, "type": "individual"},
           "providers": {"identity": {"outcome": "pass", "confidence": 0.96, "signals": {"document_authentic": True}},
                         "watchlist": {"outcome": "review", "confidence": 0.84, "signals": {}}},
           "signals": {"missing_mandatory_count": 0, "inconsistency_count": 0, "conflicting_results": False}, "history": {"failed_verifications": 0}}
    n = 20000
    runs = timeit.repeat(lambda: evaluate(snaps, ctx, ["pass", "review"]), number=n, repeat=5)
    per = [r / n * 1e6 for r in runs]
    return {"policies_evaluated_per_call": len(snaps), "iterations": n * 5, "us_per_evaluation_best": round(min(per), 2), "us_per_evaluation_median": round(statistics.median(per), 2)}


def charts(levels: list[dict], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"figure.facecolor": "#14171a", "axes.facecolor": "#1a1e22", "axes.edgecolor": "#2a3036", "axes.labelcolor": "#8a939b",
                         "xtick.color": "#8a939b", "ytick.color": "#8a939b", "text.color": "#e6eaed", "grid.color": "#2a3036", "font.size": 9})
    u = [x["users"] for x in levels]
    agg = [x["aggregate"] for x in levels]
    post = [x["endpoints"].get("POST /verifications", {}) for x in levels]
    fig, ax = plt.subplots(2, 2, figsize=(11, 7.2))
    a = ax[0][0]
    a.plot(u, [x["rps"] for x in agg], marker="o", color="#2e9e76", label="All requests")
    a.plot(u, [x.get("rps", 0) for x in post], marker="o", color="#5ba4b4", label="Verification submissions")
    a.set_title("Throughput (requests/s)", loc="left")
    a.legend(frameon=False)
    a = ax[0][1]
    for key, color in (("p50_ms", "#2e9e76"), ("p95_ms", "#d4a04a"), ("p99_ms", "#c46a62")):
        a.plot(u, [x.get(key) for x in post], marker="o", color=color, label=key.replace("_ms", ""))
    a.set_title("POST /verifications latency (ms)", loc="left")
    a.legend(frameon=False)
    a = ax[1][0]
    rates = [x["error_rate"] * 100 for x in agg]
    bars = a.bar([str(x) for x in u], rates, color="#c46a62")
    a.set_ylim(0, max(1.0, max(rates) * 1.3))
    for bar, x in zip(bars, agg, strict=True):
        a.annotate(f"{x['failures']} / {x['requests']:,}", (bar.get_x() + bar.get_width() / 2, bar.get_height()), xytext=(0, 4),
                   textcoords="offset points", ha="center", color="#8a939b", fontsize=8)
    a.set_title("API error rate (%), failed / total requests", loc="left")
    a = ax[1][1]
    a.plot(u, [x["resources"]["api"]["cpu_percent_avg"] for x in levels], marker="o", color="#5ba4b4", label="API CPU % (sum of cores)")
    a.plot(u, [x["resources"]["postgres"]["cpu_percent_avg"] for x in levels], marker="o", color="#d4a04a", label="Postgres CPU %")
    a2 = a.twinx()
    a2.plot(u, [x["resources"]["api"]["rss_mb_avg"] for x in levels], marker="s", linestyle="--", color="#8a939b", label="API memory PSS (MB)")
    a2.tick_params(colors="#8a939b")
    a.set_title("Resource usage", loc="left")
    a.legend(frameon=False, loc="upper left")
    a2.legend(frameon=False, loc="lower right")
    for row in ax:
        for a in row:
            a.grid(True, alpha=0.6)
            a.set_xlabel("Concurrent simulated clients")
    fig.suptitle("SentinelFlow local benchmark", x=0.06, ha="left", fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)


def report(res: dict, path: Path) -> None:
    hw, lv, db = res["environment"], res["load_levels"], res["derived"]
    L = ["# SentinelFlow benchmark report", "",
         f"Measured {res['generated_at']} on a single host. API, PostgreSQL, Redis and the Locust load generator all ran on the same machine, "
         "so these are **local measurements, not production capacity claims**.", "",
         "## Environment", "",
         f"- {hw['cpu_count']} vCPU visible, but **{hw['effective_cpu_cores_measured']} effective cores measured** (parallel CPU-bound probe); {hw['memory_gb']} GB RAM, {hw['platform']}",
         "- All components share that CPU budget, so throughput plateaus once it is saturated and further clients only add queueing latency",
         f"- Python {hw['python']}, {hw['postgres']}, Redis {hw['redis']}",
         f"- API: uvicorn with {res['config']['api_workers']} worker processes; per-principal rate limit raised for the run",
         f"- Simulated providers: {res['config']['provider_simulation']}",
         f"- Each level: ramp-up excluded (stats reset), then {res['config']['duration_s']} s steady state",
         "", "## Methodology", "",
         "Clients are API-key integrations. Mix: 60% `POST /verifications` (full workflow: two concurrent provider calls with retries, risk scoring, "
         "policy evaluation, one transactional write of ~7 rows plus audit events), 20% list, 15% detail, 5% dashboard. "
         "Think time 50–250 ms per client. Provider chaos (5% outages, 1% timeouts) is on, so the retry path is part of the measured latency.",
         "", "## Results by concurrency", "",
         "| Clients | Total req/s | Submissions/s | Submit p50 | Submit p95 | Submit p99 | All p95 | Error rate | API CPU avg | API memory (PSS) |",
         "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for x in lv:
        a, p = x["aggregate"], x["endpoints"].get("POST /verifications", {})
        L.append(f"| {x['users']} | {a['rps']} | {p.get('rps')} | {p.get('p50_ms')} ms | {p.get('p95_ms')} ms | {p.get('p99_ms')} ms | {a['p95_ms']} ms | "
                 f"{a['error_rate'] * 100:.2f}% | {x['resources']['api']['cpu_percent_avg']}% | {x['resources']['api']['rss_mb_avg']} MB |")
    L += ["", "CPU percentages are summed across processes (100% = one core). Memory is proportional set size, so shared pages are not double-counted.", "",
          "## Derived from stored data (benchmark organization only)", "",
          f"- Provider calls: {db['provider_calls']:,}; calls that hit a transient failure: {db['provider_calls_with_transient_failure']:,}; "
          f"recovered by retry: {db['provider_calls_recovered_by_retry']:,} → **provider failure recovery rate {db['provider_failure_recovery_rate'] * 100:.1f}%**",
          f"- Provider calls still failing after retries: {db['provider_final_error_rate'] * 100:.2f}% (routed to manual review by policy, never auto-approved)",
          f"- Workflow end-to-end (server-side): p50 {db['workflow_end_to_end_ms']['p50']} ms, p95 {db['workflow_end_to_end_ms']['p95']} ms, p99 {db['workflow_end_to_end_ms']['p99']} ms over {db['workflow_end_to_end_ms']['n']:,} runs"]
    pe = db["in_workflow_step_latency_ms"].get("policy_evaluation", {})
    L.append(f"- Policy evaluation inside workflows (8 policies): p50 {pe.get('p50')} ms, p95 {pe.get('p95')} ms")
    L.append(f"- Policy engine micro-benchmark: {res['policy_microbenchmark']['us_per_evaluation_median']} µs per evaluation of {res['policy_microbenchmark']['policies_evaluated_per_call']} policies (median of 5×20,000)")
    c = res["cache"].get("provider:sim_watchlist", {})
    L.append(f"- Watchlist screening cache (tenant-scoped, 1 h TTL, {res['config']['name_pool']}-identity pool): hit rate {c.get('hit_rate', 0) * 100:.1f}% ({c.get('hit', 0):,} hits / {c.get('miss', 0):,} misses)")
    L += ["", "## Database query latency (serial, idle system)", "", f"Table size at measurement: {db['total_verification_rows_in_db']:,} verification requests.", "",
          "| Query | p50 | p95 | p99 |", "|---|---:|---:|---:|"]
    for k, v in db["db_query_latency_ms"].items():
        L.append(f"| {k.replace('_', ' ')} | {v['p50']} ms | {v['p95']} ms | {v['p99']} ms |")
    L += ["", "## Test coverage", "", f"Backend line coverage: **{res['test_coverage_percent']}%** ({res['test_counts']})", "",
          "## Reproduce", "", "```bash", "cd backend && pip install -r requirements-dev.txt", "python ../benchmarks/run_benchmarks.py --levels 10 50 100 200 --duration 60", "```", "",
          "![charts](performance_charts.png)", ""]
    path.write_text("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", type=int, nargs="+", default=[10, 50, 100, 200])
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--api-workers", type=int, default=2)
    ap.add_argument("--render-only", action="store_true", help="re-render charts/report from benchmark_results.json")
    args = ap.parse_args()
    if args.render_only:
        res = json.loads((HERE / "benchmark_results.json").read_text())
        charts(res["load_levels"], HERE / "performance_charts.png")
        report(res, HERE / "benchmark_report.md")
        return
    (HERE / "raw").mkdir(exist_ok=True)
    import redis as sync_redis

    from app.config import get_settings

    r = sync_redis.from_url(get_settings().redis_url)
    r.delete("sf:cache:stats")
    cores = effective_cores()
    print(f"effective CPU cores measured: {cores} (visible: {psutil.cpu_count()})", flush=True)
    api = start_api(args.api_workers)
    try:
        org_id, key = setup_org()
        levels = []
        for u in args.levels:
            print(f"→ {u} clients for {args.duration}s", flush=True)
            levels.append(run_level(u, args.duration, api.pid, key))
            a = levels[-1]["aggregate"]
            print(f"  {a['rps']} req/s, p95 {a['p95_ms']} ms, errors {a['failures']}/{a['requests']}", flush=True)
            time.sleep(3)
    finally:
        api.terminate()
        api.wait(10)

    stats = {k.decode(): int(v) for k, v in r.hgetall("sf:cache:stats").items()}
    cache = {}
    for k, v in stats.items():
        m, kind = k.rsplit(":", 1)
        cache.setdefault(m, {"hit": 0, "miss": 0})[kind] = v
    for m in cache.values():
        t = m["hit"] + m["miss"]
        m["hit_rate"] = round(m["hit"] / t, 4) if t else None

    cov_file = ROOT / "docs" / "coverage.json"
    cov = round(json.loads(cov_file.read_text())["totals"]["percent_covered"], 1) if cov_file.exists() else None
    pg = subprocess.run(["psql", get_settings().database_url.replace("+psycopg", ""), "-tAc", "SHOW server_version"], capture_output=True, text=True).stdout.strip()
    res = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "disclaimer": "Local single-host measurements (API, DB, Redis and load generator co-located). Not production capacity claims.",
        "environment": {"cpu_count": psutil.cpu_count(), "effective_cpu_cores_measured": cores, "memory_gb": round(psutil.virtual_memory().total / 2**30, 1), "platform": platform.platform(),
                        "python": platform.python_version(), "postgres": f"PostgreSQL {pg}", "redis": r.info()["redis_version"]},
        "config": {"api_workers": args.api_workers, "duration_s": args.duration, "levels": args.levels, "name_pool": int(os.environ.get("SF_BENCH_NAME_POOL", "400")),
                   "provider_simulation": "40 ms ±50% latency, 5% outage rate, 1% timeout rate, 300 ms timeout, 2 retries"},
        "load_levels": levels,
        "derived": asyncio.run(db_metrics(org_id)),
        "policy_microbenchmark": policy_microbench(),
        "cache": cache,
        "test_coverage_percent": cov,
        "test_counts": os.environ.get("SF_TEST_COUNTS", "see README"),
    }
    (HERE / "benchmark_results.json").write_text(json.dumps(res, indent=2, default=str))
    charts(levels, HERE / "performance_charts.png")
    report(res, HERE / "benchmark_report.md")
    print("wrote benchmark_results.json, benchmark_report.md, performance_charts.png")


if __name__ == "__main__":
    main()
