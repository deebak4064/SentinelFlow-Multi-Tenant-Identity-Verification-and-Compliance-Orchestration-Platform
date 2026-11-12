"""Load profile for SentinelFlow.

Simulated clients are machine integrations authenticating with an API key (the realistic
high-volume path). Traffic mix per client:
    60%  POST /verifications   (full workflow: 2 provider calls, risk, policy, persistence)
    20%  GET  /verifications   (paginated, filtered list)
    15%  GET  /verifications/{id}
     5%  GET  /dashboard/overview (aggregates; 15 s cache)

Environment: SF_BENCH_API_KEY (required), SF_BENCH_NAME_POOL (default 400) controls how often
watchlist screenings repeat, which is what the tenant-scoped cache serves.
"""

import os
import random
import uuid

from locust import HttpUser, between, task

FIRST = ["Amara", "Liam", "Sofia", "Kenji", "Priya", "Mateo", "Elena", "Noah", "Aisha", "Lucas", "Mei", "Omar", "Hanna", "Ravi"]
LAST = ["Okafor", "Lindqvist", "Moreau", "Tanaka", "Raman", "Silva", "Novak", "Fischer", "Haddad", "Costa", "Chen", "Nasser"]
POOL = int(os.environ.get("SF_BENCH_NAME_POOL", "400"))
rng = random.Random(7)
PEOPLE = [(f"{rng.choice(FIRST)} {rng.choice(LAST)}", f"{rng.randint(1955, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}") for _ in range(POOL)]


def payload() -> dict:
    name, dob = random.choice(PEOPLE)
    roll = random.random()
    doc = f"P{random.randint(10_000_000, 99_999_999)}"
    if roll < 0.04:
        doc = doc[:-4] + "5555"
    elif roll < 0.07:
        doc = doc[:-4] + "0000"
    return {
        "external_ref": f"bench-{uuid.uuid4().hex[:16]}",
        "verification_type": "individual",
        "subject": {"full_name": name, "dob": dob, "country": random.choice(["GB", "DE", "FR", "SG", "IN"]),
                    "document_type": "passport", "document_number": doc, "email": "bench@example.com"},
    }


class IntegrationClient(HttpUser):
    wait_time = between(0.05, 0.25)

    def on_start(self):
        self.client.headers.update({"X-API-Key": os.environ["SF_BENCH_API_KEY"], "Content-Type": "application/json"})
        self.ids: list[str] = []

    @task(12)
    def submit(self):
        with self.client.post("/api/v1/verifications", json=payload(), headers={"Idempotency-Key": uuid.uuid4().hex},
                              name="POST /verifications", catch_response=True) as r:
            if r.status_code == 201:
                self.ids.append(r.json()["id"])
                self.ids = self.ids[-50:]
                r.success()
            else:
                r.failure(f"status {r.status_code}")

    @task(4)
    def list_page(self):
        self.client.get(f"/api/v1/verifications?page_size=25&page={random.randint(1, 5)}", name="GET /verifications")

    @task(3)
    def detail(self):
        if self.ids:
            self.client.get(f"/api/v1/verifications/{random.choice(self.ids)}", name="GET /verifications/{id}")

    @task(1)
    def dashboard(self):
        self.client.get("/api/v1/dashboard/overview?days=14", name="GET /dashboard/overview")
