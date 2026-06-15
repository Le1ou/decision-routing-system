import type { User, UserPermissions, UserRole } from "./domain";

const rolePriority: UserRole[] = ["top-manager", "manager", "executor", "author"];

export function normalizeRoles(roles: UserRole[] | undefined, fallback: UserRole = "author") {
  return roles && roles.length > 0 ? Array.from(new Set(roles)) : [fallback];
}

export function getPrimaryRole(roles: UserRole[]) {
  return rolePriority.find((role) => roles.includes(role)) ?? "author";
}

export function hasRole(user: Pick<User, "roles" | "role">, role: UserRole) {
  return user.roles.includes(role) || user.role === role;
}

export function hasAnyRole(user: Pick<User, "roles" | "role">, roles: UserRole[]) {
  return roles.some((role) => hasRole(user, role));
}

export function canAccessManagement(user: User, permissions?: UserPermissions | null) {
  return Boolean(
    permissions?.canManageEmployees ||
      permissions?.canManageWorkTypes ||
      permissions?.canManagePrioritySettings ||
      permissions?.canViewReports ||
      hasAnyRole(user, ["manager", "top-manager"]),
  );
}
