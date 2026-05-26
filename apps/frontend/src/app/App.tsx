import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
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
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/requests/new" element={<CreateRequestPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/employees" element={<EmployeesPage />} />
        <Route path="/work-types" element={<WorkTypesPage />} />
        <Route path="/priority-settings" element={<PrioritySettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
