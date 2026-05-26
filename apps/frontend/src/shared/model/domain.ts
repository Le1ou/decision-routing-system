export type UserRole = "author" | "executor" | "manager";

export type RequestStatus =
  | "new"
  | "assigned"
  | "delegated"
  | "inProgress"
  | "rejected"
  | "completed";

export type RequestPriority = "low" | "medium" | "high" | "critical";

export type RequestAction =
  | "editDescription"
  | "assignExecutor"
  | "startWork"
  | "reject"
  | "complete"
  | "delegateInternal"
  | "delegateExternal"
  | "returnToNew"
  | "confirmExternalDelegation"
  | "declineExternalDelegation";

export type Complexity = "easy" | "medium" | "hard" | "critical";

export type Department = {
  id: string;
  name: string;
  value: number;
  delegatedToSameDepartment: boolean;
  employeeApplicationDelayMinutes: number;
  deadlineNotificationRatio: number;
};

export type Position = {
  id: string;
  name: string;
  isTop: boolean;
};

export type User = {
  id: string;
  login: string;
  fullName: string;
  role: UserRole;
  departmentId: string;
  positionId: string;
  isActive: boolean;
};

export type WorkType = {
  id: string;
  name: string;
  departmentId: string;
  complexity: Complexity;
};

export type Attachment = {
  id: string;
  requestId: string;
  name: string;
  type: "photo" | "document";
};

export type Delegation = {
  id: string;
  requestId: string;
  delegatedByDepartmentId: string;
  delegatedFromDepartmentId: string;
  delegatedToDepartmentId: string;
  comment: string;
  createdAt: string;
  decision?: "confirmed" | "declined";
  decidedAt?: string;
};

export type Request = {
  id: string;
  number: string;
  title: string;
  description: string;
  status: RequestStatus;
  priority: RequestPriority;
  departmentId: string;
  workTypeId: string;
  authorId: string;
  executorId?: string;
  previousExecutorId?: string;
  executorComment?: string;
  managerComment?: string;
  resultText?: string;
  delegationId?: string;
  attachmentNames?: string[];
  assignedComplexity?: Complexity;
  assignedAt?: string;
  isUnfinished: boolean;
  createdAt: string;
  deadlineAt: string;
  updatedAt: string;
  startedAt?: string;
  finishedAt?: string;
  closedById?: string;
};

export type Notification = {
  id: string;
  text: string;
  requestId?: string;
  createdAt: string;
  isRead: boolean;
};
