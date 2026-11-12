import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { DecisionBadge, EmptyState, ErrorState, PageHeader, Pagination, RISK_COLOR, RiskScore, Section, TableSkeleton } from "@/components/common";
import { Skeleton, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtRelative, humanize } from "@/lib/format";
import type { Page, RiskAssessment, RiskCategory, VerificationSummary } from "@/lib/types";

import { useFilters } from "./Verifications";

interface Summary {
  categories: Record<RiskCategory, number>; histogram: { bucket: string; count: number }[];
  avg_factor_points: Record<string, number>; weights: Record<string, number>; thresholds: Record<string, number>; floors: Record<string, number>;
}

const bucketColor = (b: string, t: Record<string, number>) => {
  const lo = Number(b.split("-")[0]);
  return lo >= t.critical ? RISK_COLOR.critical : lo >= t.high ? RISK_COLOR.high : lo >= t.medium ? RISK_COLOR.medium : RISK_COLOR.low;
};

export default function RiskPage() {
  const navigate = useNavigate();
  const [f, set] = useFilters(["category", "page"]);
  const page = Number(f.page || 1);
  const s = useQuery({ queryKey: ["risk-summary"], queryFn: () => api<Summary>("/risk-assessments/summary") });
  const list = useQuery({
    queryKey: ["risk", f],
    queryFn: () => api<Page<RiskAssessment & { verification: VerificationSummary }>>("/risk-assessments", { query: { category: f.category, page, page_size: 20 } }),
    placeholderData: keepPreviousData,
  });
  const factorMax = s.data ? Math.max(...Object.values(s.data.avg_factor_points), 0.01) : 1;
  return (
    <>
      <PageHeader title="Risk" description="How risk is distributed across assessments, and which factors drive it. Scores are a transparent weighted sum, not a model prediction." />
      <div className="grid items-start gap-5 xl:grid-cols-[1fr_380px]">
        <Section title="Score distribution" aside={s.data && <span className="text-xs text-faint">Medium from {s.data.thresholds.medium}, high from {s.data.thresholds.high}, critical from {s.data.thresholds.critical}</span>}>
          <div className="h-[300px] px-2 pb-3 pt-4">
            {!s.data ? <Skeleton className="mx-4 h-full" /> : s.data.histogram.length === 0 ? <p className="grid h-full place-items-center text-muted">No assessments yet.</p> : (
              <ResponsiveContainer>
                <BarChart data={s.data.histogram}>
                  <CartesianGrid vertical={false} stroke="#2a3036" />
                  <XAxis dataKey="bucket" stroke="#5e666e" fontSize={11} tickLine={false} axisLine={false} />
                  <YAxis stroke="#5e666e" fontSize={11} tickLine={false} axisLine={false} width={36} allowDecimals={false} scale="sqrt" />
                  <Tooltip cursor={{ fill: "#21262b" }} contentStyle={{ background: "#1a1e22", border: "1px solid #2a3036", borderRadius: 8, fontSize: 12 }} labelStyle={{ color: "#e6eaed" }} formatter={(v) => [v, "Assessments"]} />
                  <Bar dataKey="count" radius={[3, 3, 0, 0]}>
                    {s.data.histogram.map((h) => <Cell key={h.bucket} fill={bucketColor(h.bucket, s.data!.thresholds)} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
          <p className="px-4 pb-3 text-xs text-faint">Vertical axis uses a square-root scale so small high-risk groups stay visible.</p>
        </Section>
        <Section title="What drives risk" aside={<span className="text-xs text-faint">Mean points per assessment</span>}>
          <ul className="space-y-3 p-4">
            {!s.data ? <Skeleton className="h-32" /> : Object.entries(s.data.avg_factor_points).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
              <li key={k} className="text-[13px]">
                <div className="mb-1 flex justify-between"><span>{k === "floor" ? "Score floors" : humanize(k)}</span>
                  <span className="tabular-nums text-muted">{v.toFixed(2)}{s.data!.weights[k] != null && <span className="ml-2 text-faint">weight {s.data!.weights[k]}</span>}</span></div>
                <div className="h-1.5 overflow-hidden rounded-full bg-line"><div className="h-full rounded-full bg-cyan" style={{ width: `${(v / factorMax) * 100}%` }} /></div>
              </li>
            ))}
          </ul>
          <p className="border-t border-line px-4 py-3 text-xs text-faint">Floors guarantee a minimum score for decisive signals, such as a strong watchlist match ({s.data?.floors.strong_watchlist_match}).</p>
        </Section>
      </div>
      <div className="panel mt-5">
        <div className="flex items-center justify-between border-b border-line p-3">
          <div className="flex gap-1" role="tablist" aria-label="Risk category">
            {(["", "critical", "high", "medium", "low"] as const).map((c) => (
              <button key={c || "all"} role="tab" aria-selected={f.category === c} onClick={() => set("category", c)}
                className={`rounded-md px-2.5 py-1 text-[13px] capitalize ${f.category === c ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>
                {c || "All"}{c && s.data && <span className="ml-1.5 tabular-nums text-faint">{s.data.categories[c]}</span>}
              </button>
            ))}
          </div>
          <span className="hidden text-xs text-faint sm:block">Highest score first</span>
        </div>
        {list.isError ? <ErrorState error={list.error} /> : !list.data ? <TableSkeleton /> : list.data.items.length === 0 ? <EmptyState title="No assessments in this category" /> : (
          <>
            <Table>
              <thead><tr><Th>Customer</Th><Th>Score</Th><Th>Top factor</Th><Th>Decision</Th><Th className="text-right">Assessed</Th></tr></thead>
              <tbody>{list.data.items.map((r) => {
                const top = r.breakdown[0];
                return (
                  <tr key={r.id} className="cursor-pointer hover:bg-raised/60" onClick={() => navigate(`/verifications/${r.verification_id}`)}>
                    <Td><Link to={`/verifications/${r.verification_id}`} className="font-medium hover:text-cyan">{r.verification.customer_name}</Link><span className="block text-xs text-faint">{r.verification.external_ref}</span></Td>
                    <Td><RiskScore score={r.score} category={r.category} /></Td>
                    <Td className="max-w-[340px] truncate text-muted">{top && top.points > 0 ? top.explanation : "No contributing factors"}</Td>
                    <Td><DecisionBadge decision={r.verification.decision} /></Td>
                    <Td className="text-right text-muted">{fmtRelative(r.created_at)}</Td>
                  </tr>
                );
              })}</tbody>
            </Table>
            <Pagination page={page} pageSize={20} total={list.data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </div>
    </>
  );
}
