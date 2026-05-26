import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { RequestsProvider } from "@app/providers/RequestsProvider";
import { AppShell } from "@widgets/app-shell";
import {
  CreateRequestPage,
  EmployeesPage,
  HomePage,
  LoginPage,
  PrioritySettingsPage,
  ReportsPage,
  RequestsPage,
  WorkTypesPage,
} from "@pages/index";

export function App() {
  const { currentUser } = useAuth();

  if (!currentUser) {
    return (
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <AppShell>
      <RequestsProvider>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/requests" element={<RequestsPage />} />
          <Route path="/requests/new" element={<CreateRequestPage />} />
          <Route path="/reports" element={<RequireManager><ReportsPage /></RequireManager>} />
          <Route path="/employees" element={<RequireManager><EmployeesPage /></RequireManager>} />
          <Route path="/work-types" element={<RequireManager><WorkTypesPage /></RequireManager>} />
          <Route path="/priority-settings" element={<RequireManager><PrioritySettingsPage /></RequireManager>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </RequestsProvider>
    </AppShell>
  );
}

function RequireManager({ children }: { children: ReactNode }) {
  const { currentUser } = useAuth();

  if (currentUser?.role !== "manager") {
    return <Navigate to="/" replace />;
  }

  return children;
}
