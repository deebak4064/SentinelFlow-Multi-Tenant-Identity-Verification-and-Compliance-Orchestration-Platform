import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { z } from "zod";

import { Button, Field, Input } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Me } from "@/lib/types";

const DEMO = import.meta.env.VITE_SHOW_DEMO_ACCOUNTS !== "false";
const DEMO_ACCOUNTS = [
  ["org.admin@northwind.example", "Organization Admin"],
  ["compliance.analyst@northwind.example", "Compliance Analyst"],
  ["reviewer@northwind.example", "Reviewer"],
  ["auditor@northwind.example", "Read-only Auditor"],
  ["platform@sentinelflow.example", "Super Admin"],
  ["admin@contoso.example", "Admin of a second tenant"],
] as const;

function AuthLayout({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(420px,1fr)_minmax(380px,520px)]">
      <div className="flex flex-col justify-between px-6 py-8 sm:px-14">
        <div className="flex items-center gap-2.5">
          <svg viewBox="0 0 32 32" className="size-7" aria-hidden><rect width="32" height="32" rx="7" fill="#21262b" /><path d="M9 21.5c2.2 1.6 4.6 2.2 7 2.2 3.3 0 5.6-1.6 5.6-4.1 0-5.4-11.6-3.2-11.6-8.7 0-2.4 2.3-4.1 5.6-4.1 2.1 0 4 .6 5.6 1.7" fill="none" stroke="#2e9e76" strokeWidth="2.4" strokeLinecap="round" /></svg>
          <span className="text-[15px] font-semibold">SentinelFlow</span>
        </div>
        <div className="mx-auto w-full max-w-[380px] py-12">{children}</div>
        <p className="text-xs text-faint">Verification results come from simulated providers using synthetic data. They are not legal compliance certification.</p>
      </div>
      <aside className="hidden border-l border-line bg-surface lg:flex lg:flex-col lg:justify-center lg:px-12">{aside}</aside>
    </div>
  );
}

