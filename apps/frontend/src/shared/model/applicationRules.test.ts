import { describe, expect, it, vi } from "vitest";

import type { Application, User } from "./domain";
import {
  applyApplicationFilters,
  canViewApplication,
  getAvailableApplicationActions,
  sortApplications,
} from "./applicationRules";

const user = (overrides: Partial<User> = {}): User => ({
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
  ...overrides,
});

const application = (overrides: Partial<Application> = {}): Application => ({
  id: "A-1",
  title: "Network issue",
  description: "",
  status: "new",
  priority: "medium",
  departmentId: "it",
  workTypeId: "network",
  authorId: "author-1",
  isUnfinished: true,
  createdAt: "2026-06-01T10:00:00.000Z",
  deadlineAt: "2026-06-02T10:00:00.000Z",
  updatedAt: "2026-06-01T10:00:00.000Z",
  ...overrides,
});

describe("applicationRules", () => {
  it("limits authors to their own visible applications", () => {
    const currentUser = user({ id: "author-1", role: "author", roles: ["author"] });

    expect(canViewApplication(application({ authorId: "author-1" }), currentUser)).toBe(true);
    expect(canViewApplication(application({ authorId: "other" }), currentUser)).toBe(false);
  });

  it("lets executors see assigned, previous, closed, and authored applications", () => {
    const currentUser = user({ id: "executor-1", role: "executor", roles: ["executor"] });

    expect(canViewApplication(application({ executorId: "executor-1" }), currentUser)).toBe(true);
    expect(canViewApplication(application({ previousExecutorId: "executor-1" }), currentUser)).toBe(true);
    expect(canViewApplication(application({ closedById: "executor-1" }), currentUser)).toBe(true);
    expect(canViewApplication(application({ authorId: "executor-1" }), currentUser)).toBe(true);
    expect(canViewApplication(application({ executorId: "executor-2" }), currentUser)).toBe(false);
  });

  it("hides rejected applications after seven days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-14T00:00:00.000Z"));

    expect(canViewApplication(application({
      status: "rejected",
      authorId: "author-1",
      updatedAt: "2026-06-06T23:59:59.000Z",
    }), user({ id: "author-1" }))).toBe(false);
    expect(canViewApplication(application({
      status: "rejected",
      authorId: "author-1",
      updatedAt: "2026-06-07T00:00:01.000Z",
    }), user({ id: "author-1" }))).toBe(true);

    vi.useRealTimers();
  });

  it("returns role-specific actions", () => {
    expect(getAvailableApplicationActions(application({ status: "new" }), user({ role: "manager", roles: ["manager"] })))
      .toEqual(["assignExecutor", "changeWorkType", "cancel"]);
    expect(getAvailableApplicationActions(application({ status: "assigned", executorId: "u1" }), user({ role: "executor", roles: ["executor"] })))
      .toEqual(["startWork", "delegateInternal", "delegateExternal"]);
    expect(getAvailableApplicationActions(application({ status: "completed" }), user({ role: "top-manager", roles: ["top-manager"] })))
      .toContain("archive");
  });

  it("sorts by priority and dates", () => {
    const items = [
      application({ id: "low", priority: "low", createdAt: "2026-06-01T00:00:00.000Z" }),
      application({ id: "critical", priority: "critical", createdAt: "2026-06-03T00:00:00.000Z" }),
      application({ id: "high", priority: "high", createdAt: "2026-06-02T00:00:00.000Z" }),
    ];

    expect(sortApplications(items, "priority").map((item) => item.id)).toEqual(["critical", "high", "low"]);
    expect(sortApplications(items, "createdAt").map((item) => item.id)).toEqual(["critical", "high", "low"]);
    expect(sortApplications(items, "createdAt", "reverse").map((item) => item.id)).toEqual(["low", "high", "critical"]);
  });

  it("applies combined filters", () => {
    const items = [
      application({ id: "A-100", authorId: "u1", executorId: "executor-1", delegationId: "d1" }),
      application({ id: "B-200", authorId: "u2", executorId: "executor-2" }),
    ];

    expect(applyApplicationFilters(items, {
      applicationIdQuery: "A-",
      executorQuery: "ivan",
      createdByMe: true,
      delegatedOnly: true,
    }, user(), {
      getExecutorName: (executorId) => executorId === "executor-1" ? "Ivan Petrov" : "Maria",
    })).toEqual([items[0]]);
  });

  it("filters closed applications", () => {
    const items = [
      application({ id: "new", status: "new" }),
      application({ id: "assigned", status: "assigned" }),
      application({ id: "completed", status: "completed" }),
      application({ id: "rejected", status: "rejected" }),
    ];

    expect(applyApplicationFilters(items, { openOnly: true }, user()).map((item) => item.id))
      .toEqual(["new", "assigned"]);
  });
});
