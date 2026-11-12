import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import { ArrowLeft, Check, ChevronDown, Circle, Minus } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { DecisionBadge, ErrorState, KeyValue, OutcomeBadge, RISK_COLOR, RiskScore, Section } from "@/components/common";
import { Badge, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { ACTION_LABEL, fmtDate, fmtMs, humanize, PROVIDER_LABEL } from "@/lib/format";
import type { PolicyTrace, ProviderResult, RiskAssessment, VerificationDetail, WorkflowStep } from "@/lib/types";
import { cn } from "@/lib/utils";

const STEP_LABEL: Record<string, string> = {
  load_context: "Load configuration", select_providers: "Select providers", normalize_and_derive_signals: "Normalize and derive signals",
  risk_scoring: "Risk scoring", policy_evaluation: "Policy evaluation", persist: "Persist decision",
};
const stepLabel = (n: string) => (n.startsWith("provider:") ? `${PROVIDER_LABEL[n.slice(9)] ?? n.slice(9)} check` : STEP_LABEL[n] ?? humanize(n));
const STATUS_COLOR: Record<string, string> = { pass: "#2e9e76", completed: "#5ba4b4", fail: "#c46a62", error: "#c46a62", review: "#d4a04a", skipped: "#5e666e" };

/** Span waterfall: each step positioned on the request's own timeline, so concurrency is visible. */
function Waterfall({ steps, total }: { steps: WorkflowStep[]; total: number }) {
  const span = Math.max(total, ...steps.map((s) => s.offset_ms + (s.duration_ms ?? 0)), 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <div className="px-4 pb-4 pt-3">
      <div className="relative ml-[180px] h-5 text-[11px] text-faint">
        {ticks.map((t) => <span key={t} className={cn("absolute whitespace-nowrap tabular-nums", t === 0 ? "" : t === 1 ? "-translate-x-full" : "-translate-x-1/2")} style={{ left: `${t * 100}%` }}>{fmtMs(span * t)}</span>)}
      </div>
      <ul className="space-y-1">
        {steps.map((s, i) => {
          const left = (s.offset_ms / span) * 100;
          const width = s.duration_ms == null ? 0 : Math.max((s.duration_ms / span) * 100, 0.6);
          const color = STATUS_COLOR[s.status] ?? "#5ba4b4";
          const isProvider = s.name.startsWith("provider:");
          return (
            <li key={s.name + i} className="grid grid-cols-[180px_1fr] items-center gap-0 text-[12.5px]">
              <span className={cn("truncate pr-3", isProvider ? "pl-3 text-fg" : "text-muted")}>
                {stepLabel(s.name)}
                {isProvider && s.attempts! > 1 && <span className="ml-1.5 text-xs text-amber">×{s.attempts}</span>}
                {s.cache_hit && <span className="ml-1.5 text-xs text-cyan">cached</span>}
              </span>
              <span className="relative h-6 border-l border-line">
                {ticks.slice(1).map((t) => <span key={t} className="absolute inset-y-0 w-px bg-line/50" style={{ left: `${t * 100}%` }} />)}
                {s.duration_ms == null ? (
                  <span className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rotate-45" style={{ left: `${left}%`, background: color }} title="Committed" />
                ) : (
                  <motion.span
                    className="absolute top-1/2 h-3 -translate-y-1/2 rounded-[3px]"
                    style={{ left: `${left}%`, background: color, opacity: isProvider ? 1 : 0.75 }}
                    initial={{ width: 0 }} animate={{ width: `${width}%` }}
                    transition={{ delay: 0.15 + (s.offset_ms / span) * 0.6, duration: 0.45, ease: [0.2, 0.7, 0.2, 1] }}
                    title={`${fmtMs(s.duration_ms)}${s.error_code ? `, ${s.error_code}` : ""}`}
                  />
                )}
                <span className="absolute top-1/2 -translate-y-1/2 pl-1.5 text-[11px] tabular-nums text-faint" style={{ left: `${Math.min(left + width, 88)}%` }}>
                  {s.duration_ms != null && fmtMs(s.duration_ms)}{s.error_code && <span className="ml-1 text-rose">{s.error_code}</span>}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PolicyChain({ trace }: { trace: PolicyTrace }) {
  const [open, setOpen] = useState<string | null>(trace.trace.find((t) => t.applied && t.action !== "flag")?.policy_id ?? null);
  return (
    <ol className="divide-y divide-line/70">
      {trace.trace.map((t) => {
        const decisive = t.applied && t.action !== "flag";
        const expanded = open === t.policy_id;
        return (
          <li key={t.policy_id}>
            <button type="button" aria-expanded={expanded} onClick={() => setOpen(expanded ? null : t.policy_id)}
              className={cn("flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-raised/50", decisive && "bg-emerald-soft/40")}>
              <span className="w-7 text-xs tabular-nums text-faint">{t.priority}</span>
              <span className={cn("grid size-4 place-items-center rounded-full", t.matched ? "bg-amber/20 text-amber" : "text-faint")}>
                {t.matched ? <Check className="size-3" strokeWidth={3} /> : <Minus className="size-3" />}
              </span>
              <span className="min-w-0 flex-1">
                <span className={cn("block truncate text-[13px]", !t.matched && "text-muted")}>{t.policy_name}<span className="ml-1.5 text-xs text-faint">v{t.version}</span></span>
              </span>
              {decisive ? <Badge tone="emerald">Decided</Badge> : t.matched && t.applied ? <Badge tone="cyan">Flagged</Badge> : t.matched ? <span className="text-xs text-faint">Matched, superseded</span> : null}
              <span className="w-24 text-right text-xs text-muted">{ACTION_LABEL[t.action]}</span>
              <ChevronDown className={cn("size-3.5 text-faint transition-transform", expanded && "rotate-180")} />
            </button>
            {expanded && (
              <div className="px-4 pb-3 pl-[72px]">
                <p className="mb-2 text-xs text-muted">{t.reason}</p>
                <ul className="space-y-1 text-xs">
                  {t.conditions.map((c, i) => (
                    <li key={i} className="flex flex-wrap items-center gap-x-2">
                      <Circle className={cn("size-2", c.matched ? "fill-amber text-amber" : "text-faint")} />
                      <code className="text-cyan">{c.field}</code><span className="text-faint">{c.op}</span>
                      <code>{JSON.stringify(c.expected)}</code>
                      <span className="text-faint">actual</span><code className={c.matched ? "text-amber" : "text-muted"}>{JSON.stringify(c.actual)}</code>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </li>
        );
      })}
      <li className="flex items-center gap-3 px-4 py-2.5 text-[13px]">
        <span className="w-7" />
        <span className="flex-1 text-muted">
          {trace.decided_by === "risk_fallback" ? "No terminal policy matched; the risk band default applied." :
            trace.decided_by === "system_guard" ? "System guard: automated approval requires every check to pass." : "Remaining policies were evaluated for the record only."}
        </span>
      </li>
    </ol>
  );
}

function ProviderCard({ r }: { r: ProviderResult }) {
  const [raw, setRaw] = useState(false);
  const signals = Object.entries(r.normalized.signals ?? {});
  return (
    <div className="min-w-0 p-4">
      <div className="flex items-center justify-between gap-2">
        <p className="font-medium">{PROVIDER_LABEL[r.provider_key] ?? r.provider_key}</p>
        <OutcomeBadge outcome={r.outcome} />
      </div>
      <p className="mt-0.5 text-xs text-faint">{r.provider_key}, simulated</p>
      <div className="mt-3 grid grid-cols-3 gap-2 border-y border-line py-2.5 text-xs">
        <div><p className="text-faint">Confidence</p><p className="mt-0.5 tabular-nums">{r.outcome === "error" || r.outcome === "skipped" ? "—" : r.confidence.toFixed(2)}</p></div>
        <div><p className="text-faint">Attempts</p><p className={cn("mt-0.5 tabular-nums", r.attempts > 1 && "text-amber")}>{r.attempts}</p></div>
        <div><p className="text-faint">Latency</p><p className="mt-0.5 tabular-nums">{r.cache_hit ? "cached" : fmtMs(r.latency_ms)}</p></div>
      </div>
      <ul className="mt-3 space-y-1 text-[13px]">{r.normalized.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
      {signals.length > 0 && (
        <dl className="mt-3 space-y-1 text-xs">
          {signals.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3"><dt className="text-muted">{humanize(k)}</dt><dd className="tabular-nums">{String(v)}</dd></div>
          ))}
        </dl>
      )}
      {r.raw_response != null && (
        <>
          <button type="button" className="mt-3 text-xs text-cyan hover:underline" onClick={() => setRaw(!raw)} aria-expanded={raw}>{raw ? "Hide" : "Show"} provider response</button>
          {raw && <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-base p-2.5 text-[11px] leading-relaxed text-muted">{JSON.stringify(r.raw_response, null, 2)}</pre>}
        </>
      )}
    </div>
  );
}

export function RiskBreakdown({ risk }: { risk: RiskAssessment }) {
  const max = Math.max(...risk.breakdown.map((b) => b.points), 1);
  return (
    <ul className="space-y-2.5 p-4">
      {risk.breakdown.map((b) => (
        <li key={b.factor} className="grid grid-cols-[130px_1fr_52px] items-center gap-3 text-[13px]">
          <span className={cn(b.points === 0 && "text-muted")}>{b.factor === "floor" ? "Score floor" : humanize(b.factor)}</span>
          <span className="min-w-0">
            <span className="block h-1.5 overflow-hidden rounded-full bg-line">
              <span className="block h-full rounded-full" style={{ width: `${(b.points / max) * 100}%`, background: b.factor === "floor" ? RISK_COLOR[risk.category] : "#5ba4b4" }} />
            </span>
            <span className="mt-1 block truncate text-xs text-faint" title={b.explanation}>{b.explanation}{b.weight ? `, weight ${b.weight}` : ""}</span>
          </span>
          <span className="text-right tabular-nums">+{b.points.toFixed(1)}</span>
        </li>
      ))}
    </ul>
  );
}

export default function VerificationDetailPage() {
  const { id } = useParams();
  const q = useQuery({
    queryKey: ["verification", id],
    queryFn: () => api<VerificationDetail>(`/verifications/${id}`),
    refetchInterval: (query) => (["queued", "processing"].includes(query.state.data?.status ?? "") ? 1500 : false),
  });
  const v = q.data;
  if (q.isError) return <div className="panel"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>;
  if (!v) return <div className="space-y-4"><Skeleton className="h-8 w-72" /><Skeleton className="h-28" /><Skeleton className="h-72" /></div>;

  const trace = v.policy_trace;
  const decidedBy = trace?.trace.find((t) => t.policy_id === trace.decided_by);
  return (
    <>
      <Link to="/verifications" className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-fg"><ArrowLeft className="size-3.5" />Verifications</Link>
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[22px] font-semibold">{v.customer.full_name}</h1>
            <DecisionBadge decision={v.decision} status={v.status} />
          </div>
          <p className="mt-1 text-[13px] text-muted">
            <span className="capitalize">{v.verification_type}</span> verification for <Link to={`/customers/${v.customer_id}`} className="text-cyan hover:underline">{v.external_ref}</Link>, submitted {fmtDate(v.created_at)}
          </p>
        </div>
        {v.case && <Link to={`/cases/${v.case.id}`} className="inline-flex h-8 items-center rounded-md border border-line-strong bg-raised px-3 text-[13px] font-medium hover:border-faint">Open case</Link>}
      </header>

      {v.status !== "completed" ? (
        <div className="panel p-6 text-[13px] text-muted">{v.status === "failed" ? `The workflow failed: ${v.error}` : "Checks are running. This page updates automatically."}</div>
      ) : (
        <div className="space-y-5">
          <div className="panel grid divide-y divide-line md:grid-cols-3 md:divide-x md:divide-y-0">
            <div className="p-4">
              <p className="text-xs text-muted">Decision</p>
              <p className="mt-1.5 text-[15px] font-medium">{trace?.reason}</p>
              <p className="mt-1 text-xs text-faint">
                {v.decision_source === "manual" ? `Automated outcome was ${v.automated_decision?.replace("_", " ")}; overridden by a reviewer.` :
                  decidedBy ? `Policy "${decidedBy.policy_name}" v${decidedBy.version}` : trace?.decided_by === "system_guard" ? "System guard" : "Risk band default"}
              </p>
              {!!trace?.flags.length && <div className="mt-2 flex flex-wrap gap-1.5">{trace.flags.map((f) => <Badge key={f} tone="cyan">{f}</Badge>)}</div>}
            </div>
            <div className="p-4">
              <p className="text-xs text-muted">Risk score</p>
              <div className="mt-1.5"><RiskScore score={v.risk_assessment?.score} category={v.risk_assessment?.category} /></div>
              <p className="mt-2 text-xs text-faint">Model {v.risk_assessment?.model_version}, {v.risk_assessment?.breakdown.filter((b) => b.points > 0).length} contributing factors</p>
            </div>
            <div className="p-4">
              <p className="text-xs text-muted">End-to-end processing</p>
              <p className="mt-1.5 text-[15px] font-medium tabular-nums">{fmtMs(v.latency_ms)}</p>
              <p className="mt-1 text-xs text-faint">{v.workflow?.execution_mode === "queue" ? "Processed by a background worker" : "Processed inline"}, {v.provider_results.reduce((a, r) => a + r.attempts, 0)} provider calls</p>
            </div>
          </div>

          <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
            <Section title="Execution trace" aside={<span className="text-xs text-faint">Provider checks run concurrently</span>}>
              {v.workflow ? <Waterfall steps={v.workflow.steps} total={v.workflow.duration_ms ?? 0} /> : <p className="p-4 text-muted">No workflow record.</p>}
            </Section>
            <Section title="Policy evaluation" aside={<span className="text-xs text-faint">In priority order</span>}>
              {trace ? <PolicyChain trace={trace} /> : <p className="p-4 text-muted">Not evaluated.</p>}
            </Section>
          </div>

          <Section title="Provider results">
            <div className="grid divide-y divide-line md:grid-cols-[repeat(var(--n),minmax(0,1fr))] md:divide-x md:divide-y-0" style={{ "--n": v.provider_results.length } as React.CSSProperties}>
              {v.provider_results.map((r) => <ProviderCard key={r.id} r={r} />)}
            </div>
          </Section>

          <div className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
            <Section title="Risk breakdown" aside={<span className="text-xs text-faint">Points out of 100</span>}>
              {v.risk_assessment && <RiskBreakdown risk={v.risk_assessment} />}
            </Section>
            <Section title="Customer" aside={<span className="text-xs text-faint">{v.customer.pii_masked ? "Personal data masked" : "Unmasked"}</span>}>
              <div className="p-4">
                <KeyValue items={[
                  ["Reference", v.customer.external_ref], ["Name", v.customer.full_name], ["Date of birth", v.customer.dob],
                  ["Country", v.customer.country], ["Document", v.customer.document_type ? `${humanize(v.customer.document_type)} ${v.customer.document_number ?? ""}` : null],
                  ["Registration", v.customer.registration_number], ["Email", v.customer.email],
                ]} />
              </div>
            </Section>
          </div>
        </div>
      )}
    </>
  );
}
