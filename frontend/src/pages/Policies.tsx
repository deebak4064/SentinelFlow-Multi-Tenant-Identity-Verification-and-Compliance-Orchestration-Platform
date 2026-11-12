import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Code2, FlaskConical, History, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState, ErrorState, PageHeader, Section, TableSkeleton, useToast } from "@/components/common";
import { Badge, Button, Field, Input, Select, Tabs, TabsContent, TabsList, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ACTION_LABEL, fmtDate } from "@/lib/format";
import type { Page, Policy, PolicyCondition, PolicyDefinition, PolicyGroup, PolicyNode, VerificationSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

const FIELDS = [
  "risk.score", "risk.category", "customer.country", "customer.type", "customer.has_email", "request.type",
  "providers.identity.outcome", "providers.identity.confidence", "providers.identity.signals.document_authentic", "providers.identity.signals.name_similarity",
  "providers.business.outcome", "providers.business.signals.registry_status", "providers.watchlist.outcome", "providers.watchlist.confidence",
  "signals.missing_mandatory_count", "signals.missing_count", "signals.inconsistency_count", "signals.conflicting_results", "signals.provider_errors",
  "history.failed_verifications",
];
const OPS = [["eq", "equals"], ["neq", "does not equal"], ["gt", ">"], ["gte", "≥"], ["lt", "<"], ["lte", "≤"], ["in", "is one of"], ["not_in", "is not one of"], ["exists", "is present"], ["missing", "is missing"]];
const isGroup = (n: PolicyNode): n is PolicyGroup => "all" in n || "any" in n;

function parseValue(raw: string, op: string): unknown {
  const one = (s: string) => { const t = s.trim(); if (t === "true") return true; if (t === "false") return false; return t !== "" && !isNaN(Number(t)) ? Number(t) : t; };
  return op === "in" || op === "not_in" ? raw.split(",").map(one).filter((v) => v !== "") : one(raw);
}
const showValue = (v: unknown) => (Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v));

function ConditionRow({ node, onChange, onRemove, disabled }: { node: PolicyCondition; onChange: (n: PolicyCondition) => void; onRemove?: () => void; disabled: boolean }) {
  const [text, setText] = useState(showValue(node.value));
  useEffect(() => setText(showValue(node.value)), [node.value]);
  const needsValue = !["exists", "missing"].includes(node.op);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input list="policy-fields" aria-label="Field" className="w-[260px]" value={node.field} disabled={disabled} onChange={(e) => onChange({ ...node, field: e.target.value })} />
      <Select aria-label="Operator" className="w-36" value={node.op} disabled={disabled} onChange={(e) => onChange({ ...node, op: e.target.value, value: parseValue(text, e.target.value) })}>
        {OPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </Select>
      {needsValue && <Input aria-label="Value" className="w-44 flex-1" value={text} disabled={disabled} placeholder={node.op.includes("in") ? "comma, separated" : "value"}
        onChange={(e) => setText(e.target.value)} onBlur={() => onChange({ ...node, value: parseValue(text, node.op) })} />}
      {onRemove && !disabled && <Button size="icon" variant="ghost" aria-label="Remove condition" onClick={onRemove}><Trash2 /></Button>}
    </div>
  );
}

