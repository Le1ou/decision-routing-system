import type { Request, RequestAction, RequestPriority, User } from "./domain";

const priorityOrder: Record<RequestPriority, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
};

export type RequestSortKey = "priority" | "status" | "createdAt" | "finishedAt";

export type RequestFilter = {
  executorQuery?: string;
  requestNumberQuery?: string;
  createdByMe?: boolean;
  delegatedFromAnotherDepartment?: boolean;
  assignedToMe?: boolean;
};

export function canViewRequest(request: Request, user: User) {
  if (user.role === "author") {
    return request.authorId === user.id;
  }

  if (user.role === "executor") {
    return (
      request.authorId === user.id ||
      request.executorId === user.id ||
      request.previousExecutorId === user.id ||
      request.closedById === user.id
    );
  }

  return request.authorId === user.id || request.departmentId === user.departmentId;
}

export function filterRequestsByRole(requests: Request[], user: User) {
  return requests.filter((request) => canViewRequest(request, user));
}

export function getAvailableRequestActions(request: Request, user: User): RequestAction[] {
  const actions: RequestAction[] = [];

  if (request.authorId === user.id && ["new", "assigned", "inProgress"].includes(request.status)) {
    actions.push("editDescription");
  }

  if (user.role === "manager") {
    if (request.status === "new") {
      actions.push("assignExecutor");
    }

    if (request.status === "assigned") {
      actions.push("delegateExternal", "reject", "returnToNew");
    }

    if (request.status === "delegated") {
      actions.push("confirmExternalDelegation", "declineExternalDelegation");
    }

    if (request.status === "inProgress") {
      actions.push("reject", "returnToNew");
    }
  }

  if (user.role === "executor" && request.executorId === user.id) {
    if (request.status === "assigned") {
      actions.push("startWork", "reject", "delegateInternal", "delegateExternal");
    }

    if (request.status === "inProgress") {
      actions.push("complete", "reject", "delegateInternal");
    }
  }

  return actions;
}

export function sortRequests(requests: Request[], sortKey: RequestSortKey) {
  return [...requests].sort((left, right) => {
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

type RequestFilterLookups = {
  getExecutorName?: (executorId?: string) => string;
};

export function applyRequestFilters(
  requests: Request[],
  filter: RequestFilter,
  currentUser: User,
  lookups: RequestFilterLookups = {},
) {
  return requests.filter((request) => {
    const executorName = lookups.getExecutorName?.(request.executorId) ?? "";

    if (filter.executorQuery && !executorName.toLowerCase().includes(filter.executorQuery.toLowerCase())) {
      return false;
    }

    if (filter.requestNumberQuery && !request.number.toLowerCase().includes(filter.requestNumberQuery.toLowerCase())) {
      return false;
    }

    if (filter.createdByMe && request.authorId !== currentUser.id) {
      return false;
    }

    if (filter.assignedToMe && request.executorId !== currentUser.id) {
      return false;
    }

    if (filter.delegatedFromAnotherDepartment && !request.delegationId) {
      return false;
    }

    return true;
  });
}
