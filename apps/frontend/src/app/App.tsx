import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { ApplicationsProvider } from "@app/providers/ApplicationsProvider";
import { AppShell } from "@widgets/app-shell";
import {
  CreateApplicationPage,
  EmployeesPage,
  HomePage,
  LoginPage,
  PrioritySettingsPage,
  ReportsPage,
  ApplicationsPage,
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
      <ApplicationsProvider>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/applications" element={<ApplicationsPage />} />
          <Route path="/applications/new" element={<CreateApplicationPage />} />
          <Route path="/reports" element={<RequireManagement><ReportsPage /></RequireManagement>} />
          <Route path="/employees" element={<RequireManagement><EmployeesPage /></RequireManagement>} />
          <Route path="/work-types" element={<RequireManagement><WorkTypesPage /></RequireManagement>} />
          <Route path="/priority-settings" element={<RequireManagement><PrioritySettingsPage /></RequireManagement>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ApplicationsProvider>
    </AppShell>
  );
}

function RequireManagement({ children }: { children: ReactNode }) {
  const { currentUser } = useAuth();

  if (currentUser?.role !== "manager" && currentUser?.role !== "top-manager") {
    return <Navigate to="/" replace />;
  }

  return children;
}
