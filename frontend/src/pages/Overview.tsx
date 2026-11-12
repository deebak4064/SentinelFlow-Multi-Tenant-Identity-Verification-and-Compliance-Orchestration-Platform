import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { ErrorState, PageHeader, RISK_COLOR, Section } from "@/components/common";
import { Select, Skeleton, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtMs, fmtNum, fmtPct, PROVIDER_LABEL } from "@/lib/format";
import { useState } from "react";

interface Overview {
  window_days: number; generated_at: string; total_verifications: number;
  decision_distribution: Record<"approved" | "rejected" | "manual_review" | "escalated", number>;
  avg_processing_latency_ms: number; p50_processing_latency_ms: number; p95_processing_latency_ms: number;
  provider_success_rate: number | null; providers: { provider_key: string; calls: number; success_rate: number | null; avg_latency_ms: number }[];
  high_risk_open_cases: number; open_cases: number; risk_distribution: Record<string, number>;
  throughput: { date: string; total: number; approved: number; rejected: number; review: number }[];
  workflow_health: { completed: number; failed: number; running: number };
}

const DECISIONS = [
  { key: "approved", label: "Approved", color: "#2e9e76" },
  { key: "manual_review", label: "Manual review", color: "#d4a04a" },
  { key: "escalated", label: "Escalated", color: "#a8793a" },
  { key: "rejected", label: "Rejected", color: "#c46a62" },
] as const;

const axis = { stroke: "#5e666e", fontSize: 11, tickLine: false, axisLine: false } as const;

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="panel px-3 py-2 text-xs shadow-xl">
      <p className="mb-1 font-medium">{new Date(label).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })}</p>
      {payload.map((p: any) => (
        <p key={p.dataKey} className="flex items-center justify-between gap-6 text-muted">
          <span className="flex items-center gap-1.5"><span className="size-2 rounded-sm" style={{ background: p.color }} />{p.name}</span>
          <span className="tabular-nums text-fg">{p.value}</span>
        </p>
      ))}
    </div>
  );
}

function Stat({ label, value, detail, warn }: { label: string; value: string; detail?: string; warn?: boolean }) {
  return (
    <div className="min-w-0 px-5 py-4">
      <p className="text-xs text-muted">{label}</p>
      <p className={`mt-1.5 text-[26px] font-semibold leading-none tabular-nums ${warn ? "text-amber" : ""}`}>{value}</p>
      {detail && <p className="mt-2 text-xs text-faint">{detail}</p>}
    </div>
  );
}

