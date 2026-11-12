import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  Activity, Building2, ChevronsUpDown, FileClock, Gauge, KeyRound, LayoutDashboard, ListChecks, LogOut, Menu,
  Plug, ScrollText, Settings, ShieldCheck, Users, X, type LucideIcon,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";

import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ROLE_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Dropdown, DropdownContent, DropdownItem, DropdownLabel, DropdownSeparator, DropdownTrigger } from "./ui";

interface NavItem { to: string; label: string; icon: LucideIcon; perm: string }
const NAV: { group: string; items: NavItem[] }[] = [
  { group: "Operate", items: [
    { to: "/", label: "Overview", icon: LayoutDashboard, perm: "verifications:read" },
    { to: "/verifications", label: "Verifications", icon: ListChecks, perm: "verifications:read" },
    { to: "/cases", label: "Cases", icon: FileClock, perm: "cases:read" },
    { to: "/customers", label: "Customers", icon: Building2, perm: "customers:read" },
    { to: "/risk", label: "Risk", icon: Gauge, perm: "verifications:read" },
  ] },
  { group: "Configure", items: [
    { to: "/policies", label: "Policies", icon: ShieldCheck, perm: "policies:read" },
    { to: "/providers", label: "Providers", icon: Plug, perm: "providers:read" },
    { to: "/settings", label: "Settings", icon: Settings, perm: "policies:read" },
  ] },
  { group: "Govern", items: [
    { to: "/members", label: "Members & roles", icon: Users, perm: "users:read" },
    { to: "/api-keys", label: "API keys", icon: KeyRound, perm: "api_keys:manage" },
    { to: "/audit", label: "Audit log", icon: ScrollText, perm: "audit:read" },
  ] },
];

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="7" fill="#21262b" />
        <path d="M9 21.5c2.2 1.6 4.6 2.2 7 2.2 3.3 0 5.6-1.6 5.6-4.1 0-5.4-11.6-3.2-11.6-8.7 0-2.4 2.3-4.1 5.6-4.1 2.1 0 4 .6 5.6 1.7" fill="none" stroke="#2e9e76" strokeWidth="2.4" strokeLinecap="round" />
      </svg>
      <span className="text-[15px] font-semibold tracking-[-0.01em]">SentinelFlow</span>
    </div>
  );
}

