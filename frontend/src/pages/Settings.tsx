import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ErrorState, KeyValue, PageHeader, RISK_COLOR, Section, TableSkeleton, useToast } from "@/components/common";
import { Button, Field, Input, Select } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDate, fmtRelative, humanize } from "@/lib/format";

interface Settings {
  organization: { id: string; name: string; slug: string; status: string; created_at: string };
  risk_weights: Record<string, number>; risk_thresholds: Record<"medium" | "high" | "critical", number>;
  ai_enabled: boolean; ai_external_llm_allowed: boolean; default_execution_mode: string;
}
interface Session { id: string; ip: string | null; user_agent: string | null; last_seen_at: string; current: boolean }

function Toggle({ label, hint, checked, onChange, disabled }: { label: string; hint: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className="flex items-start justify-between gap-6 py-3">
      <span><span className="block text-[13px] font-medium">{label}</span><span className="mt-0.5 block max-w-[60ch] text-xs text-muted">{hint}</span></span>
      <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-5 w-9 shrink-0 rounded-full transition-colors disabled:opacity-50 ${checked ? "bg-emerald" : "bg-line-strong"}`}>
        <span className={`absolute top-0.5 size-4 rounded-full bg-fg transition-transform ${checked ? "translate-x-4" : "translate-x-0.5"}`} />
      </button>
    </label>
  );
}

export default function SettingsPage() {
  const { can } = useAuth();
  const editable = can("settings:write");
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/settings") });
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: () => api<Session[]>("/auth/sessions") });
  const [draft, setDraft] = useState<Settings | null>(null);
  useEffect(() => { if (q.data) setDraft(structuredClone(q.data)); }, [q.data]);
  const save = useMutation({
    mutationFn: (body: Partial<Settings>) => api<Settings>("/settings", { method: "PATCH", body }),
    onSuccess: (s) => { qc.setQueryData(["settings"], s); toast("Settings saved"); },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  const revoke = useMutation({ mutationFn: (id: string) => api(`/auth/sessions/${id}`, { method: "DELETE" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }) });

  if (q.isError) return <div className="panel"><ErrorState error={q.error} /></div>;
  if (!draft) return <div className="panel"><TableSkeleton /></div>;
  const t = draft.risk_thresholds;
  const thresholdsValid = 0 < t.medium && t.medium < t.high && t.high < t.critical && t.critical <= 100;
  return (
    <>
      <PageHeader title="Settings" description="Organization-wide configuration. Changes apply to verifications submitted afterwards and are recorded in the audit log." />
      <div className="grid gap-5 xl:grid-cols-2">
        <Section title="Risk scoring">
          <div className="space-y-5 p-4">
            <div>
              <p className="mb-2 text-[12.5px] font-medium">Factor weights</p>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                {Object.entries(draft.risk_weights).map(([k, v]) => (
                  <Field key={k} label={humanize(k)} htmlFor={`w-${k}`}>
                    <Input id={`w-${k}`} type="number" min={0} max={100} value={v} disabled={!editable} onChange={(e) => setDraft({ ...draft, risk_weights: { ...draft.risk_weights, [k]: Number(e.target.value) } })} />
                  </Field>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-2 text-[12.5px] font-medium">Category thresholds</p>
              <div className="relative mb-3 h-2 overflow-hidden rounded-full" aria-hidden>
                {[["low", 0, t.medium], ["medium", t.medium, t.high], ["high", t.high, t.critical], ["critical", t.critical, 100]].map(([c, a, b]) => (
                  <span key={c as string} className="absolute inset-y-0" style={{ left: `${a}%`, width: `${Math.max(Number(b) - Number(a), 0)}%`, background: RISK_COLOR[c as "low"] }} />
                ))}
              </div>
              <div className="grid grid-cols-3 gap-3">
                {(["medium", "high", "critical"] as const).map((k) => (
                  <Field key={k} label={`${humanize(k)} from`} htmlFor={`t-${k}`}>
                    <Input id={`t-${k}`} type="number" min={1} max={100} value={t[k]} disabled={!editable} aria-invalid={!thresholdsValid}
                      onChange={(e) => setDraft({ ...draft, risk_thresholds: { ...t, [k]: Number(e.target.value) } })} />
                  </Field>
                ))}
              </div>
              {!thresholdsValid && <p className="mt-2 text-xs text-rose">Thresholds must increase: medium below high, high below critical.</p>}
            </div>
            {editable && <div className="flex justify-end border-t border-line pt-4">
              <Button variant="primary" disabled={!thresholdsValid || save.isPending} onClick={() => save.mutate({ risk_weights: draft.risk_weights, risk_thresholds: draft.risk_thresholds })}>Save risk settings</Button>
            </div>}
          </div>
        </Section>
        <div className="space-y-5">
          <Section title="Processing and AI">
            <div className="divide-y divide-line px-4">
              <div className="flex items-center justify-between gap-6 py-3">
                <span><span className="block text-[13px] font-medium">Default execution</span><span className="mt-0.5 block text-xs text-muted">Inline returns the decision in the API response; queued hands off to background workers.</span></span>
                <Select aria-label="Default execution" className="w-32" disabled={!editable} value={draft.default_execution_mode} onChange={(e) => save.mutate({ default_execution_mode: e.target.value })}>
                  <option value="inline">Inline</option><option value="queue">Queued</option>
                </Select>
              </div>
              <Toggle label="Analyst assistant" hint="Lets reviewers generate case summaries. The assistant never makes decisions." checked={draft.ai_enabled} disabled={!editable} onChange={(v) => save.mutate({ ai_enabled: v })} />
              <Toggle label="Allow an external language model" hint="Sends redacted, structured evidence to the configured LLM provider. No names, dates of birth, document numbers or emails leave the system. Off uses a deterministic summary."
                checked={draft.ai_external_llm_allowed} disabled={!editable} onChange={(v) => save.mutate({ ai_external_llm_allowed: v })} />
            </div>
          </Section>
          <Section title="Organization">
            <div className="p-4"><KeyValue items={[["Name", draft.organization.name], ["Slug", draft.organization.slug], ["Status", humanize(draft.organization.status)], ["Created", fmtDate(draft.organization.created_at)], ["ID", <code key="i" className="text-xs">{draft.organization.id}</code>]]} /></div>
          </Section>
          <Section title="Your sessions">
            <ul className="divide-y divide-line/70">
              {sessions.data?.map((s) => (
                <li key={s.id} className="flex items-center gap-3 px-4 py-2.5 text-[13px]">
                  <span className="min-w-0 flex-1"><span className="block truncate">{s.user_agent || "Unknown client"}</span><span className="text-xs text-faint">{s.ip}, active {fmtRelative(s.last_seen_at)}</span></span>
                  {s.current ? <span className="text-xs text-emerald">This session</span> : <Button size="sm" variant="ghost" onClick={() => revoke.mutate(s.id)}>Sign out</Button>}
                </li>
              ))}
            </ul>
          </Section>
        </div>
      </div>
    </>
  );
}
