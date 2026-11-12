import { zodResolver } from "@hookform/resolvers/zod";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useSearchParams } from "react-router-dom";
import { z } from "zod";

import { DecisionBadge, EmptyState, ErrorState, PageHeader, Pagination, RiskScore, TableSkeleton } from "@/components/common";
import { Button, Dialog, DialogContent, Field, Input, Select, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtMs, fmtRelative } from "@/lib/format";
import type { Page, VerificationDetail, VerificationSummary } from "@/lib/types";

export function useFilters(keys: string[]) {
  const [sp, setSp] = useSearchParams();
  const values = Object.fromEntries(keys.map((k) => [k, sp.get(k) ?? ""]));
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(sp);
    if (v) next.set(k, v); else next.delete(k);
    if (k !== "page") next.delete("page");
    setSp(next, { replace: true });
  };
  return [values, set] as const;
}

export default function VerificationsPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [f, set] = useFilters(["decision", "status", "verification_type", "risk_category", "q", "page", "sort"]);
  const page = Number(f.page || 1);
  const q = useQuery({
    queryKey: ["verifications", f],
    queryFn: () => api<Page<VerificationSummary>>("/verifications", { query: { ...f, page, page_size: 25 } }),
    placeholderData: keepPreviousData,
  });
  const filtered = ["decision", "status", "verification_type", "risk_category", "q"].some((k) => f[k]);

  return (
    <>
      <PageHeader title="Verifications" description="Every request submitted by your team or through the API, with its automated decision."
        actions={can("verifications:create") && <NewVerificationDialog />} />
      <div className="panel">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-faint" />
            <Input aria-label="Search by customer reference" placeholder="Search customer reference" className="pl-8" defaultValue={f.q}
              onKeyDown={(e) => e.key === "Enter" && set("q", (e.target as HTMLInputElement).value.trim())} onBlur={(e) => set("q", e.target.value.trim())} />
          </div>
          <Select aria-label="Decision" className="w-40" value={f.decision} onChange={(e) => set("decision", e.target.value)}>
            <option value="">All decisions</option><option value="approved">Approved</option><option value="manual_review">Manual review</option>
            <option value="escalated">Escalated</option><option value="rejected">Rejected</option>
          </Select>
          <Select aria-label="Risk" className="w-36" value={f.risk_category} onChange={(e) => set("risk_category", e.target.value)}>
            <option value="">All risk levels</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option>
          </Select>
          <Select aria-label="Type" className="w-36" value={f.verification_type} onChange={(e) => set("verification_type", e.target.value)}>
            <option value="">All types</option><option value="individual">Individual</option><option value="business">Business</option>
          </Select>
          <Select aria-label="Status" className="w-36" value={f.status} onChange={(e) => set("status", e.target.value)}>
            <option value="">All statuses</option><option value="completed">Completed</option><option value="queued">Queued</option><option value="processing">Processing</option><option value="failed">Failed</option>
          </Select>
          <Select aria-label="Sort" className="ml-auto w-40" value={f.sort} onChange={(e) => set("sort", e.target.value)}>
            <option value="">Newest first</option><option value="created_at">Oldest first</option><option value="-risk_score">Highest risk</option><option value="-latency_ms">Slowest</option>
          </Select>
        </div>
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <TableSkeleton /> : q.data.items.length === 0 ? (
          filtered ? <EmptyState title="No verifications match these filters">Clear a filter or search for a different reference.</EmptyState>
            : <EmptyState title="No verifications yet" action={can("verifications:create") && <NewVerificationDialog />}>Submit one here, or call POST /api/v1/verifications with an API key.</EmptyState>
        ) : (
          <>
            <Table>
              <thead><tr><Th>Customer</Th><Th>Type</Th><Th>Decision</Th><Th>Risk</Th><Th className="text-right">Processing</Th><Th className="text-right">Submitted</Th></tr></thead>
              <tbody>
                {q.data.items.map((v) => (
                  <tr key={v.id} className="cursor-pointer hover:bg-raised/60" onClick={() => navigate(`/verifications/${v.id}`)}>
                    <Td>
                      <a href={`/verifications/${v.id}`} onClick={(e) => { e.preventDefault(); navigate(`/verifications/${v.id}`); }} className="font-medium hover:text-cyan">{v.customer_name}</a>
                      <span className="flex gap-2 text-xs text-faint"><span>{v.external_ref}</span><span>{v.country}</span></span>
                    </Td>
                    <Td className="capitalize text-muted">{v.verification_type}</Td>
                    <Td><DecisionBadge decision={v.decision} status={v.status} />{v.decision_source === "manual" && <span className="ml-2 text-xs text-faint">by reviewer</span>}</Td>
                    <Td><RiskScore score={v.risk_score} category={v.risk_category} /></Td>
                    <Td className="text-right tabular-nums text-muted">{fmtMs(v.latency_ms)}</Td>
                    <Td className="text-right text-muted" title={v.created_at}>{fmtRelative(v.created_at)}</Td>
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

const schema = z.object({
  verification_type: z.enum(["individual", "business"]),
  external_ref: z.string().min(1, "Required").regex(/^[A-Za-z0-9_.:-]+$/, "Letters, numbers and _ . : - only"),
  full_name: z.string().optional(),
  dob: z.string().optional(),
  country: z.string().regex(/^[A-Z]{2}$/, "Two-letter ISO code, e.g. GB"),
  document_type: z.string().optional(),
  document_number: z.string().optional(),
  company_name: z.string().optional(),
  registration_number: z.string().optional(),
  email: z.union([z.email("Enter a valid email"), z.literal("")]).optional(),
}).superRefine((v, ctx) => {
  if (v.verification_type === "individual" && !v.full_name) ctx.addIssue({ code: "custom", path: ["full_name"], message: "Required for individuals" });
  if (v.verification_type === "business" && !v.company_name) ctx.addIssue({ code: "custom", path: ["company_name"], message: "Required for businesses" });
});
type FormValues = z.infer<typeof schema>;

export function NewVerificationDialog() {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { verification_type: "individual", external_ref: `cust-${Date.now().toString(36)}`, country: "GB", document_type: "passport", email: "" },
  });
  const type = form.watch("verification_type");
  const err = form.formState.errors;
  const m = useMutation({
    mutationFn: (v: FormValues) => {
      const { verification_type, external_ref, ...s } = v;
      const subject = Object.fromEntries(Object.entries(s).filter(([k, val]) => val && (type === "individual" ? !["company_name", "registration_number"].includes(k) : ["company_name", "registration_number", "country", "email"].includes(k))));
      return api<VerificationDetail>("/verifications", { method: "POST", body: { verification_type, external_ref, subject }, headers: { "Idempotency-Key": crypto.randomUUID() } });
    },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["verifications"] }); qc.invalidateQueries({ queryKey: ["overview"] }); setOpen(false); navigate(`/verifications/${r.id}`); },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="primary" onClick={() => setOpen(true)}><Plus />New verification</Button>
      <DialogContent title="New verification" description="Runs the full workflow now. Use synthetic test data only." className="w-[min(92vw,600px)]">
        <form onSubmit={form.handleSubmit((v) => m.mutate(v))} className="space-y-4" noValidate>
          <div className="grid grid-cols-2 gap-1 rounded-md border border-line p-1" role="radiogroup" aria-label="Verification type">
            {(["individual", "business"] as const).map((t) => (
              <label key={t} className={`cursor-pointer rounded px-3 py-1.5 text-center text-[13px] capitalize ${type === t ? "bg-raised text-fg" : "text-muted"}`}>
                <input type="radio" value={t} className="sr-only" {...form.register("verification_type")} />{t}
              </label>
            ))}
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Customer reference" htmlFor="ref" error={err.external_ref?.message}><Input id="ref" {...form.register("external_ref")} /></Field>
            <Field label="Country" htmlFor="country" error={err.country?.message}><Input id="country" maxLength={2} {...form.register("country", { setValueAs: (v) => v.toUpperCase() })} /></Field>
            {type === "individual" ? <>
              <Field label="Full name" htmlFor="fn" error={err.full_name?.message}><Input id="fn" {...form.register("full_name")} /></Field>
              <Field label="Date of birth" htmlFor="dob"><Input id="dob" type="date" {...form.register("dob")} /></Field>
              <Field label="Document type" htmlFor="dt"><Select id="dt" {...form.register("document_type")}><option value="passport">Passport</option><option value="national_id">National ID</option><option value="drivers_license">Driver's license</option></Select></Field>
              <Field label="Document number" htmlFor="dn" hint="Ending 0000 fails, 5555 is ambiguous"><Input id="dn" {...form.register("document_number")} /></Field>
            </> : <>
              <Field label="Company name" htmlFor="cn" error={err.company_name?.message}><Input id="cn" {...form.register("company_name")} /></Field>
              <Field label="Registration number" htmlFor="rn" hint="Prefix DISS dissolved, NF not found"><Input id="rn" {...form.register("registration_number")} /></Field>
            </>}
            <Field label="Contact email" htmlFor="em" error={err.email?.message}><Input id="em" type="email" {...form.register("email")} /></Field>
          </div>
          {m.isError && <p role="alert" className="text-[13px] text-rose">{errorMessage(m.error)}</p>}
          <div className="flex justify-end gap-2 border-t border-line pt-4">
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={m.isPending}>{m.isPending ? "Running checks…" : "Run verification"}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
