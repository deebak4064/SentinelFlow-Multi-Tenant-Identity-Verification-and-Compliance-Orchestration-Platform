import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Eye, EyeOff } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { DecisionBadge, EmptyState, ErrorState, KeyValue, PriorityBadge, RiskScore, Section } from "@/components/common";
import { Badge, Button, Skeleton, Table, Td, Th } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRelative, humanize } from "@/lib/format";
import type { Case, Customer, VerificationSummary } from "@/lib/types";

import { CUSTOMER_STATUS } from "./Customers";

type Profile = Customer & { verifications: VerificationSummary[]; cases: Case[] };

export default function CustomerProfilePage() {
  const { id } = useParams();
  const { can } = useAuth();
  const navigate = useNavigate();
  const [reveal, setReveal] = useState(false);
  const q = useQuery({ queryKey: ["customer", id, reveal], queryFn: () => api<Profile>(`/customers/${id}`, { query: { reveal: reveal || undefined } }) });
  const c = q.data;
  if (q.isError) return <div className="panel"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>;
  if (!c) return <div className="space-y-4"><Skeleton className="h-8 w-72" /><Skeleton className="h-60" /></div>;
  const [label, tone] = CUSTOMER_STATUS[c.status] ?? [c.status, "neutral"];
  return (
    <>
      <Link to="/customers" className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-fg"><ArrowLeft className="size-3.5" />Customers</Link>
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3"><h1 className="text-[22px] font-semibold">{c.full_name}</h1><Badge tone={tone}>{label}</Badge></div>
          <p className="mt-1 text-[13px] text-muted"><span className="capitalize">{c.customer_type}</span> customer {c.external_ref}, first seen {fmtDate(c.created_at)}</p>
        </div>
        {c.customer_type === "individual" && can("customers:read_pii") && (
          <Button onClick={() => setReveal(!reveal)} aria-pressed={reveal}>{reveal ? <><EyeOff />Mask personal data</> : <><Eye />Reveal personal data</>}</Button>
        )}
      </header>
      <div className="grid gap-5 xl:grid-cols-[380px_1fr]">
        <Section title="Profile" aside={<span className="text-xs text-faint">{c.pii_masked ? "Masked" : "Revealed, and recorded in the audit log"}</span>}>
          <div className="p-4">
            <KeyValue items={[
              ["Name", c.full_name], ["Email", c.email], ["Date of birth", c.dob], ["Country", c.country],
              ["Document", c.document_type ? `${humanize(c.document_type)} ${c.document_number ?? ""}` : null],
              ["Company", c.company_name], ["Registration", c.registration_number],
              ["Latest risk", <RiskScore key="r" score={c.latest_risk_score} category={c.latest_risk_category} />], ["Updated", fmtRelative(c.updated_at)],
            ]} />
          </div>
        </Section>
        <div className="space-y-5">
          <Section title="Verification history">
            {c.verifications.length === 0 ? <EmptyState title="No verifications" /> : (
              <Table>
                <thead><tr><Th>Submitted</Th><Th>Decision</Th><Th>Risk</Th><Th>Source</Th></tr></thead>
                <tbody>{c.verifications.map((v) => (
                  <tr key={v.id} className="cursor-pointer hover:bg-raised/60" onClick={() => navigate(`/verifications/${v.id}`)}>
                    <Td><Link to={`/verifications/${v.id}`} className="hover:text-cyan">{fmtDate(v.created_at)}</Link></Td><Td><DecisionBadge decision={v.decision} status={v.status} /></Td>
                    <Td><RiskScore score={v.risk_score} category={v.risk_category} /></Td>
                    <Td className="text-muted">{v.decision_source === "manual" ? "Reviewer" : "Policy"}</Td>
                  </tr>
                ))}</tbody>
              </Table>
            )}
          </Section>
          <Section title="Cases">
            {c.cases.length === 0 ? <EmptyState title="No cases">This customer has never needed manual review.</EmptyState> : (
              <Table>
                <thead><tr><Th>Opened</Th><Th>Reason</Th><Th>Priority</Th><Th>Status</Th></tr></thead>
                <tbody>{c.cases.map((k) => (
                  <tr key={k.id} className="cursor-pointer hover:bg-raised/60" onClick={() => navigate(`/cases/${k.id}`)}>
                    <Td><Link to={`/cases/${k.id}`} className="hover:text-cyan">{fmtDate(k.created_at)}</Link></Td><Td className="max-w-[320px] truncate">{k.reason}</Td><Td><PriorityBadge priority={k.priority} /></Td><Td className="capitalize">{k.status.replace("_", " ")}</Td>
                  </tr>
                ))}</tbody>
              </Table>
            )}
          </Section>
        </div>
      </div>
    </>
  );
}
