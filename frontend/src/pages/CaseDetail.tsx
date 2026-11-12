import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Sparkles } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ConfirmDialog, DecisionBadge, ErrorState, KeyValue, OutcomeBadge, PriorityBadge, RiskScore, Section, useToast } from "@/components/common";
import { Badge, Button, Select, Skeleton, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRelative, humanize, PROVIDER_LABEL } from "@/lib/format";
import type { Case, VerificationDetail } from "@/lib/types";

import { CASE_STATUS } from "./Cases";
import { RiskBreakdown } from "./VerificationDetail";

type CaseDetail = Case & {
  verification: VerificationDetail | null; can_decide: boolean; assignee_name: string | null;
  comments: { id: string; body: string; author_name: string | null; created_at: string; evidence_refs: { type: string; id: string }[] }[];
  history: { action: string; actor_label: string | null; details: Record<string, unknown>; created_at: string }[];
};
interface Analysis {
  analysis: { summary: string; risk_signals: { signal: string; explanation: string; evidence_refs: string[] }[]; inconsistencies: { description: string; evidence_refs: string[] }[];
    reviewer_questions: string[]; draft_report: string; confidence: number };
  source: string; model: string | null; warnings: string[]; advisory_notice: string; evidence: { id: string; summary: string }[];
}

function AiPanel({ caseId }: { caseId: string }) {
  const m = useMutation({ mutationFn: () => api<Analysis>(`/cases/${caseId}/ai-analysis`, { method: "POST" }) });
  const [report, setReport] = useState(false);
  const a = m.data;
  return (
    <Section title="Analyst assistant" aside={a && <span className="text-xs text-faint">{a.source === "llm" ? `Model ${a.model}` : "Deterministic summary"}, confidence {a.analysis.confidence.toFixed(2)}</span>}>
      <div className="p-4">
        {!a ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="max-w-[56ch] text-[13px] text-muted">Summarizes the evidence, explains risk signals and suggests questions. It cites evidence for every claim and cannot approve or reject.</p>
            <Button onClick={() => m.mutate()} disabled={m.isPending}><Sparkles />{m.isPending ? "Analyzing…" : "Summarize case"}</Button>
          </div>
        ) : (
          <div className="space-y-4 text-[13px]">
            <p className="leading-relaxed">{a.analysis.summary}</p>
            {a.analysis.risk_signals.length > 0 && <div>
              <p className="mb-1.5 text-xs text-muted">Risk signals</p>
              <ul className="space-y-1.5">{a.analysis.risk_signals.map((s, i) => (
                <li key={i}>{s.explanation} {s.evidence_refs.map((r) => <span key={r} className="ml-1 rounded bg-raised px-1 text-[11px] text-cyan" title={a.evidence.find((e) => e.id === r)?.summary}>{r}</span>)}</li>
              ))}</ul>
            </div>}
            {a.analysis.inconsistencies.length > 0 && <div>
              <p className="mb-1.5 text-xs text-muted">Inconsistencies</p>
              <ul className="list-disc space-y-1 pl-4">{a.analysis.inconsistencies.map((s, i) => <li key={i}>{s.description}</li>)}</ul>
            </div>}
            {a.analysis.reviewer_questions.length > 0 && <div>
              <p className="mb-1.5 text-xs text-muted">Questions to resolve</p>
              <ul className="list-disc space-y-1 pl-4">{a.analysis.reviewer_questions.map((q) => <li key={q}>{q}</li>)}</ul>
            </div>}
            {a.warnings.map((w) => <p key={w} className="text-xs text-amber">{w}</p>)}
            <div className="flex items-center justify-between border-t border-line pt-3">
              <p className="text-xs text-faint">{a.advisory_notice}</p>
              <Button size="sm" variant="ghost" onClick={() => setReport(!report)}>{report ? "Hide" : "Show"} draft report</Button>
            </div>
            {report && <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-base p-3 text-xs leading-relaxed text-muted">{a.analysis.draft_report}</pre>}
          </div>
        )}
        {m.isError && <p className="mt-2 text-[13px] text-rose">{errorMessage(m.error)}</p>}
      </div>
    </Section>
  );
}

