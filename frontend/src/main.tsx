import "./index.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import { ApiError } from "@/lib/api";
import { AuthProvider, useAuth } from "@/lib/auth";
import { LoginPage, RegisterPage } from "@/pages/Auth";

const Overview = lazy(() => import("@/pages/Overview"));
const Verifications = lazy(() => import("@/pages/Verifications"));
const VerificationDetail = lazy(() => import("@/pages/VerificationDetail"));
const Customers = lazy(() => import("@/pages/Customers"));
const CustomerProfile = lazy(() => import("@/pages/CustomerProfile"));
const Risk = lazy(() => import("@/pages/Risk"));
const Policies = lazy(() => import("@/pages/Policies"));
const Cases = lazy(() => import("@/pages/Cases"));
const CaseDetail = lazy(() => import("@/pages/CaseDetail"));
const Providers = lazy(() => import("@/pages/Providers"));
const Members = lazy(() => import("@/pages/Members"));
const ApiKeys = lazy(() => import("@/pages/ApiKeys"));
const Audit = lazy(() => import("@/pages/Audit"));
const SettingsPage = lazy(() => import("@/pages/Settings"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
    },
  },
});

function Protected({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="grid min-h-screen place-items-center text-muted" aria-busy="true">Loading workspace…</div>;
  if (!me) return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return (
    <AppShell>
      <Suspense fallback={<div className="skeleton h-8 w-64" />}>{children}</Suspense>
    </AppShell>
  );
}

const routes: [string, ReactNode][] = [
  ["/", <Overview />], ["/verifications", <Verifications />], ["/verifications/:id", <VerificationDetail />],
  ["/customers", <Customers />], ["/customers/:id", <CustomerProfile />], ["/risk", <Risk />], ["/policies", <Policies />],
  ["/cases", <Cases />], ["/cases/:id", <CaseDetail />], ["/providers", <Providers />], ["/members", <Members />],
  ["/api-keys", <ApiKeys />], ["/audit", <Audit />], ["/settings", <SettingsPage />],
];

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            {routes.map(([path, el]) => <Route key={path} path={path} element={<Protected>{el}</Protected>} />)}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
