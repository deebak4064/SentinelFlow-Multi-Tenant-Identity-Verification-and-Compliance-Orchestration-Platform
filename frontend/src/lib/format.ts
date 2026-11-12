const dtf = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export const fmtDate = (s?: string | null) => (s ? dtf.format(new Date(s)) : "—");

export function fmtRelative(s?: string | null): string {
  if (!s) return "—";
  const diff = (new Date(s).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 60) return rtf.format(Math.round(diff), "second");
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  return rtf.format(Math.round(diff / 86400), "day");
}

export const fmtMs = (ms?: number | null) => (ms == null ? "—" : ms < 1000 ? `${ms.toFixed(ms < 10 ? 1 : 0)} ms` : `${(ms / 1000).toFixed(2)} s`);
export const fmtPct = (v?: number | null, digits = 1) => (v == null ? "—" : `${(v * 100).toFixed(digits)}%`);
export const fmtNum = (v?: number | null) => (v == null ? "—" : new Intl.NumberFormat().format(v));

export const DECISION_LABEL: Record<string, string> = {
  approved: "Approved", rejected: "Rejected", manual_review: "Manual review", escalated: "Escalated",
};
export const ACTION_LABEL: Record<string, string> = {
  approve: "Approve", reject: "Reject", manual_review: "Manual review", escalate: "Escalate", flag: "Flag",
};
export const ROLE_LABEL: Record<string, string> = {
  super_admin: "Super Admin", org_admin: "Organization Admin", compliance_analyst: "Compliance Analyst", reviewer: "Reviewer", auditor: "Read-only Auditor",
};
export const PROVIDER_LABEL: Record<string, string> = { sim_idv: "Identity", sim_kyb: "Business registry", sim_watchlist: "Watchlist" };

export const humanize = (s: string) => s.replace(/[_.:]/g, " ").replace(/^\w/, (c) => c.toUpperCase());
