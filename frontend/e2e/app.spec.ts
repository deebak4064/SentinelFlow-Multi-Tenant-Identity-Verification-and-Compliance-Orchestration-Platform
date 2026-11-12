import { expect, test, type Page } from "@playwright/test";

const PASSWORD = "SentinelDemo2026!";

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("dashboard shows metrics computed by the backend", async ({ page }) => {
  await login(page, "org.admin@northwind.example");
  const api = await page.request.get("/api/v1/dashboard/overview?days=14");
  const data = await api.json();
  await expect(page.getByRole("main").getByText("Verifications", { exact: true })).toBeVisible();
  await expect(page.getByText(new Intl.NumberFormat().format(data.total_verifications), { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Throughput by decision")).toBeVisible();
});

test("submit a verification and inspect its decision trace", async ({ page }) => {
  await login(page, "org.admin@northwind.example");
  await page.getByRole("link", { name: "Verifications" }).click();
  await page.getByRole("button", { name: "New verification" }).click();
  await page.getByLabel("Full name").fill("Viktor Sanctionov");
  await page.getByLabel("Date of birth").fill("1971-03-14");
  await page.getByLabel("Document number").fill("P77665544");
  await page.getByRole("button", { name: "Run verification" }).click();
  await expect(page.getByText("Escalated").first()).toBeVisible();
  await expect(page.getByText("Strong watchlist match requires senior review").first()).toBeVisible();
  await expect(page.getByText("Execution trace")).toBeVisible();
  await expect(page.getByText("Watchlist check")).toBeVisible();
  await expect(page.getByText("Decided", { exact: true })).toBeVisible();
  await expect(page.getByText("V*** S***").first()).toBeVisible(); // PII masked
});

test("manual review: approve an ambiguous case", async ({ page }) => {
  await login(page, "org.admin@northwind.example");
  const res = await page.request.post("/api/v1/verifications", {
    headers: { "X-CSRF-Token": (await page.context().cookies()).find((c) => c.name === "sf_csrf")!.value },
    data: { external_ref: `e2e-${Date.now()}`, verification_type: "individual",
      subject: { full_name: "Elena Novak", dob: "1988-02-02", country: "DE", document_type: "passport", document_number: "P1235555" } },
  });
  const v = await res.json();
  expect(v.decision).toBe("manual_review");
  await page.goto(`/cases/${v.case.id}`);
  await page.getByRole("button", { name: "Approve" }).click();
  await page.getByLabel("Decision notes").fill("Clear image provided on resubmission");
  await page.getByRole("button", { name: "Approve customer" }).click();
  await expect(page.getByText("Customer approved")).toBeVisible();
  await expect(page.getByText("Approved", { exact: true }).first()).toBeVisible();
});

test("auditor gets read-only, masked access", async ({ page }) => {
  await login(page, "auditor@northwind.example");
  await expect(page.getByRole("link", { name: "API keys" })).toHaveCount(0);
  await page.getByRole("link", { name: "Verifications" }).click();
  await expect(page.getByRole("button", { name: "New verification" })).toHaveCount(0);
  await page.goto("/api-keys");
  await expect(page.getByText("You don't have access to this")).toBeVisible();
  await page.goto("/customers");
  await page.locator("tbody tr").first().click();
  await expect(page.getByRole("button", { name: "Reveal personal data" })).toHaveCount(0);
});

test("another tenant cannot open this organization's records", async ({ page }) => {
  await login(page, "org.admin@northwind.example");
  const list = await (await page.request.get("/api/v1/verifications?page_size=1")).json();
  const id = list.items[0].id;
  await page.getByRole("button", { name: /Northwind/ }).click();
  await page.getByText("Sign out").click();
  await login(page, "admin@contoso.example");
  await page.goto(`/verifications/${id}`);
  await expect(page.getByText("Not found", { exact: true })).toBeVisible();
});

test("create, version and simulate a policy", async ({ page }) => {
  await login(page, "compliance.analyst@northwind.example");
  await page.getByRole("link", { name: "Policies" }).click();
  await page.getByRole("button", { name: "New policy" }).click();
  const name = `E2E high history ${Date.now()}`;
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Reason shown to reviewers").fill("Customer has prior rejections");
  await page.getByRole("button", { name: "Create policy" }).click();
  await expect(page.getByText(`Saved ${name} as version 1`)).toBeVisible();
  await page.getByRole("tab", { name: "Test" }).click();
  await page.getByLabel("Verification").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Run" }).click();
  await expect(page.getByText(/This policy would|Does not match/)).toBeVisible();
});

test("API key secret is shown once", async ({ page }) => {
  await login(page, "org.admin@northwind.example");
  await page.goto("/api-keys");
  await page.getByRole("button", { name: "Create key" }).first().click();
  const keyName = `E2E service ${Date.now()}`;
  await page.getByLabel("Name").fill(keyName);
  await page.getByRole("button", { name: "Create key" }).last().click();
  const secret = await page.getByLabel("API key secret").inputValue();
  expect(secret).toMatch(/^sf_live_/);
  await page.getByRole("button", { name: "I've stored the secret" }).click();
  await expect(page.getByText(keyName)).toBeVisible();
  await expect(page.getByText(secret)).toHaveCount(0);
});

test("onboarding creates an isolated, empty organization", async ({ page }) => {
  await page.goto("/register");
  await page.getByLabel("Organization name").fill(`E2E Bank ${Date.now()}`);
  await page.getByLabel("Your name").fill("Erin Example");
  await page.getByLabel("Work email").fill(`erin.${Date.now()}@example.com`);
  await page.getByLabel("Password").fill("LongEnough123456");
  await page.getByRole("button", { name: "Create organization" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: "Verifications" }).click();
  await expect(page.getByText("No verifications yet")).toBeVisible();
});