function NodeEditor({ node, onChange, onRemove, depth = 0, disabled }: { node: PolicyNode; onChange: (n: PolicyNode) => void; onRemove?: () => void; depth?: number; disabled: boolean }) {
  if (!isGroup(node)) return <ConditionRow node={node} onChange={onChange} onRemove={onRemove} disabled={disabled} />;
  const mode = node.all ? "all" : "any";
  const kids = (node.all ?? node.any)!;
  const setKids = (k: PolicyNode[]) => onChange({ [mode]: k } as PolicyGroup);
  return (
    <div className={cn("space-y-2 rounded-md border border-line p-3", depth % 2 === 1 && "bg-base/60")}>
      <div className="flex items-center gap-2 text-[13px]">
        <span className="text-muted">Match</span>
        <Select aria-label="Group mode" className="w-24" value={mode} disabled={disabled} onChange={(e) => onChange({ [e.target.value]: kids } as PolicyGroup)}>
          <option value="all">all</option><option value="any">any</option>
        </Select>
        <span className="text-muted">of these conditions</span>
        {onRemove && !disabled && <Button size="sm" variant="ghost" className="ml-auto" onClick={onRemove}>Remove group</Button>}
      </div>
      {kids.map((k, i) => (
        <NodeEditor key={i} node={k} depth={depth + 1} disabled={disabled}
          onChange={(n) => setKids(kids.map((x, j) => (j === i ? n : x)))}
          onRemove={kids.length > 1 ? () => setKids(kids.filter((_, j) => j !== i)) : undefined} />
      ))}
      {!disabled && (
        <div className="flex gap-1 pt-1">
          <Button size="sm" variant="ghost" onClick={() => setKids([...kids, { field: "risk.score", op: "gte", value: 50 }])}><Plus />Condition</Button>
          {depth < 3 && <Button size="sm" variant="ghost" onClick={() => setKids([...kids, { any: [{ field: "risk.category", op: "eq", value: "high" }] }])}><Plus />Group</Button>}
        </div>
      )}
    </div>
  );
}

interface Draft { id?: string; name: string; description: string; priority: number; definition: PolicyDefinition; change_note: string }
const toDraft = (p: Policy): Draft => ({ id: p.id, name: p.name, description: p.description, priority: p.priority, definition: structuredClone(p.definition), change_note: "" });
const BLANK: Draft = { name: "", description: "", priority: 100, change_note: "", definition: { when: { all: [{ field: "risk.score", op: "gte", value: 60 }] }, action: "manual_review", reason: "" } };
const wrap = (d: PolicyDefinition): PolicyDefinition => (isGroup(d.when) ? d : { ...d, when: { all: [d.when] } });