function Nav({ onNavigate }: { onNavigate?: () => void }) {
  const { can } = useAuth();
  return (
    <nav className="mt-6 space-y-5" aria-label="Main">
      {NAV.map((g) => {
        const items = g.items.filter((i) => can(i.perm));
        if (!items.length) return null;
        return (
          <div key={g.group}>
            <p className="mb-1.5 px-2.5 text-xs text-faint">{g.group}</p>
            <ul className="space-y-px">
              {items.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end={to === "/"}
                    onClick={onNavigate}
                    className={({ isActive }) => cn(
                      "relative flex h-8 items-center gap-2.5 rounded-md px-2.5 text-[13px] text-muted hover:bg-raised hover:text-fg",
                      isActive && "bg-raised text-fg before:absolute before:inset-y-1.5 before:-left-3 before:w-0.5 before:rounded-full before:bg-emerald",
                    )}
                  >
                    <Icon className="size-4" strokeWidth={1.75} />
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function OrgSwitcher() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const sw = useMutation({
    mutationFn: (id: string) => api("/auth/switch-org", { method: "POST", body: { organization_id: id } }),
    onSuccess: () => { qc.clear(); navigate("/"); },
  });
  const logout = useMutation({ mutationFn: () => api("/auth/logout", { method: "POST" }), onSettled: () => { qc.clear(); navigate("/login"); } });
  if (!me) return null;
  return (
    <Dropdown>
      <DropdownTrigger className="flex w-full items-center gap-2.5 rounded-md border border-line bg-surface px-2.5 py-2 text-left hover:border-line-strong">
        <span className="grid size-7 shrink-0 place-items-center rounded-md bg-emerald-soft text-xs font-semibold text-[#7fd4b2]">
          {me.organization.name.split(/\s+/).slice(0, 2).map((w) => w[0]).join("").toUpperCase()}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium">{me.organization.name}</span>
          <span className="block truncate text-xs text-faint">{me.role_name}</span>
        </span>
        <ChevronsUpDown className="size-3.5 text-faint" />
      </DropdownTrigger>
      <DropdownContent>
        <DropdownLabel>Organizations</DropdownLabel>
        {me.memberships.map((m) => (
          <DropdownItem key={m.organization_id} onSelect={() => m.organization_id !== me.organization.id && sw.mutate(m.organization_id)}>
            <span className={cn("size-1.5 rounded-full", m.organization_id === me.organization.id ? "bg-emerald" : "bg-transparent")} />
            <span className="flex-1 truncate">{m.organization_name}</span>
            <span className="text-xs text-faint">{ROLE_LABEL[m.role]}</span>
          </DropdownItem>
        ))}
        <DropdownSeparator />
        <DropdownItem onSelect={() => logout.mutate()}><LogOut className="size-4 text-muted" />Sign out</DropdownItem>
      </DropdownContent>
    </Dropdown>
  );
}

function Toaster() {
  const [toasts, setToasts] = useState<{ id: number; msg: string; tone: string }[]>([]);
  useEffect(() => {
    const on = (e: Event) => {
      const { msg, tone } = (e as CustomEvent).detail;
      const id = Date.now() + Math.random();
      setToasts((t) => [...t, { id, msg, tone }]);
      setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
    };
    window.addEventListener("sf:toast", on);
    return () => window.removeEventListener("sf:toast", on);
  }, []);
  return (
    <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex flex-col gap-2" aria-live="polite">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div key={t.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className={cn("panel pointer-events-auto flex items-center gap-2.5 px-3.5 py-2.5 text-[13px] shadow-xl", t.tone === "error" && "border-rose/50")}>
            <span className={cn("size-1.5 rounded-full", t.tone === "error" ? "bg-rose" : "bg-emerald")} />
            {t.msg}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[232px_1fr]">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded focus:bg-raised focus:px-3 focus:py-2">Skip to content</a>
      <aside className="sticky top-0 hidden h-screen flex-col border-r border-line px-3 py-4 lg:flex">
        <Brand />
        <div className="flex-1 overflow-y-auto pl-3 -ml-3"><Nav /></div>
        <div className="mb-3 flex items-center gap-2 px-2 text-xs text-faint"><Activity className="size-3.5" />Simulated providers, synthetic data</div>
        <OrgSwitcher />
      </aside>
      <div className="flex items-center justify-between border-b border-line px-4 py-3 lg:hidden">
        <Brand />
        <button aria-label="Open navigation" className="rounded p-1.5 hover:bg-raised" onClick={() => setOpen(true)}><Menu className="size-5" /></button>
      </div>
      <AnimatePresence>
        {open && (
          <motion.div className="fixed inset-0 z-40 lg:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <div className="absolute inset-0 bg-black/60" onClick={() => setOpen(false)} />
            <motion.aside initial={{ x: -24 }} animate={{ x: 0 }} className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-line bg-base px-3 py-4">
              <div className="flex items-center justify-between"><Brand /><button aria-label="Close navigation" onClick={() => setOpen(false)} className="rounded p-1.5 hover:bg-raised"><X className="size-5" /></button></div>
              <div className="flex-1 overflow-y-auto pl-3 -ml-3"><Nav onNavigate={() => setOpen(false)} /></div>
              <OrgSwitcher />
            </motion.aside>
          </motion.div>
        )}
      </AnimatePresence>
      <main id="main" className="min-w-0 px-4 py-6 sm:px-8 lg:py-8">
        <div className="mx-auto max-w-[1320px]">{children}</div>
      </main>
      <Toaster />
    </div>
  );
}
