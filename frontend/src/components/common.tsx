import { AlertTriangle, ChevronLeft, ChevronRight, Inbox, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ApiError } from "@/lib/api";
import { DECISION_LABEL } from "@/lib/format";
import type { RiskCategory } from "@/lib/types";
import { cn } from "@/lib/utils";

import { Badge, Button, Dialog, DialogContent, Skeleton, type Tone } from "./ui";

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold leading-tight">{title}</h1>
        {description && <p className="mt-1 max-w-[70ch] text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

const DECISION_TONE: Record<string, Tone> = { approved: "emerald", rejected: "rose", manual_review: "amber", escalated: "amber" };
export function DecisionBadge({ decision, status }: { decision: string | null; status?: string }) {
  if (!decision) {
    const s = status ?? "pending";
    return <Badge tone={s === "failed" ? "rose" : "cyan"} dot>{s === "failed" ? "Failed" : s === "queued" ? "Queued" : "Processing"}</Badge>;
  }
  return <Badge tone={DECISION_TONE[decision] ?? "neutral"} dot>{DECISION_LABEL[decision] ?? decision}</Badge>;
}

export const RISK_COLOR: Record<RiskCategory, string> = { low: "#2e9e76", medium: "#5ba4b4", high: "#d4a04a", critical: "#c46a62" };
export const RISK_TONE: Record<RiskCategory, Tone> = { low: "emerald", medium: "cyan", high: "amber", critical: "rose" };

/** Score with a 0–100 track; the category threshold position is what gives the number meaning. */
export function RiskScore({ score, category, compact }: { score?: number | null; category?: RiskCategory | null; compact?: boolean }) {
  if (score == null || !category) return <span className="text-faint">—</span>;
  return (
    <span className="inline-flex items-center gap-2" title={`Risk ${score} (${category})`}>
      <span className="w-8 text-right font-medium tabular-nums">{Math.round(score)}</span>
      {!compact && (
        <span className="relative h-1 w-14 overflow-hidden rounded-full bg-line" aria-hidden>
          <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.max(score, 3)}%`, background: RISK_COLOR[category] }} />
        </span>
      )}
      <span className="text-xs capitalize" style={{ color: RISK_COLOR[category] }}>{category}</span>
    </span>
  );
}

const OUTCOME_TONE: Record<string, Tone> = { pass: "emerald", fail: "rose", review: "amber", error: "rose", skipped: "neutral" };
const OUTCOME_LABEL: Record<string, string> = { pass: "Passed", fail: "Failed", review: "Needs review", error: "Error", skipped: "Skipped" };
export const OutcomeBadge = ({ outcome }: { outcome: string }) => <Badge tone={OUTCOME_TONE[outcome] ?? "neutral"}>{OUTCOME_LABEL[outcome] ?? outcome}</Badge>;

const PRIORITY_TONE: Record<string, Tone> = { low: "neutral", normal: "cyan", high: "amber", critical: "rose" };
export const PriorityBadge = ({ priority }: { priority: string }) => <Badge tone={PRIORITY_TONE[priority]} className="capitalize">{priority}</Badge>;

export function EmptyState({ icon: Icon = Inbox, title, children, action }: { icon?: LucideIcon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-14 text-center">
      <Icon className="mb-3 size-6 text-faint" strokeWidth={1.5} />
      <p className="font-medium">{title}</p>
      {children && <p className="mt-1 max-w-[52ch] text-[13px] text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  const title = e?.status === 403 ? "You don't have access to this" : e?.status === 404 ? "Not found" : "Couldn't load this data";
  return (
    <div role="alert" className="flex flex-col items-center px-6 py-14 text-center">
      <AlertTriangle className="mb-3 size-6 text-amber" strokeWidth={1.5} />
      <p className="font-medium">{title}</p>
      <p className="mt-1 max-w-[52ch] text-[13px] text-muted">
        {e?.message ?? "The server didn't respond. Check that the API is running."}
        {e?.requestId && <span className="mt-1 block text-xs text-faint">Request ID {e.requestId}</span>}
      </p>
      {onRetry && e?.status !== 403 && <Button className="mt-4" onClick={onRetry}>Try again</Button>}
    </div>
  );
}

export function TableSkeleton({ rows = 8, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-0" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex h-11 items-center gap-6 border-b border-line/60 px-4">
          {Array.from({ length: cols }).map((__, j) => <Skeleton key={j} className={cn("h-3", j === 0 ? "w-40" : "w-20")} />)}
        </div>
      ))}
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total ? (page - 1) * pageSize + 1 : 0;
  return (
    <div className="flex items-center justify-between px-4 py-3 text-xs text-muted">
      <span>{from}–{Math.min(page * pageSize, total)} of {total.toLocaleString()}</span>
      <div className="flex items-center gap-1">
        <Button size="icon" variant="ghost" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft /></Button>
        <span className="px-1 tabular-nums">Page {page} of {pages}</span>
        <Button size="icon" variant="ghost" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}><ChevronRight /></Button>
      </div>
    </div>
  );
}

/** Confirmation for consequential actions. The confirm button repeats the action's name. */
export function ConfirmDialog({ trigger, title, description, confirmLabel, tone = "primary", onConfirm, children }: {
  trigger: ReactNode; title: string; description: string; confirmLabel: string; tone?: "primary" | "danger";
  onConfirm: () => Promise<unknown> | void; children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <span onClick={() => setOpen(true)} className="contents">{trigger}</span>
      <DialogContent title={title} description={description}>
        {children}
        <div className="mt-5 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant={tone} disabled={busy} onClick={async () => {
            setBusy(true);
            try { await onConfirm(); setOpen(false); } finally { setBusy(false); }
          }}>{busy ? "Working…" : confirmLabel}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function Section({ title, aside, children, className }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("panel", className)}>
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <h2 className="text-[13.5px] font-semibold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-2.5 text-[13px]">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function useToast() {
  return (msg: string, tone: "ok" | "error" = "ok") => window.dispatchEvent(new CustomEvent("sf:toast", { detail: { msg, tone } }));
}