export default function CaseDetailPage() {
  const { id } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["case", id], queryFn: () => api<CaseDetail>(`/cases/${id}`) });
  const members = useQuery({ queryKey: ["members"], queryFn: () => api<{ id: string; full_name: string; role: string }[]>("/users"), enabled: can("cases:assign") });
  const [comment, setComment] = useState("");
  const [notes, setNotes] = useState("");
  const [reason, setReason] = useState("");
  const invalidate = () => { qc.invalidateQueries({ queryKey: ["case", id] }); qc.invalidateQueries({ queryKey: ["cases"] }); qc.invalidateQueries({ queryKey: ["overview"] }); };
  const act = (path: string, body: unknown, done: string) => api(`/cases/${id}/${path}`, { method: "POST", body }).then(() => { invalidate(); toast(done); }).catch((e) => { toast(errorMessage(e), "error"); throw e; });
  const post = useMutation({ mutationFn: () => act("comments", { body: comment }, "Comment added"), onSuccess: () => setComment("") });

  const c = q.data;
  if (q.isError) return <div className="panel"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>;
  if (!c) return <div className="space-y-4"><Skeleton className="h-8 w-72" /><Skeleton className="h-60" /></div>;
  const v = c.verification;
  const open = ["open", "in_review", "escalated"].includes(c.status);
  const deciders = members.data?.filter((m) => ["reviewer", "compliance_analyst", "org_admin", "super_admin"].includes(m.role)) ?? [];

  return (
    <>
      <Link to="/cases" className="mb-4 inline-flex items-center gap-1.5 text-[13px] text-muted hover:text-fg"><ArrowLeft className="size-3.5" />Cases</Link>
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[22px] font-semibold">{v?.customer.full_name ?? "Case"}</h1>
            <Badge tone={CASE_STATUS[c.status]?.[1]}>{CASE_STATUS[c.status]?.[0]}</Badge><PriorityBadge priority={c.priority} />
          </div>
          <p className="mt-1 text-[13px] text-muted">{c.reason}. Opened {fmtDate(c.created_at)}{c.sla_due_at && open && (new Date(c.sla_due_at) < new Date() ? `, overdue since ${fmtDate(c.sla_due_at)}` : `, due ${fmtRelative(c.sla_due_at)}`)}.</p>
        </div>
        {open && (
          <div className="flex flex-wrap gap-2">
            {can("cases:write") && c.status !== "escalated" && (
              <ConfirmDialog trigger={<Button>Escalate</Button>} title="Escalate case" description="Raises the priority and returns the case to the analyst queue." confirmLabel="Escalate case"
                onConfirm={() => act("escalate", { reason }, "Case escalated")}>
                <Textarea aria-label="Escalation reason" placeholder="What needs a senior review?" value={reason} onChange={(e) => setReason(e.target.value)} />
              </ConfirmDialog>
            )}
            {c.can_decide && <>
              <ConfirmDialog trigger={<Button variant="danger">Reject</Button>} title="Reject customer" tone="danger" confirmLabel="Reject customer"
                description="Records a final manual rejection. This is written to the audit log and cannot be changed afterwards."
                onConfirm={() => act("decision", { decision: "reject", notes }, "Customer rejected")}>
                <Textarea aria-label="Decision notes" placeholder="Reason for the decision (required)" value={notes} onChange={(e) => setNotes(e.target.value)} />
              </ConfirmDialog>
              <ConfirmDialog trigger={<Button variant="primary">Approve</Button>} title="Approve customer" confirmLabel="Approve customer"
                description="Records a final manual approval. This is written to the audit log and cannot be changed afterwards."
                onConfirm={() => act("decision", { decision: "approve", notes }, "Customer approved")}>
                <Textarea aria-label="Decision notes" placeholder="Reason for the decision (required)" value={notes} onChange={(e) => setNotes(e.target.value)} />
              </ConfirmDialog>
            </>}
          </div>
        )}
      </header>

      <div className="grid gap-5 xl:grid-cols-[1fr_360px]">
        <div className="space-y-5">
          {can("ai:use") && v && <AiPanel caseId={c.id} />}
          {v && (
            <Section title="Evidence" aside={<Link to={`/verifications/${v.id}`} className="text-xs text-cyan hover:underline">Full decision trace</Link>}>
              <ul className="divide-y divide-line/70">
                {v.provider_results.map((r) => (
                  <li key={r.id} className="flex items-start gap-4 px-4 py-3 text-[13px]">
                    <span className="w-36 shrink-0 font-medium">{PROVIDER_LABEL[r.provider_key]}</span>
                    <span className="flex-1 text-muted">{r.normalized.reasons.join(". ")}{r.attempts > 1 && <span className="ml-1 text-amber">({r.attempts} attempts)</span>}</span>
                    <OutcomeBadge outcome={r.outcome} />
                  </li>
                ))}
              </ul>
              {v.risk_assessment && <div className="border-t border-line"><RiskBreakdown risk={v.risk_assessment} /></div>}
            </Section>
          )}
          <Section title="Discussion">
            <ul className="divide-y divide-line/70">
              {c.comments.length === 0 && <li className="px-4 py-6 text-center text-[13px] text-muted">No comments yet. Record what you checked so the next reviewer doesn't repeat it.</li>}
              {c.comments.map((m) => (
                <li key={m.id} className="px-4 py-3 text-[13px]">
                  <p className="mb-1 text-xs text-faint"><span className="text-muted">{m.author_name ?? "System"}</span> {fmtRelative(m.created_at)}</p>
                  <p className="whitespace-pre-wrap">{m.body}</p>
                  {m.evidence_refs.length > 0 && <p className="mt-1 text-xs text-cyan">{m.evidence_refs.length} evidence reference{m.evidence_refs.length > 1 && "s"}</p>}
                </li>
              ))}
            </ul>
            {can("cases:write") && (
              <form className="border-t border-line p-4" onSubmit={(e) => { e.preventDefault(); if (comment.trim()) post.mutate(); }}>
                <Textarea aria-label="Comment" placeholder="Add a note for the case record" value={comment} onChange={(e) => setComment(e.target.value)} />
                <div className="mt-2 flex justify-end"><Button type="submit" disabled={!comment.trim() || post.isPending}>Add comment</Button></div>
              </form>
            )}
          </Section>
        </div>

        <div className="space-y-5">
          <Section title="Case">
            <div className="space-y-4 p-4">
              <KeyValue items={[
                ["Decision", v ? <DecisionBadge key="d" decision={v.decision} /> : null], ["Risk", <RiskScore key="r" score={v?.risk_assessment?.score} category={v?.risk_assessment?.category} />],
                ["Assignee", c.assignee_name ?? "Unassigned"], ["Decided", c.decided_at ? fmtDate(c.decided_at) : null], ["Notes", c.decision_notes],
              ]} />
              {open && can("cases:assign") && (
                <div className="border-t border-line pt-4">
                  <label htmlFor="assignee" className="mb-1.5 block text-[12.5px] font-medium">Assign to</label>
                  <Select id="assignee" value={c.assignee_id ?? ""} onChange={(e) => act("assign", { assignee_id: e.target.value || null }, "Assignment updated")}>
                    <option value="">Unassigned</option>
                    {deciders.map((m) => <option key={m.id} value={m.id}>{m.full_name} ({humanize(m.role)})</option>)}
                  </Select>
                </div>
              )}
              {!c.can_decide && open && <p className="text-xs text-faint">{c.status === "escalated" ? "Escalated cases are decided by a compliance analyst or admin." : "Only the assignee or an analyst can decide this case."}</p>}
            </div>
          </Section>
          <Section title="History">
            <ol className="space-y-0 p-4">
              {c.history.map((h, i) => (
                <li key={i} className="relative border-l border-line pb-4 pl-4 last:pb-0">
                  <span className="absolute -left-[4px] top-1.5 size-[7px] rounded-full bg-line-strong" />
                  <p className="text-[13px]">{humanize(h.action.replace("case.", ""))}</p>
                  <p className="text-xs text-faint">{h.actor_label ?? "system"}, {fmtRelative(h.created_at)}</p>
                </li>
              ))}
            </ol>
          </Section>
        </div>
      </div>
    </>
  );
}
