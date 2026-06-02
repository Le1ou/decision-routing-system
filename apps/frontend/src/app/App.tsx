import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { ApplicationsProvider } from "@app/providers/ApplicationsProvider";
import type { UserPermissions } from "@shared/model/domain";
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
          <Route path="/reports" element={<RequirePermission permission="canViewReports"><ReportsPage /></RequirePermission>} />
          <Route path="/employees" element={<RequirePermission permission="canManageEmployees"><EmployeesPage /></RequirePermission>} />
          <Route path="/work-types" element={<RequirePermission permission="canManageWorkTypes"><WorkTypesPage /></RequirePermission>} />
          <Route path="/priority-settings" element={<RequirePermission permission="canManagePrioritySettings"><PrioritySettingsPage /></RequirePermission>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ApplicationsProvider>
    </AppShell>
  );
}

function RequirePermission({ children, permission }: { children: ReactNode; permission: keyof UserPermissions }) {
  const { permissions } = useAuth();

  if (!permissions?.[permission]) {
    return <Navigate to="/" replace />;
  }

  return children;
}
