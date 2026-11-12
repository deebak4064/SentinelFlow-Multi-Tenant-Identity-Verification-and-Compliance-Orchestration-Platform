import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, type ReactNode } from "react";

import { api } from "./api";
import type { Me } from "./types";

interface AuthState { me: Me | null; loading: boolean; can: (perm: string) => boolean; refresh: () => void }
const Ctx = createContext<AuthState>({ me: null, loading: true, can: () => false, refresh: () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me"), retry: false, staleTime: 60_000 });
  useEffect(() => {
    const onUnauthorized = () => qc.setQueryData(["me"], null);
    window.addEventListener("sf:unauthorized", onUnauthorized);
    return () => window.removeEventListener("sf:unauthorized", onUnauthorized);
  }, [qc]);
  const me = q.data ?? null;
  const value: AuthState = {
    me,
    loading: q.isLoading,
    can: (perm) => !!me?.permissions.includes(perm),
    refresh: () => qc.invalidateQueries({ queryKey: ["me"] }),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
