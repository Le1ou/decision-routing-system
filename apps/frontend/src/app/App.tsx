import { Navigate, Route, Routes } from "react-router-dom";
import type { ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { ApplicationsProvider } from "@app/providers/ApplicationsProvider";
import { ReferenceDataProvider } from "@app/providers/ReferenceDataProvider";
import type { UserPermissions, UserRole } from "@shared/model/domain";
import { hasAnyRole } from "@shared/model/roles";
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
    <ReferenceDataProvider>
      <ApplicationsProvider>
        <AppShell>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/applications" element={<ApplicationsPage />} />
          <Route path="/applications/new" element={<CreateApplicationPage />} />
          <Route path="/reports" element={<RequirePermission permission="canViewReports"><ReportsPage /></RequirePermission>} />
          <Route path="/employees" element={<RequirePermission permission="canManageEmployees"><EmployeesPage /></RequirePermission>} />
          <Route path="/work-types" element={<RequirePermission permission="canManageWorkTypes"><WorkTypesPage /></RequirePermission>} />
          <Route path="/priority-settings" element={<RequireRole roles={["manager", "top-manager"]}><PrioritySettingsPage /></RequireRole>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        </AppShell>
      </ApplicationsProvider>
    </ReferenceDataProvider>
  );
}

function RequirePermission({ children, permission }: { children: ReactNode; permission: keyof UserPermissions }) {
  const { permissions } = useAuth();

  if (!permissions?.[permission]) {
    return <Navigate to="/" replace />;
  }

  return children;
}

function RequireRole({ children, roles }: { children: ReactNode; roles: UserRole[] }) {
  const { currentUser } = useAuth();

  if (!currentUser || !hasAnyRole(currentUser, roles)) {
    return <Navigate to="/" replace />;
  }

  return children;
}
