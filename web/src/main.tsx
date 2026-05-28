import { ChakraProvider, defaultSystem } from "@chakra-ui/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import "@/design-system.css";

import { App } from "@/App";
import { AppToaster } from "@/components/AppToaster";
import { RootErrorBoundary } from "@/components/RootErrorBoundary";
import { AdminAuditPage } from "@/pages/AdminAudit";
import { DashboardPage } from "@/pages/Dashboard";
import { IntegrationsPage } from "@/pages/Integrations";
import { LeasPage } from "@/pages/Leas";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      refetchInterval: 10_000,
      refetchOnWindowFocus: true,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ChakraProvider value={defaultSystem}>
      <RootErrorBoundary>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <Routes>
              <Route element={<App />}>
                <Route index element={<DashboardPage />} />
                <Route path="leas" element={<LeasPage />} />
                <Route path="integrations" element={<IntegrationsPage />} />
                <Route path="admin/audit" element={<AdminAuditPage />} />
              </Route>
            </Routes>
          </BrowserRouter>
          <AppToaster />
        </QueryClientProvider>
      </RootErrorBoundary>
    </ChakraProvider>
  </StrictMode>,
);
