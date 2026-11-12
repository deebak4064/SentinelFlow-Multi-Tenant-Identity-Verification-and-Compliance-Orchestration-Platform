import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDialog, DecisionBadge, ErrorState, Pagination, RiskScore } from "@/components/common";
import { api, ApiError, qs } from "@/lib/api";
import { fmtMs, fmtPct } from "@/lib/format";
import { LoginPage } from "@/pages/Auth";

const wrap = (ui: ReactNode) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>,
);

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockResolvedValue({ ok: status < 400, status, json: async () => body });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("domain components", () => {
  it("renders risk score with category and a dash when absent", () => {
    const { rerender } = render(<RiskScore score={81.4} category="critical" />);
    expect(screen.getByText("81")).toBeInTheDocument();
    expect(screen.getByText("critical")).toBeInTheDocument();
    rerender(<RiskScore score={null} category={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("labels decisions and in-flight statuses", () => {
    const { rerender } = render(<DecisionBadge decision="manual_review" />);
    expect(screen.getByText("Manual review")).toBeInTheDocument();
    rerender(<DecisionBadge decision={null} status="queued" />);
    expect(screen.getByText("Queued")).toBeInTheDocument();
  });

  it("paginates and disables out-of-range buttons", async () => {
    const onPage = vi.fn();
    render(<Pagination page={1} pageSize={25} total={60} onPage={onPage} />);
    expect(screen.getByText("1–25 of 60")).toBeInTheDocument();
    expect(screen.getByLabelText("Previous page")).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Next page"));
    expect(onPage).toHaveBeenCalledWith(2);
  });

  it("explains permission errors without offering a retry", () => {
    render(<ErrorState error={new ApiError(403, "forbidden", "You do not have permission", undefined, "rid-1")} onRetry={() => {}} />);
    expect(screen.getByText("You don't have access to this")).toBeInTheDocument();
    expect(screen.getByText(/rid-1/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("only runs a consequential action after confirmation", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<ConfirmDialog trigger={<button>Revoke</button>} title="Revoke key?" description="Can't be undone." confirmLabel="Revoke key" tone="danger" onConfirm={onConfirm} />);
    await userEvent.click(screen.getByText("Revoke"));
    expect(onConfirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Revoke key" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});

describe("api client", () => {
  it("sends the CSRF token on writes but not reads", async () => {
    document.cookie = "sf_csrf=tok123";
    const f = mockFetch(200, { ok: true });
    await api("/x", { method: "POST", body: { a: 1 } });
    expect(f.mock.calls[0][1].headers["X-CSRF-Token"]).toBe("tok123");
    await api("/x");
    expect(f.mock.calls[1][1].headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("maps the error envelope to ApiError", async () => {
    mockFetch(409, { error: { code: "idempotency_conflict", message: "Key reused", request_id: "r1" } });
    await expect(api("/verifications", { method: "POST", body: {} })).rejects.toMatchObject({ status: 409, code: "idempotency_conflict", requestId: "r1" });
  });

  it("builds query strings without empty values", () => {
    expect(qs({ a: 1, b: "", c: undefined, d: "x y" })).toBe("?a=1&d=x+y");
  });

  it("formats durations and percentages", () => {
    expect(fmtMs(4.25)).toBe("4.3 ms");
    expect(fmtMs(1500)).toBe("1.50 s");
    expect(fmtPct(0.9851)).toBe("98.5%");
  });
});

describe("login", () => {
  it("validates before calling the API", async () => {
    const f = mockFetch(200, {});
    wrap(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email")).toBeInTheDocument();
    expect(f).not.toHaveBeenCalled();
  });

  it("shows the server's message on bad credentials", async () => {
    mockFetch(401, { error: { code: "invalid_credentials", message: "Invalid email or password" } });
    wrap(<LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "someone@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid email or password"));
  });

  it("fills credentials from a demo account", async () => {
    wrap(<LoginPage />);
    await userEvent.click(screen.getByText("auditor@northwind.example"));
    expect(screen.getByLabelText("Email")).toHaveValue("auditor@northwind.example");
  });
});
