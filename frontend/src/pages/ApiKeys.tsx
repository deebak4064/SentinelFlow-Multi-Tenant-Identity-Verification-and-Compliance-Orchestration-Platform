import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog, EmptyState, ErrorState, PageHeader, TableSkeleton, useToast } from "@/components/common";
import { Badge, Button, Dialog, DialogContent, Field, Input, Select, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { fmtDate, fmtRelative } from "@/lib/format";

interface Key { id: string; name: string; prefix: string; scopes: string[]; created_at: string; last_used_at: string | null; expires_at: string | null; status: string; secret?: string }
const SCOPES = ["verifications:create", "verifications:read", "customers:read", "cases:read", "policies:read", "audit:read"];

function SecretReveal({ secret, onDone }: { secret: string; onDone: () => void }) {
  const toast = useToast();
  return (
    <div className="space-y-4">
      <p className="text-[13px] text-amber">Copy this secret now. It is stored only as a hash and can't be shown again.</p>
      <div className="flex gap-2">
        <Input readOnly value={secret} aria-label="API key secret" onFocus={(e) => e.target.select()} />
        <Button onClick={() => navigator.clipboard.writeText(secret).then(() => toast("Secret copied"))}><Copy />Copy</Button>
      </div>
      <pre className="overflow-x-auto rounded-md bg-base p-3 text-[11.5px] text-muted">{`curl -X POST $HOST/api/v1/verifications \\\n  -H "X-API-Key: ${secret.slice(0, 14)}…" \\\n  -H "Idempotency-Key: $(uuidgen)" \\\n  -H "Content-Type: application/json" -d @request.json`}</pre>
      <div className="flex justify-end"><Button variant="primary" onClick={onDone}>I've stored the secret</Button></div>
    </div>
  );
}

export default function ApiKeysPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["api-keys"], queryFn: () => api<Key[]>("/api-keys") });
  const [open, setOpen] = useState(false);
  const [secret, setSecret] = useState<string | null>(null);
  const [f, setF] = useState({ name: "", scopes: ["verifications:create", "verifications:read"], expires_in_days: "90" });
  const create = useMutation({
    mutationFn: () => api<Key>("/api-keys", { method: "POST", body: { name: f.name, scopes: f.scopes, expires_in_days: f.expires_in_days ? Number(f.expires_in_days) : null } }),
    onSuccess: (k) => { setSecret(k.secret!); qc.invalidateQueries({ queryKey: ["api-keys"] }); },
  });
  const done = () => { setOpen(false); setSecret(null); setF({ ...f, name: "" }); };
  const rotate = (k: Key) => api<Key>(`/api-keys/${k.id}/rotate`, { method: "POST" }).then((n) => { qc.invalidateQueries({ queryKey: ["api-keys"] }); setSecret(n.secret!); setOpen(true); });
  const revoke = (k: Key) => api(`/api-keys/${k.id}/revoke`, { method: "POST" }).then(() => { qc.invalidateQueries({ queryKey: ["api-keys"] }); toast(`${k.name} revoked`); }).catch((e) => toast(errorMessage(e), "error"));

  return (
    <>
      <PageHeader title="API keys" description="Machine credentials for submitting verifications from your systems. Keys carry a subset of permissions and can't manage users, keys or settings."
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus />Create key</Button>} />
      <Dialog open={open} onOpenChange={(o) => (o ? setOpen(true) : done())}>
        <DialogContent title={secret ? "Key created" : "Create API key"}>
          {secret ? <SecretReveal secret={secret} onDone={done} /> : (
            <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
              <Field label="Name" htmlFor="kn" hint="Where it will be used, e.g. Onboarding service (prod)"><Input id="kn" required minLength={2} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
              <fieldset>
                <legend className="mb-2 text-[12.5px] font-medium">Permissions</legend>
                <div className="grid grid-cols-2 gap-2">
                  {SCOPES.map((s) => (
                    <label key={s} className="flex items-center gap-2 text-[13px]">
                      <input type="checkbox" className="accent-emerald" checked={f.scopes.includes(s)} onChange={(e) => setF({ ...f, scopes: e.target.checked ? [...f.scopes, s] : f.scopes.filter((x) => x !== s) })} />
                      <code className="text-xs">{s}</code>
                    </label>
                  ))}
                </div>
              </fieldset>
              <Field label="Expires" htmlFor="ke"><Select id="ke" value={f.expires_in_days} onChange={(e) => setF({ ...f, expires_in_days: e.target.value })}>
                <option value="30">In 30 days</option><option value="90">In 90 days</option><option value="365">In 1 year</option><option value="">Never</option>
              </Select></Field>
              {create.isError && <p role="alert" className="text-[13px] text-rose">{errorMessage(create.error)}</p>}
              <div className="flex justify-end gap-2"><Button variant="ghost" onClick={done}>Cancel</Button><Button type="submit" variant="primary" disabled={!f.scopes.length || create.isPending}>Create key</Button></div>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <div className="panel">
        {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <TableSkeleton rows={4} /> : q.data.length === 0 ? (
          <EmptyState icon={KeyRound} title="No API keys" action={<Button onClick={() => setOpen(true)}>Create key</Button>}>Create a key to call the verification API from your backend.</EmptyState>
        ) : (
          <Table>
            <thead><tr><Th>Name</Th><Th>Key</Th><Th>Permissions</Th><Th>Status</Th><Th>Last used</Th><Th className="text-right">Actions</Th></tr></thead>
            <tbody>{q.data.map((k) => (
              <tr key={k.id} className={k.status !== "active" ? "text-muted" : ""}>
                <Td><span className="font-medium">{k.name}</span><span className="block text-xs text-faint">Created {fmtDate(k.created_at)}</span></Td>
                <Td><code className="text-xs">{k.prefix}…</code></Td>
                <Td className="max-w-[260px]"><span className="line-clamp-2 text-xs text-muted">{k.scopes.join(", ")}</span></Td>
                <Td><Badge tone={k.status === "active" ? "emerald" : "neutral"}>{k.status === "active" ? (k.expires_at ? `Expires ${fmtRelative(k.expires_at)}` : "Active") : k.status === "revoked" ? "Revoked" : "Expired"}</Badge></Td>
                <Td className="text-muted">{k.last_used_at ? fmtRelative(k.last_used_at) : "Never"}</Td>
                <Td className="text-right">{k.status === "active" && (
                  <span className="inline-flex gap-1">
                    <ConfirmDialog trigger={<Button size="sm" variant="ghost">Rotate</Button>} title={`Rotate ${k.name}?`} confirmLabel="Rotate key"
                      description="Issues a new secret with the same permissions and revokes the current one immediately. Update your service before rotating." onConfirm={() => rotate(k)} />
                    <ConfirmDialog trigger={<Button size="sm" variant="ghost">Revoke</Button>} title={`Revoke ${k.name}?`} tone="danger" confirmLabel="Revoke key"
                      description="Requests using this key will be rejected immediately. This can't be undone." onConfirm={() => revoke(k)} />
                  </span>
                )}</Td>
              </tr>
            ))}</tbody>
          </Table>
        )}
      </div>
    </>
  );
}
