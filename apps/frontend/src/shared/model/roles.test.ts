import { describe, expect, it } from "vitest";

import type { User } from "./domain";
import { canAccessManagement, getPrimaryRole, hasAnyRole, hasRole, normalizeRoles } from "./roles";

const baseUser: User = {
  id: "u1",
  login: "user",
  fullName: "Test User",
  roles: ["author"],
  role: "author",
  departmentId: "it",
  postName: "Engineer",
  positionId: "engineer",
  jobTitleId: "engineer",
  isActive: true,
};

describe("roles", () => {
  it("normalizes empty roles with the fallback role", () => {
    expect(normalizeRoles(undefined, "executor")).toEqual(["executor"]);
    expect(normalizeRoles([], "manager")).toEqual(["manager"]);
  });

  it("deduplicates provided roles", () => {
    expect(normalizeRoles(["author", "author", "executor"])).toEqual(["author", "executor"]);
  });

  it("selects the highest primary role", () => {
    expect(getPrimaryRole(["author", "executor"])).toBe("executor");
    expect(getPrimaryRole(["manager", "top-manager", "author"])).toBe("top-manager");
  });

  it("checks both role and roles collection", () => {
    expect(hasRole({ ...baseUser, roles: ["author"], role: "executor" }, "executor")).toBe(true);
    expect(hasAnyRole({ ...baseUser, roles: ["manager"], role: "author" }, ["executor", "manager"])).toBe(true);
  });

  it("allows management access by permissions or management role", () => {
    expect(canAccessManagement(baseUser, {
      canManageEmployees: false,
      canManageWorkTypes: true,
      canManagePrioritySettings: false,
      canViewReports: false,
    })).toBe(true);
    expect(canAccessManagement({ ...baseUser, roles: ["manager"], role: "manager" }, null)).toBe(true);
    expect(canAccessManagement(baseUser, null)).toBe(false);
  });
});
