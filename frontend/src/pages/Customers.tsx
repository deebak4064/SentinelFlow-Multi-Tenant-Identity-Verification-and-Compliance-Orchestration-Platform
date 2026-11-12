import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { EmptyState, ErrorState, PageHeader, Pagination, RiskScore, TableSkeleton } from "@/components/common";
import { Badge, Input, Select, Table, Td, Th, type Tone } from "@/components/ui";
import { api } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import type { Customer, Page } from "@/lib/types";

import { useFilters } from "./Verifications";

export const CUSTOMER_STATUS: Record<string, [string, Tone]> = {
  verified: ["Verified", "emerald"], rejected: ["Rejected", "rose"], in_review: ["In review", "amber"], pending: ["Pending", "cyan"],
};

export default function CustomersPage() {
  const navigate = useNavigate();
  const [f, set] = useFilters(["q", "status", "customer_type", "risk_category", "page", "sort"]);
  const page = Number(f.page || 1);
  const q = useQuery({ queryKey: ["customers", f], queryFn: () => api<Page<Customer>>("/customers", { query: { ...f, page, page_size: 25 } }), placeholderData: keepPreviousData });
  return (
    <>
      <PageHeader title="Customers" description="People and businesses your organization has verified. Personal data is masked unless you reveal it on a profile." />
      <div className="panel">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <div className="relative w-full sm:w-72">
            <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-faint" />
            <Input aria-label="Search customers" placeholder="Reference or company name" className="pl-8" defaultValue={f.q}
              onKeyDown={(e) => e.key === "Enter" && set("q", (e.target as HTMLInputElement).value.trim())} onBlur={(e) => set("q", e.target.value.trim())} />
          </div>
          <Select aria-label="Status" className="w-36" value={f.status} onChange={(e) => set("status", e.target.value)}>
            <option value="">All statuses</option><option value="verified">Verified</option><option value="in_review">In review</option><option value="rejected">Rejected</option>
          </Select>
          <Select aria-label="Type" className="w-36" value={f.customer_type} onChange={(e) => set("customer_type", e.target.value)}>
            <option value="">All types</option><option value="individual">Individual</option><option value="business">Business</option>
          </Select>
          <Select aria-label="Sort" className="ml-auto w-40" value={f.sort} onChange={(e) => set("sort", e.target.value)}>
            <option value="">Recently added</option><option value="-risk_score">Highest risk</option><option value="external_ref">Reference A–Z</option>
          </Select>
        </div>
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <TableSkeleton /> : q.data.items.length === 0 ? (
          <EmptyState title={f.q || f.status ? "No customers match" : "No customers yet"}>Customers appear here after their first verification.</EmptyState>
        ) : (
          <>
            <Table>
              <thead><tr><Th>Customer</Th><Th>Type</Th><Th>Country</Th><Th>Status</Th><Th>Latest risk</Th><Th className="text-right">Added</Th></tr></thead>
              <tbody>
                {q.data.items.map((c) => (
                  <tr key={c.id} className="cursor-pointer hover:bg-raised/60" onClick={() => navigate(`/customers/${c.id}`)}>
                    <Td><Link to={`/customers/${c.id}`} className="font-medium hover:text-cyan">{c.full_name}</Link><span className="block text-xs text-faint">{c.external_ref}</span></Td>
                    <Td className="capitalize text-muted">{c.customer_type}</Td>
                    <Td className="text-muted">{c.country}</Td>
                    <Td><Badge tone={CUSTOMER_STATUS[c.status]?.[1]}>{CUSTOMER_STATUS[c.status]?.[0] ?? c.status}</Badge></Td>
                    <Td><RiskScore score={c.latest_risk_score} category={c.latest_risk_category} /></Td>
                    <Td className="text-right text-muted">{fmtRelative(c.created_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <Pagination page={page} pageSize={25} total={q.data.total} onPage={(p) => set("page", String(p))} />
          </>
        )}
      </div>
    </>
  );
}