export default function OverviewPage() {
  const [days, setDays] = useState(14);
  const q = useQuery({ queryKey: ["overview", days], queryFn: () => api<Overview>("/dashboard/overview", { query: { days } }), refetchInterval: 30_000 });
  const d = q.data;
  const decided = d ? Object.values(d.decision_distribution).reduce((a, b) => a + b, 0) : 0;
  const autoRate = d && decided ? d.decision_distribution.approved / decided : null;
  const wf = d?.workflow_health;
  const wfTotal = wf ? wf.completed + wf.failed + wf.running : 0;

  return (
    <>
      <PageHeader
        title="Overview"
        description={d ? `Computed from stored verification data, refreshed ${new Date(d.generated_at).toLocaleTimeString()}.` : "Verification volume, decision outcomes and pipeline health."}
        actions={
          <Select aria-label="Time window" value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-36">
            <option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option>
          </Select>
        }
      />
      {q.isError ? <div className="panel"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : (
        <div className="space-y-5">
          <div className="panel grid grid-cols-2 divide-line md:grid-cols-5 md:divide-x [&>*:nth-child(n+3)]:border-t [&>*:nth-child(n+3)]:border-line md:[&>*:nth-child(n+3)]:border-t-0">
            {!d ? Array.from({ length: 5 }).map((_, i) => <div key={i} className="space-y-3 px-5 py-4"><Skeleton className="w-24" /><Skeleton className="h-7 w-20" /></div>) : <>
              <Stat label="Verifications" value={fmtNum(d.total_verifications)} detail={`${fmtPct(autoRate, 0)} approved automatically`} />
              <Stat label="Avg processing time" value={fmtMs(d.avg_processing_latency_ms)} detail={`p50 ${fmtMs(d.p50_processing_latency_ms)}, p95 ${fmtMs(d.p95_processing_latency_ms)}`} />
              <Stat label="Provider success rate" value={fmtPct(d.provider_success_rate)} detail={`${fmtNum(d.providers.reduce((a, p) => a + p.calls, 0))} provider calls`} />
              <Stat label="High-risk open cases" value={fmtNum(d.high_risk_open_cases)} detail={`${d.open_cases} open cases in total`} warn={d.high_risk_open_cases > 0} />
              <Stat label="Workflow failures" value={fmtNum(wf!.failed)} detail={`${fmtNum(wf!.completed)} completed, ${wf!.running} running`} warn={wf!.failed > 0} />
            </>}
          </div>

          <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
            <Section title="Throughput by decision" aside={<Legend />}>
              <div className="h-[280px] px-2 pb-3 pt-4">
                {!d ? <Skeleton className="mx-4 h-full" /> : d.throughput.length === 0 ? <p className="grid h-full place-items-center text-[13px] text-muted">No verifications in this window.</p> : (
                  <ResponsiveContainer>
                    <BarChart data={d.throughput} barCategoryGap="28%">
                      <CartesianGrid vertical={false} stroke="#2a3036" />
                      <XAxis dataKey="date" {...axis} tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "numeric" })} />
                      <YAxis {...axis} width={36} allowDecimals={false} />
                      <Tooltip content={<ChartTooltip />} cursor={{ fill: "#21262b" }} />
                      <Bar dataKey="approved" name="Approved" stackId="a" fill="#2e9e76" />
                      <Bar dataKey="review" name="Review or escalated" stackId="a" fill="#d4a04a" />
                      <Bar dataKey="rejected" name="Rejected" stackId="a" fill="#c46a62" radius={[3, 3, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </Section>

            <Section title="Decision mix">
              <div className="p-4">
                {!d ? <Skeleton className="h-24" /> : <>
                  <div className="flex h-2.5 overflow-hidden rounded-full bg-line" role="img" aria-label="Decision distribution">
                    {DECISIONS.map((k) => <span key={k.key} style={{ width: `${decided ? (d.decision_distribution[k.key] / decided) * 100 : 0}%`, background: k.color }} />)}
                  </div>
                  <ul className="mt-4 space-y-2.5">
                    {DECISIONS.map((k) => (
                      <li key={k.key} className="flex items-center justify-between text-[13px]">
                        <Link to={`/verifications?decision=${k.key}`} className="flex items-center gap-2 text-muted hover:text-fg">
                          <span className="size-2 rounded-sm" style={{ background: k.color }} />{k.label}
                        </Link>
                        <span className="tabular-nums">{fmtNum(d.decision_distribution[k.key])}<span className="ml-2 inline-block w-12 text-right text-faint">{fmtPct(decided ? d.decision_distribution[k.key] / decided : 0, 0)}</span></span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-5 border-t border-line pt-4">
                    <p className="mb-2.5 text-xs text-muted">Risk categories</p>
                    <div className="grid grid-cols-4 gap-2">
                      {(["low", "medium", "high", "critical"] as const).map((c) => (
                        <Link to={`/risk?category=${c}`} key={c} className="rounded-md border border-line px-2 py-1.5 hover:border-line-strong">
                          <span className="block text-[15px] font-semibold tabular-nums">{d.risk_distribution[c]}</span>
                          <span className="text-xs capitalize" style={{ color: RISK_COLOR[c] }}>{c}</span>
                        </Link>
                      ))}
                    </div>
                  </div>
                </>}
              </div>
            </Section>
          </div>

          <div className="grid gap-5 xl:grid-cols-[1fr_340px]">
            <Section title="Provider health" aside={<Link to="/providers" className="text-xs text-cyan hover:underline">Manage providers</Link>}>
              {!d ? <div className="p-4"><Skeleton className="h-20" /></div> : (
                <Table>
                  <thead><tr><Th>Provider</Th><Th className="text-right">Calls</Th><Th>Success rate</Th><Th className="text-right">Avg latency</Th></tr></thead>
                  <tbody>
                    {d.providers.map((p) => (
                      <tr key={p.provider_key}>
                        <Td>{PROVIDER_LABEL[p.provider_key] ?? p.provider_key}<span className="ml-2 text-xs text-faint">{p.provider_key}</span></Td>
                        <Td className="text-right tabular-nums">{fmtNum(p.calls)}</Td>
                        <Td>
                          <span className="flex items-center gap-2.5">
                            <span className="h-1 w-24 overflow-hidden rounded-full bg-line"><span className="block h-full bg-emerald" style={{ width: `${(p.success_rate ?? 0) * 100}%` }} /></span>
                            <span className="tabular-nums">{fmtPct(p.success_rate)}</span>
                          </span>
                        </Td>
                        <Td className="text-right tabular-nums">{fmtMs(p.avg_latency_ms)}</Td>
                      </tr>
                    ))}
                    {!d.providers.length && <tr><Td colSpan={4} className="text-center text-muted">No provider calls in this window.</Td></tr>}
                  </tbody>
                </Table>
              )}
            </Section>
            <Section title="Workflow executions">
              <div className="space-y-3 p-4 text-[13px]">
                {!d ? <Skeleton className="h-20" /> : (["completed", "failed", "running"] as const).map((k) => (
                  <div key={k}>
                    <div className="mb-1 flex justify-between"><span className="capitalize text-muted">{k}</span><span className="tabular-nums">{fmtNum(wf![k])}</span></div>
                    <div className="h-1 overflow-hidden rounded-full bg-line">
                      <div className="h-full" style={{ width: `${wfTotal ? (wf![k] / wfTotal) * 100 : 0}%`, background: k === "completed" ? "#2e9e76" : k === "failed" ? "#c46a62" : "#5ba4b4" }} />
                    </div>
                  </div>
                ))}
              </div>
            </Section>
          </div>
        </div>
      )}
    </>
  );
}

function Legend() {
  return (
    <div className="hidden gap-4 text-xs text-muted sm:flex">
      {[["Approved", "#2e9e76"], ["Review or escalated", "#d4a04a"], ["Rejected", "#c46a62"]].map(([l, c]) => (
        <span key={l} className="flex items-center gap-1.5"><span className="size-2 rounded-sm" style={{ background: c }} />{l}</span>
      ))}
    </div>
  );
}