function Simulator({ definition }: { definition: PolicyDefinition }) {
  const recent = useQuery({ queryKey: ["verifications", "sim"], queryFn: () => api<Page<VerificationSummary>>("/verifications", { query: { page_size: 15, status: "completed" } }) });
  const [vid, setVid] = useState("");
  const sim = useMutation({ mutationFn: () => api<{ action: string; trace: { matched: boolean; conditions: { field: string; actual: unknown; matched: boolean }[] }[] }>("/policies/simulate", { method: "POST", body: { verification_id: vid, definition } }) });
  return (
    <div className="space-y-3 p-4">
      <p className="text-[13px] text-muted">Run this draft against a stored verification's evaluation context. Nothing is saved.</p>
      <div className="flex gap-2">
        <Select aria-label="Verification" value={vid} onChange={(e) => setVid(e.target.value)}>
          <option value="">Choose a recent verification</option>
          {recent.data?.items.map((v) => <option key={v.id} value={v.id}>{v.external_ref} ({v.decision?.replace("_", " ")}, risk {Math.round(v.risk_score ?? 0)})</option>)}
        </Select>
        <Button disabled={!vid || sim.isPending} onClick={() => sim.mutate()}><FlaskConical />Run</Button>
      </div>
      {sim.isError && <p className="text-[13px] text-rose">{errorMessage(sim.error)}</p>}
      {sim.data && (
        <div className="rounded-md border border-line p-3 text-[13px]">
          <p>{sim.data.trace[0]?.matched ? <>Matches. This policy would <strong>{ACTION_LABEL[sim.data.action]?.toLowerCase()}</strong>.</> : <>Does not match. Outcome falls through to <strong>{ACTION_LABEL[sim.data.action]?.toLowerCase()}</strong>.</>}</p>
          <ul className="mt-2 space-y-1 text-xs">
            {sim.data.trace[0]?.conditions.map((c, i) => <li key={i} className={c.matched ? "text-amber" : "text-muted"}><code>{c.field}</code> is <code>{JSON.stringify(c.actual)}</code></li>)}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function PoliciesPage() {
  const { can } = useAuth();
  const editable = can("policies:write");
  const qc = useQueryClient();
  const toast = useToast();
  const list = useQuery({ queryKey: ["policies"], queryFn: () => api<Policy[]>("/policies") });
  const [selected, setSelected] = useState<string | "new" | null>(null);
  const current = selected && selected !== "new" ? selected : list.data?.[0]?.id;
  const detail = useQuery({ queryKey: ["policy", current], queryFn: () => api<Policy>(`/policies/${current}`), enabled: !!current && selected !== "new" });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [json, setJson] = useState(false);
  useEffect(() => { if (selected === "new") setDraft(structuredClone(BLANK)); else if (detail.data) setDraft(toDraft(detail.data)); }, [detail.data, selected]);

  const refresh = () => { qc.invalidateQueries({ queryKey: ["policies"] }); qc.invalidateQueries({ queryKey: ["policy"] }); };
  const save = useMutation({
    mutationFn: (d: Draft) => d.id
      ? api<Policy>(`/policies/${d.id}`, { method: "PATCH", body: { description: d.description, priority: d.priority, definition: d.definition, change_note: d.change_note } })
      : api<Policy>("/policies", { method: "POST", body: { name: d.name, description: d.description, priority: d.priority, definition: d.definition } }),
    onSuccess: (p) => { refresh(); setSelected(p.id); toast(`Saved ${p.name} as version ${p.current_version}`); },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  const toggle = useMutation({
    mutationFn: (p: Policy) => api(`/policies/${p.id}/${p.is_active ? "deactivate" : "activate"}`, { method: "POST" }),
    onSuccess: refresh,
  });
  const restore = useMutation({
    mutationFn: ({ id, v }: { id: string; v: number }) => api<Policy>(`/policies/${id}/versions/${v}/restore`, { method: "POST" }),
    onSuccess: (p) => { refresh(); toast(`Restored as version ${p.current_version}`); },
  });

  const dirty = draft && detail.data && selected !== "new" && JSON.stringify(toDraft(detail.data)) !== JSON.stringify({ ...draft, change_note: "" });
  const valid = draft && draft.name.length >= 3 && draft.definition.reason.length >= 3;

  return (
    <>
      <PageHeader title="Policies" description="Deterministic rules evaluated in priority order. The first matching rule with a terminal action decides; flags only annotate. Every save creates a new version."
        actions={editable && <Button variant="primary" onClick={() => setSelected("new")}><Plus />New policy</Button>} />
      <datalist id="policy-fields">{FIELDS.map((f) => <option key={f} value={f} />)}</datalist>
      <div className="grid gap-5 xl:grid-cols-[360px_1fr]">
        <Section title="Evaluation order" aside={<span className="text-xs text-faint">{list.data?.filter((p) => p.is_active).length ?? 0} active</span>}>
          {list.isError ? <ErrorState error={list.error} /> : !list.data ? <TableSkeleton rows={6} cols={2} /> : list.data.length === 0 ? <EmptyState title="No policies">Without policies, decisions follow the risk band defaults.</EmptyState> : (
            <ul className="divide-y divide-line/70">
              {list.data.map((p) => (
                <li key={p.id}>
                  <button type="button" onClick={() => setSelected(p.id)} className={cn("flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-raised/50", current === p.id && selected !== "new" && "bg-raised")}>
                    <span className="mt-0.5 w-7 text-xs tabular-nums text-faint">{p.priority}</span>
                    <span className="min-w-0 flex-1">
                      <span className={cn("block truncate text-[13px] font-medium", !p.is_active && "text-muted line-through decoration-faint")}>{p.name}</span>
                      <span className="mt-0.5 block text-xs text-faint">{ACTION_LABEL[p.definition.action]}, v{p.current_version}</span>
                    </span>
                    {!p.is_active && <Badge>Inactive</Badge>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Section>

        {!draft ? <div className="panel"><TableSkeleton rows={6} cols={3} /></div> : (
          <Section title={selected === "new" ? "New policy" : draft.name}
            aside={detail.data && selected !== "new" && editable && (
              <Button size="sm" variant={detail.data.is_active ? "secondary" : "primary"} onClick={() => toggle.mutate(detail.data!)}>{detail.data.is_active ? "Deactivate" : "Activate"}</Button>
            )}>
            <Tabs defaultValue="rule" className="p-4">
              <TabsList tabs={[{ value: "rule", label: "Rule" }, { value: "test", label: "Test" }, ...(selected !== "new" ? [{ value: "history", label: `History (${detail.data?.versions?.length ?? 0})` }] : [])]} />
              <TabsContent value="rule" className="space-y-4 pt-4">
                <div className="grid gap-4 sm:grid-cols-[1fr_120px]">
                  <Field label="Name" htmlFor="pname"><Input id="pname" value={draft.name} disabled={!!draft.id || !editable} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></Field>
                  <Field label="Priority" htmlFor="pprio" hint="Lower runs first"><Input id="pprio" type="number" min={0} value={draft.priority} disabled={!editable} onChange={(e) => setDraft({ ...draft, priority: Number(e.target.value) })} /></Field>
                </div>
                <Field label="Description" htmlFor="pdesc"><Input id="pdesc" value={draft.description} disabled={!editable} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></Field>
                <div>
                  <div className="mb-1.5 flex items-center justify-between">
                    <p className="text-[12.5px] font-medium">When</p>
                    <Button size="sm" variant="ghost" onClick={() => setJson(!json)} aria-pressed={json}><Code2 />{json ? "Visual editor" : "JSON"}</Button>
                  </div>
                  {json ? (
                    <Textarea aria-label="Policy JSON" className="min-h-56 text-xs" disabled={!editable} defaultValue={JSON.stringify(draft.definition, null, 2)}
                      onBlur={(e) => { try { setDraft({ ...draft, definition: JSON.parse(e.target.value) }); } catch { toast("That JSON is not valid", "error"); } }} />
                  ) : (
                    <NodeEditor node={wrap(draft.definition).when} disabled={!editable} onChange={(n) => setDraft({ ...draft, definition: { ...draft.definition, when: n } })} />
                  )}
                </div>
                <div className="grid gap-4 sm:grid-cols-[180px_1fr]">
                  <Field label="Then" htmlFor="pact">
                    <Select id="pact" value={draft.definition.action} disabled={!editable} onChange={(e) => setDraft({ ...draft, definition: { ...draft.definition, action: e.target.value } })}>
                      {Object.entries(ACTION_LABEL).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </Select>
                  </Field>
                  <Field label="Reason shown to reviewers" htmlFor="preason"><Input id="preason" value={draft.definition.reason} disabled={!editable} onChange={(e) => setDraft({ ...draft, definition: { ...draft.definition, reason: e.target.value } })} /></Field>
                </div>
                {editable && (
                  <div className="flex flex-wrap items-end gap-3 border-t border-line pt-4">
                    {draft.id && <div className="min-w-60 flex-1"><Field label="Change note" htmlFor="pnote"><Input id="pnote" placeholder="Why this change" value={draft.change_note} onChange={(e) => setDraft({ ...draft, change_note: e.target.value })} /></Field></div>}
                    <div className="ml-auto flex gap-2">
                      {dirty && <Button variant="ghost" onClick={() => setDraft(toDraft(detail.data!))}>Discard</Button>}
                      <Button variant="primary" disabled={!valid || save.isPending || (!!draft.id && !dirty)} onClick={() => save.mutate(draft)}>
                        {draft.id ? `Save as version ${(detail.data?.current_version ?? 0) + 1}` : "Create policy"}
                      </Button>
                    </div>
                  </div>
                )}
              </TabsContent>
              <TabsContent value="test"><Simulator definition={draft.definition} /></TabsContent>
              <TabsContent value="history" className="pt-2">
                <ul className="divide-y divide-line/70">
                  {detail.data?.versions?.map((v) => (
                    <li key={v.version} className="flex items-center gap-3 py-3 text-[13px]">
                      <History className="size-4 text-faint" />
                      <span className="w-10 tabular-nums">v{v.version}</span>
                      <span className="flex-1 text-muted">{v.change_note || "No note"}<span className="ml-2 text-xs text-faint">{ACTION_LABEL[v.definition.action]}</span></span>
                      <span className="text-xs text-faint">{fmtDate(v.created_at)}</span>
                      {editable && v.version !== detail.data?.current_version && <Button size="sm" onClick={() => restore.mutate({ id: detail.data!.id, v: v.version })}>Restore</Button>}
                      {v.version === detail.data?.current_version && <Badge tone="emerald">Current</Badge>}
                    </li>
                  ))}
                </ul>
              </TabsContent>
            </Tabs>
          </Section>
        )}
      </div>
    </>
  );
}
