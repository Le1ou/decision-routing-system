import type { Application, ApplicationAction, ApplicationPriority, User } from "./domain";

const priorityOrder: Record<ApplicationPriority, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
};

export type ApplicationSortKey = "priority" | "status" | "createdAt" | "finishedAt";

export type ApplicationFilter = {
  executorQuery?: string;
  applicationIdQuery?: string;
  createdByMe?: boolean;
  delegatedFromAnotherDepartment?: boolean;
  assignedToMe?: boolean;
};

export function canViewApplication(application: Application, user: User) {
  if (application.archivedAt || isAutoHiddenRejectedApplication(application)) {
    return false;
  }

  if (user.role === "author") {
    return application.authorId === user.id;
  }

  if (user.role === "executor") {
    return (
      application.authorId === user.id ||
      application.executorId === user.id ||
      application.previousExecutorId === user.id ||
      application.closedById === user.id
    );
  }

  if (user.role === "manager") {
    return application.authorId === user.id || application.departmentId === user.departmentId;
  }

  return true;
}

export function filterApplicationsByRole(applications: Application[], user: User) {
  return applications.filter((application) => canViewApplication(application, user));
}

export function getAvailableApplicationActions(application: Application, user: User): ApplicationAction[] {
  const actions: ApplicationAction[] = [];

  if (application.authorId === user.id && ["new", "assigned", "inProgress"].includes(application.status)) {
    actions.push("editDescription");
  }

  if (application.authorId === user.id && application.status === "new") {
    actions.push("cancel");
  }

  if (user.role === "manager" || user.role === "top-manager") {
    if (application.status === "new") {
      actions.push("assignExecutor", "changeWorkType", "cancel");
    }

    if (application.status === "assigned") {
      actions.push("delegateExternal", "reject", "returnToNew", "changeWorkType");
    }

    if (application.status === "delegated") {
      actions.push("confirmExternalDelegation", "declineExternalDelegation");
    }

    if (application.status === "inProgress") {
      actions.push("reject", "returnToNew", "changeWorkType");
    }
  }

  if (user.role === "executor" && application.executorId === user.id) {
    if (application.status === "assigned") {
      actions.push("startWork", "delegateInternal", "delegateExternal");
    }

    if (application.status === "inProgress") {
      actions.push("complete", "delegateInternal");
    }
  }

  if (["rejected", "completed"].includes(application.status)) {
    actions.push("archive");
  }

  return actions;
}

function isAutoHiddenRejectedApplication(application: Application) {
  if (application.status !== "rejected") {
    return false;
  }

  const updatedAt = new Date(application.updatedAt).getTime();
  const sevenDaysMs = 7 * 24 * 60 * 60 * 1000;

  return Date.now() - updatedAt >= sevenDaysMs;
}

export function sortApplications(applications: Application[], sortKey: ApplicationSortKey) {
  return [...applications].sort((left, right) => {
    if (sortKey === "priority") {
      return priorityOrder[right.priority] - priorityOrder[left.priority];
    }

    if (sortKey === "status") {
      return left.status.localeCompare(right.status);
    }

    const leftDate = sortKey === "finishedAt" ? left.finishedAt : left.createdAt;
    const rightDate = sortKey === "finishedAt" ? right.finishedAt : right.createdAt;

    return new Date(rightDate ?? 0).getTime() - new Date(leftDate ?? 0).getTime();
  });
}

type ApplicationFilterLookups = {
  getExecutorName?: (executorId?: string) => string;
};

export function applyApplicationFilters(
  applications: Application[],
  filter: ApplicationFilter,
  currentUser: User,
  lookups: ApplicationFilterLookups = {},
) {
  return applications.filter((application) => {
    const executorName = lookups.getExecutorName?.(application.executorId) ?? "";

    if (filter.executorQuery && !executorName.toLowerCase().includes(filter.executorQuery.toLowerCase())) {
      return false;
    }

    if (filter.applicationIdQuery && !application.id.toLowerCase().includes(filter.applicationIdQuery.toLowerCase())) {
      return false;
    }

    if (filter.createdByMe && application.authorId !== currentUser.id) {
      return false;
    }

    if (filter.assignedToMe && application.executorId !== currentUser.id) {
      return false;
    }

    if (filter.delegatedFromAnotherDepartment && !application.delegationId) {
      return false;
    }

    return true;
  });
}
