import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap } from "lucide-react";
import { useState } from "react";

import { ErrorState, KeyValue, OutcomeBadge, PageHeader, Section, TableSkeleton, useToast } from "@/components/common";
import { Badge, Button, Field, Input } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtMs, fmtNum, fmtPct, humanize } from "@/lib/format";

interface Provider {
  key: string; display_name: string; description: string; check_type: string; required_fields: string[]; simulated: boolean;
  config: { enabled: boolean; timeout_ms: number; max_retries: number; simulation: { latency_ms?: number; failure_rate?: number; timeout_rate?: number } } | null;
  circuit_state: string;
  stats_24h: { calls: number; success_rate?: number | null; avg_latency_ms?: number; p95_latency_ms?: number; retried?: number; cache_hits?: number };
}
interface TestResult { ok: boolean; attempts: number; latency_ms: number; error_code: string | null; normalized: { outcome: string; reasons: string[] } | null; circuit_state: string }

function ProviderPanel({ p }: { p: Provider }) {
  const { can } = useAuth();
  const editable = can("providers:write");
  const qc = useQueryClient();
  const toast = useToast();
  const c = p.config!;
  const [form, setForm] = useState({ timeout_ms: c.timeout_ms, max_retries: c.max_retries, latency_ms: c.simulation.latency_ms ?? 40,
    failure_rate: Math.round((c.simulation.failure_rate ?? 0) * 100), timeout_rate: Math.round((c.simulation.timeout_rate ?? 0) * 100) });
  const save = useMutation({
    mutationFn: (body: object) => api(`/providers/${p.key}`, { method: "PATCH", body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); toast(`${p.display_name} updated`); },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  const test = useMutation({ mutationFn: () => api<TestResult>(`/providers/${p.key}/test`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }) });
  const s = p.stats_24h;
  const num = (k: keyof typeof form) => ({ value: form[k], onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: Number(e.target.value) }), disabled: !editable, type: "number" });

  return (
    <Section title={p.display_name} aside={
      <div className="flex items-center gap-2">
        <Badge tone={p.circuit_state === "closed" ? "emerald" : p.circuit_state === "open" ? "rose" : "amber"} dot>Circuit {p.circuit_state.replace("_", "-")}</Badge>
        <Badge tone={c.enabled ? "emerald" : "neutral"}>{c.enabled ? "Enabled" : "Disabled"}</Badge>
      </div>}>
      <div className="grid gap-0 divide-y divide-line lg:grid-cols-[1fr_1.2fr] lg:divide-x lg:divide-y-0">
        <div className="space-y-4 p-4">
          <p className="text-[13px] text-muted">{p.description}</p>
          <KeyValue items={[
            ["Adapter key", p.key], ["Check type", humanize(p.check_type)], ["Required input", p.required_fields.join(", ")],
            ["Calls (24h)", fmtNum(s.calls)], ["Success rate", fmtPct(s.success_rate)], ["Latency", s.calls ? `avg ${fmtMs(s.avg_latency_ms)}, p95 ${fmtMs(s.p95_latency_ms)}` : "—"],
            ["Retried calls", fmtNum(s.retried ?? 0)], ["Cache hits", fmtNum(s.cache_hits ?? 0)],
          ]} />
        </div>
        <div className="space-y-4 p-4">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Timeout per attempt (ms)" htmlFor={`${p.key}-to`}><Input id={`${p.key}-to`} min={50} max={10000} {...num("timeout_ms")} /></Field>
            <Field label="Retries" htmlFor={`${p.key}-re`}><Input id={`${p.key}-re`} min={0} max={5} {...num("max_retries")} /></Field>
          </div>
          <div>
            <p className="mb-2 text-[12.5px] font-medium">Simulation</p>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Latency (ms)" htmlFor={`${p.key}-la`}><Input id={`${p.key}-la`} min={0} max={5000} {...num("latency_ms")} /></Field>
              <Field label="Failure rate (%)" htmlFor={`${p.key}-fr`}><Input id={`${p.key}-fr`} min={0} max={100} {...num("failure_rate")} /></Field>
              <Field label="Timeout rate (%)" htmlFor={`${p.key}-tr`}><Input id={`${p.key}-tr`} min={0} max={100} {...num("timeout_rate")} /></Field>
            </div>
          </div>
          {editable && (
            <div className="flex flex-wrap gap-2 border-t border-line pt-4">
              <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate({ timeout_ms: form.timeout_ms, max_retries: form.max_retries,
                simulation: { latency_ms: form.latency_ms, failure_rate: form.failure_rate / 100, timeout_rate: form.timeout_rate / 100 } })}>Save configuration</Button>
              <Button onClick={() => save.mutate({ enabled: !c.enabled })}>{c.enabled ? "Disable provider" : "Enable provider"}</Button>
              <Button variant="ghost" className="ml-auto" disabled={test.isPending} onClick={() => test.mutate()}><PlugZap />{test.isPending ? "Testing…" : "Test connection"}</Button>
            </div>
          )}
          {test.data && (
            <div className="rounded-md border border-line p-3 text-[13px]" role="status">
              <div className="flex items-center justify-between">
                <span>{test.data.ok ? "Responded" : `Failed: ${test.data.error_code}`} in {fmtMs(test.data.latency_ms)} after {test.data.attempts} attempt{test.data.attempts !== 1 && "s"}</span>
                {test.data.normalized && <OutcomeBadge outcome={test.data.normalized.outcome} />}
              </div>
              {test.data.normalized && <p className="mt-1 text-xs text-muted">{test.data.normalized.reasons.join(". ")}</p>}
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}

export default function ProvidersPage() {
  const q = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/providers") });
  return (
    <>
      <PageHeader title="Providers" description="Verification providers behind the orchestration engine. These are simulations with configurable latency and failure, so you can exercise retries, timeouts and the circuit breaker." />
      {q.isError ? <div className="panel"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !q.data ? <div className="panel"><TableSkeleton rows={6} /></div> : (
        <div className="space-y-5">{q.data.filter((p) => p.config).map((p) => <ProviderPanel key={p.key + JSON.stringify(p.config)} p={p} />)}</div>
      )}
    </>
  );
}
