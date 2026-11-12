import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, UserPlus } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog, ErrorState, PageHeader, Section, TableSkeleton, useToast } from "@/components/common";
import { Button, Dialog, DialogContent, Field, Input, Select, Table, Td, Th } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtRelative } from "@/lib/format";

interface Member { id: string; email: string; full_name: string; role: string; role_name: string; last_login_at: string | null; joined_at: string }
interface Roles { permissions: Record<string, string>; roles: { key: string; name: string; description: string; rank: number; permissions: string[] }[] }

function AddMember({ roles }: { roles: Roles["roles"] }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ email: "", full_name: "", role: "reviewer", temporary_password: "" });
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: () => api("/users", { method: "POST", body: f }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["members"] }); setOpen(false); toast(`${f.full_name} added`); },
  });
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="primary" onClick={() => setOpen(true)}><UserPlus />Add member</Button>
      <DialogContent title="Add member" description="They sign in with this temporary password. You can only grant roles at or below your own.">
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <Field label="Name" htmlFor="mn"><Input id="mn" required value={f.full_name} onChange={(e) => setF({ ...f, full_name: e.target.value })} /></Field>
          <Field label="Email" htmlFor="me"><Input id="me" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></Field>
          <Field label="Role" htmlFor="mr"><Select id="mr" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>{roles.filter((r) => r.key !== "super_admin").map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</Select></Field>
          <Field label="Temporary password" htmlFor="mp" hint="At least 12 characters"><Input id="mp" type="password" minLength={12} required value={f.temporary_password} onChange={(e) => setF({ ...f, temporary_password: e.target.value })} /></Field>
          {m.isError && <p role="alert" className="text-[13px] text-rose">{errorMessage(m.error)}</p>}
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={m.isPending}>Add member</Button></div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function MembersPage() {
  const { can, me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const members = useQuery({ queryKey: ["members"], queryFn: () => api<Member[]>("/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<Roles>("/roles") });
  const edit = can("users:write");
  const change = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => api(`/users/${id}/role`, { method: "PATCH", body: { role } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["members"] }); toast("Role updated"); },
    onError: (e) => toast(errorMessage(e), "error"),
  });
  return (
    <>
      <PageHeader title="Members & roles" description="Least-privilege access. Each role grants a fixed set of permissions, enforced on every API request."
        actions={edit && roles.data && <AddMember roles={roles.data.roles} />} />
      <div className="space-y-5">
        <Section title="Members" aside={<span className="text-xs text-faint">{members.data?.length ?? 0} people</span>}>
          {members.isError ? <ErrorState error={members.error} /> : !members.data ? <TableSkeleton rows={5} /> : (
            <Table>
              <thead><tr><Th>Name</Th><Th>Role</Th><Th>Last sign-in</Th><Th className="text-right">Actions</Th></tr></thead>
              <tbody>{members.data.map((m) => (
                <tr key={m.id}>
                  <Td><span className="font-medium">{m.full_name}</span>{m.id === me?.user.id && <span className="ml-2 text-xs text-faint">You</span>}<span className="block text-xs text-faint">{m.email}</span></Td>
                  <Td>
                    {edit && m.id !== me?.user.id && m.role !== "super_admin" ? (
                      <Select aria-label={`Role for ${m.full_name}`} className="w-48" value={m.role} onChange={(e) => change.mutate({ id: m.id, role: e.target.value })}>
                        {roles.data?.roles.filter((r) => r.key !== "super_admin").map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}
                      </Select>
                    ) : <span className="text-muted">{m.role_name}</span>}
                  </Td>
                  <Td className="text-muted">{m.last_login_at ? fmtRelative(m.last_login_at) : "Never"}</Td>
                  <Td className="text-right">{edit && m.id !== me?.user.id && (
                    <ConfirmDialog trigger={<Button size="sm" variant="ghost">Remove</Button>} title={`Remove ${m.full_name}?`} tone="danger" confirmLabel="Remove member"
                      description="They lose access to this organization immediately. Their past actions remain in the audit log."
                      onConfirm={() => api(`/users/${m.id}`, { method: "DELETE" }).then(() => { qc.invalidateQueries({ queryKey: ["members"] }); toast("Member removed"); }).catch((e) => toast(errorMessage(e), "error"))} />
                  )}</Td>
                </tr>
              ))}</tbody>
            </Table>
          )}
        </Section>
        <Section title="Permission matrix">
          {!roles.data ? <TableSkeleton rows={6} /> : (
            <Table>
              <thead><tr><Th className="min-w-64">Permission</Th>{roles.data.roles.map((r) => <Th key={r.key} className="text-center">{r.name}</Th>)}</tr></thead>
              <tbody>{Object.entries(roles.data.permissions).map(([k, d]) => (
                <tr key={k}>
                  <Td><span>{d}</span><code className="block text-[11px] text-faint">{k}</code></Td>
                  {roles.data!.roles.map((r) => <Td key={r.key} className="text-center">{r.permissions.includes(k) ? <Check className="mx-auto size-4 text-emerald" aria-label="Granted" /> : <span className="text-line-strong" aria-label="Not granted">·</span>}</Td>)}
                </tr>
              ))}</tbody>
            </Table>
          )}
        </Section>
      </div>
    </>
  );
}
