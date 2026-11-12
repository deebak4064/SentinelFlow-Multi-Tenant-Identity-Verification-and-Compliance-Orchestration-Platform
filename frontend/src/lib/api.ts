/** Thin fetch wrapper. Session lives in an HttpOnly cookie; writes echo the CSRF cookie in a header. */

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown, public requestId?: string) {
    super(message);
  }
}

function csrfToken(): string | undefined {
  return document.cookie.split("; ").find((c) => c.startsWith("sf_csrf="))?.slice(8);
}

type Query = Record<string, string | number | boolean | undefined | null>;

export function qs(params: Query = {}): string {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") s.set(k, String(v));
  const out = s.toString();
  return out ? `?${out}` : "";
}

export async function api<T = unknown>(path: string, init: { method?: string; body?: unknown; query?: Query; headers?: Record<string, string> } = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json", ...init.headers };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") {
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const res = await fetch(`/api/v1${path}${qs(init.query)}`, {
    method,
    headers,
    credentials: "same-origin",
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const e = data?.error ?? {};
    if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event("sf:unauthorized"));
    throw new ApiError(res.status, e.code ?? "error", e.message ?? `Request failed (${res.status})`, e.details, e.request_id);
  }
  return data as T;
}

export const errorMessage = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Check your connection and try again.");
