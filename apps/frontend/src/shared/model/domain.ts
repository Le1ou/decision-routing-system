export type UserRole = "author" | "executor" | "manager";

export type ApplicationStatus =
  | "new"
  | "assigned"
  | "delegated"
  | "inProgress"
  | "rejected"
  | "completed";

export type ApplicationPriority = "low" | "medium" | "high" | "critical";

export type ApplicationAction =
  | "editDescription"
  | "assignExecutor"
  | "startWork"
  | "reject"
  | "complete"
  | "delegateInternal"
  | "delegateExternal"
  | "returnToNew"
  | "confirmExternalDelegation"
  | "declineExternalDelegation"
  | "changeWorkType";

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
  applicationId: string;
  name: string;
  type: "photo" | "document";
};

export type Delegation = {
  id: string;
  applicationId: string;
  delegatedByDepartmentId: string;
  delegatedFromDepartmentId: string;
  delegatedToDepartmentId: string;
  comment: string;
  createdAt: string;
  decision?: "confirmed" | "declined";
  decidedAt?: string;
};

export type Application = {
  id: string;
  title: string;
  description: string;
  status: ApplicationStatus;
  priority: ApplicationPriority;
  departmentId: string;
  workTypeId: string;
  authorId: string;
  executorId?: string;
  previousExecutorId?: string;
  executorComment?: string;
  managerComment?: string;
  resultText?: string;
  delegationId?: string;
  delegatedFromDepartmentId?: string;
  delegatedToDepartmentId?: string;
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
  applicationId?: string;
  createdAt: string;
  isRead: boolean;
};
