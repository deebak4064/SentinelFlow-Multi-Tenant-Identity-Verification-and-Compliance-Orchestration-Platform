import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";

import { EmptyState, ErrorState, PageHeader, Pagination, TableSkeleton } from "@/components/common";
import { Badge, Input, Select, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtDate, humanize } from "@/lib/format";
import type { AuditEvent, Page } from "@/lib/types";
import { cn } from "@/lib/utils";

import { useFilters } from "./Verifications";

const PREFIXES = [["", "All events"], ["auth", "Sign-ins and sessions"], ["verification", "Verifications"], ["case", "Cases"], ["policy", "Policies"],
  ["customer.pii", "Personal data reveals"], ["member", "Membership"], ["api_key", "API keys"], ["provider", "Providers"], ["settings", "Settings"], ["authz.denied", "Permission denials"]];

export default function AuditPage() {
  const [f, set] = useFilters(["action", "resource_id", "page"]);
  const page = Number(f.page || 1);
  const [open, setOpen] = useState<number | null>(null);
  const q = useQuery({ queryKey: ["audit", f], queryFn: () => api<Page<AuditEvent>>("/audit-events", { query: { ...f, page, page_size: 50 } }), placeholderData: keepPreviousData });
  return (
    <>
      <PageHeader title="Audit log" description="Append-only record of every security-relevant action. The database rejects updates and deletes to this table." />
      <div className="panel">
        <div className="flex flex-wrap gap-2 border-b border-line p-3">
          <Select aria-label="Event type" className="w-56" value={f.action} onChange={(e) => set("action", e.target.value)}>{PREFIXES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select>
          <Input aria-label="Resource ID" placeholder="Filter by resource ID" className="w-80" defaultValue={f.resource_id} onBlur={(e) => set("resource_id", e.target.value.trim())}
            onKeyDown={(e) => e.key === "Enter" && set("resource_id", (e.target as HTMLInputElement).value.trim())} />
        </div>
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <TableSkeleton rows={10} /> : q.data.items.length === 0 ? <EmptyState title="No events match" /> : (
          <>
            <Table>
              <thead><tr><Th className="w-8" /><Th>Time</Th><Th>Event</Th><Th>Actor</Th><Th>Resource</Th><Th>IP</Th></tr></thead>
              <tbody>{q.data.items.map((e) => (
                <Fragment key={e.id}>
                  <tr className="cursor-pointer hover:bg-raised/60" onClick={() => setOpen(open === e.id ? null : e.id)} aria-expanded={open === e.id}>
                    <Td><ChevronRight className={cn("size-3.5 text-faint transition-transform", open === e.id && "rotate-90")} /></Td>
                    <Td className="whitespace-nowrap text-muted">{fmtDate(e.created_at)}</Td>
                    <Td>{e.action === "authz.denied" ? <Badge tone="rose">Permission denied</Badge> : humanize(e.action)}</Td>
                    <Td><span>{e.actor_label ?? e.actor_type}</span><span className="block text-xs text-faint">{humanize(e.actor_type)}</span></Td>
                    <Td className="text-muted">{e.resource_type ? <>{humanize(e.resource_type)} <code className="text-[11px] text-faint">{e.resource_id?.slice(0, 8)}</code></> : "—"}</Td>
                    <Td className="text-muted">{e.ip ?? "—"}</Td>
                  </tr>
                  {open === e.id && (
                    <tr><Td colSpan={6} className="bg-base/60">
                      <pre className="overflow-x-auto py-2 text-xs text-muted">{JSON.stringify({ id: e.id, request_id: e.request_id, actor_id: e.actor_id, resource_id: e.resource_id, details: e.details }, null, 2)}</pre>
                    </Td></tr>
                  )}
                </Fragment>
              ))}</tbody>
            </Table>
            <Pagination page={page} pageSize={50} total={q.data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </div>
    </>
  );
}
