import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState, ErrorState, PageHeader, Pagination, PriorityBadge, RiskScore, TableSkeleton } from "@/components/common";
import { Badge, Select, Table, Td, Th, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import type { Case, Page } from "@/lib/types";

import { useFilters } from "./Verifications";

export const CASE_STATUS: Record<string, [string, Tone]> = {
  open: ["Open", "cyan"], in_review: ["In review", "cyan"], escalated: ["Escalated", "amber"], approved: ["Approved", "emerald"], rejected: ["Rejected", "rose"],
};

function Sla({ due, closed }: { due: string | null; closed: boolean }) {
  if (!due || closed) return <span className="text-faint">—</span>;
  const overdue = new Date(due).getTime() < Date.now();
  return <span className={overdue ? "text-rose" : "text-muted"}>{overdue ? `Overdue ${fmtRelative(due).replace(" ago", "")}` : `Due ${fmtRelative(due)}`}</span>;
}

const VIEWS = [["open", "Open queue"], ["me", "Assigned to me"], ["escalated", "Escalated"], ["closed", "Decided"]] as const;

export default function CasesPage() {
  const navigate = useNavigate();
  const [f, set] = useFilters(["view", "priority", "page", "sort"]);
  const view = f.view || "open";
  const page = Number(f.page || 1);
  const query = {
    open: { open_only: true }, me: { assigned: "me", open_only: true }, escalated: { status: "escalated" }, closed: { closed_only: true },
  }[view as "open"];
  const q = useQuery({
    queryKey: ["cases", f],
    queryFn: () => api<Page<Case>>("/cases", { query: { ...query, priority: f.priority, page, page_size: 25, sort: f.sort || (view === "closed" ? "-created_at" : "sla_due_at") } }),
    placeholderData: keepPreviousData,
  });
  const items = q.data?.items;
  return (
    <>
      <PageHeader title="Cases" description="Verifications routed to people. Reviewers work cases assigned to them; escalated cases go to compliance analysts." />
      <div className="panel">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <div className="flex gap-1" role="tablist" aria-label="Case view">
            {VIEWS.map(([k, l]) => (
              <button key={k} role="tab" aria-selected={view === k} onClick={() => set("view", k)}
                className={`rounded-md px-2.5 py-1 text-[13px] ${view === k ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}>{l}</button>
            ))}
          </div>
          <Select aria-label="Priority" className="ml-auto w-36" value={f.priority} onChange={(e) => set("priority", e.target.value)}>
            <option value="">Any priority</option><option value="critical">Critical</option><option value="high">High</option><option value="normal">Normal</option>
          </Select>
        </div>
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !items ? <TableSkeleton /> : items.length === 0 ? (
          <EmptyState title={view === "me" ? "Nothing assigned to you" : "No cases here"}>{view === "me" ? "Cases assigned to you by an analyst will appear here." : "Cases are opened when a policy routes a verification to manual review."}</EmptyState>
        ) : (
          <>
            <Table>
              <thead><tr><Th>Customer</Th><Th>Reason</Th><Th>Priority</Th><Th>Status</Th><Th>Risk</Th><Th>Assignee</Th><Th className="text-right">SLA</Th></tr></thead>
              <tbody>{items.map((c) => (
                <tr key={c.id} className="cursor-pointer hover:bg-raised/60" onClick={() => navigate(`/cases/${c.id}`)}>
                  <Td><Link to={`/cases/${c.id}`} className="font-medium hover:text-cyan">{c.customer_name}</Link><span className="block text-xs capitalize text-faint">{c.customer_type}</span></Td>
                  <Td className="max-w-[300px] truncate text-muted" title={c.reason}>{c.reason}</Td>
                  <Td><PriorityBadge priority={c.priority} /></Td>
                  <Td><Badge tone={CASE_STATUS[c.status]?.[1]}>{CASE_STATUS[c.status]?.[0]}</Badge></Td>
                  <Td><RiskScore score={c.risk_score} category={c.risk_category} compact /></Td>
                  <Td className="text-muted">{c.assignee_name ?? <span className="text-faint">Unassigned</span>}</Td>
                  <Td className="text-right"><Sla due={c.sla_due_at} closed={["approved", "rejected"].includes(c.status)} /></Td>
                </tr>
              ))}</tbody>
            </Table>
            <Pagination page={page} pageSize={25} total={q.data!.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </div>
    </>
  );
}