function Pipeline() {
  const stages = [
    ["Request", "Validated against strict schemas; unknown fields refused"],
    ["Providers", "Identity, registry and watchlist checks run concurrently with timeouts, retries and circuit breakers"],
    ["Risk", "Weighted 0–100 score with every contributing factor itemized"],
    ["Policy", "Versioned rules evaluated in priority order; each match recorded"],
    ["Decision", "Approve, reject or route to a reviewer, written to an append-only audit log"],
  ];
  return (
    <div>
      <h2 className="max-w-[22ch] text-2xl font-semibold leading-snug">Every decision arrives with the evidence that produced it.</h2>
      <ol className="mt-10 border-l border-line-strong">
        {stages.map(([t, d], i) => (
          <li key={t} className="relative pb-6 pl-6 last:pb-0">
            <span className={`absolute -left-[5px] top-1.5 size-[9px] rounded-full border-2 border-surface ${i === stages.length - 1 ? "bg-emerald" : "bg-line-strong"}`} />
            <p className="text-[13.5px] font-medium">{t}</p>
            <p className="mt-0.5 max-w-[46ch] text-[13px] text-muted">{d}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

const loginSchema = z.object({ email: z.email("Enter a valid email"), password: z.string().min(1, "Enter your password") });

export function LoginPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const loc = useLocation() as { state?: { from?: string } };
  const [error, setError] = useState<string | null>(null);
  const form = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema), defaultValues: { email: "", password: "" } });
  if (me) return <Navigate to="/" replace />;

  const submit = form.handleSubmit(async (v) => {
    setError(null);
    try {
      const res = await api<Me>("/auth/login", { method: "POST", body: v });
      qc.setQueryData(["me"], res);
      navigate(loc.state?.from ?? "/", { replace: true });
    } catch (e) {
      setError(errorMessage(e));
    }
  });

  return (
    <AuthLayout aside={<Pipeline />}>
      <h1 className="text-[22px] font-semibold">Sign in</h1>
      <p className="mt-1 text-[13px] text-muted">Use your work email to open your organization's workspace.</p>
      <form onSubmit={submit} className="mt-7 space-y-4" noValidate>
        <Field label="Email" htmlFor="email" error={form.formState.errors.email?.message}>
          <Input id="email" type="email" autoComplete="username" aria-invalid={!!form.formState.errors.email} {...form.register("email")} />
        </Field>
        <Field label="Password" htmlFor="password" error={form.formState.errors.password?.message}>
          <Input id="password" type="password" autoComplete="current-password" aria-invalid={!!form.formState.errors.password} {...form.register("password")} />
        </Field>
        {error && <p role="alert" className="rounded-md border border-rose/40 bg-rose-soft px-3 py-2 text-[13px] text-[#f0b2ab]">{error}</p>}
        <Button type="submit" variant="primary" size="lg" className="w-full" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>
      <p className="mt-6 text-[13px] text-muted">New to SentinelFlow? <Link to="/register" className="text-cyan hover:underline">Create an organization</Link></p>
      {DEMO && (
        <div className="mt-10 border-t border-line pt-5">
          <p className="text-[13px] font-medium">Demo accounts</p>
          <p className="mt-0.5 text-xs text-faint">Local seed data. Password: SentinelDemo2026!</p>
          <ul className="mt-3 space-y-1">
            {DEMO_ACCOUNTS.map(([email, role]) => (
              <li key={email}>
                <button type="button" className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-[13px] hover:bg-raised"
                  onClick={() => { form.setValue("email", email); form.setValue("password", "SentinelDemo2026!"); }}>
                  <span className="text-muted">{email}</span><span className="text-xs text-faint">{role}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </AuthLayout>
  );
}

const registerSchema = z.object({
  organization_name: z.string().min(2, "Enter your organization's name"),
  full_name: z.string().min(2, "Enter your name"),
  email: z.email("Enter a valid email"),
  password: z.string().min(12, "Use at least 12 characters").regex(/[A-Za-z]/, "Include a letter").regex(/\d/, "Include a number"),
});

export function RegisterPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  const form = useForm<z.infer<typeof registerSchema>>({ resolver: zodResolver(registerSchema) });
  const err = form.formState.errors;
  const submit = form.handleSubmit(async (v) => {
    setError(null);
    try {
      const res = await api<Me>("/auth/register", { method: "POST", body: v });
      qc.setQueryData(["me"], res);
      navigate("/", { replace: true });
    } catch (e) {
      setError(errorMessage(e));
    }
  });
  return (
    <AuthLayout aside={
      <div>
        <h2 className="text-xl font-semibold">What you get on day one</h2>
        <ul className="mt-6 space-y-4 text-[13px] text-muted">
          <li><span className="font-medium text-fg">Eight starter policies</span>, active and versioned, covering missing fields, watchlist matches, critical risk and inconsistent data.</li>
          <li><span className="font-medium text-fg">Three simulated providers</span> for identity, business registry and watchlist screening, each configurable for latency and failure.</li>
          <li><span className="font-medium text-fg">You as Organization Admin</span>, able to invite analysts, reviewers and auditors with least-privilege roles.</li>
        </ul>
      </div>
    }>
      <h1 className="text-[22px] font-semibold">Create your organization</h1>
      <p className="mt-1 text-[13px] text-muted">Your data is isolated from every other organization on the platform.</p>
      <form onSubmit={submit} className="mt-7 space-y-4" noValidate>
        <Field label="Organization name" htmlFor="org" error={err.organization_name?.message}><Input id="org" aria-invalid={!!err.organization_name} {...form.register("organization_name")} /></Field>
        <Field label="Your name" htmlFor="name" error={err.full_name?.message}><Input id="name" autoComplete="name" aria-invalid={!!err.full_name} {...form.register("full_name")} /></Field>
        <Field label="Work email" htmlFor="remail" error={err.email?.message}><Input id="remail" type="email" autoComplete="email" aria-invalid={!!err.email} {...form.register("email")} /></Field>
        <Field label="Password" htmlFor="rpass" hint="At least 12 characters with letters and numbers" error={err.password?.message}>
          <Input id="rpass" type="password" autoComplete="new-password" aria-invalid={!!err.password} {...form.register("password")} />
        </Field>
        {error && <p role="alert" className="rounded-md border border-rose/40 bg-rose-soft px-3 py-2 text-[13px] text-[#f0b2ab]">{error}</p>}
        <Button type="submit" variant="primary" size="lg" className="w-full" disabled={form.formState.isSubmitting}>
          {form.formState.isSubmitting ? "Creating organization…" : "Create organization"}
        </Button>
      </form>
      <p className="mt-6 text-[13px] text-muted">Already have an account? <Link to="/login" className="text-cyan hover:underline">Sign in</Link></p>
    </AuthLayout>
  );
}
