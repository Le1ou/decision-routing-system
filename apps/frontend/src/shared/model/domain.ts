export type UserRole = "author" | "executor" | "manager" | "top-manager";

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
  | "cancel"
  | "archive"
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

export type PrioritySettings = {
  department: Record<string, number>;
  deadline: number;
  managerAuthor: Record<string, number>;
  urgent: {
    thresholdHours: number;
    bonus: number;
  };
};

export type Position = {
  id: string;
  name: string;
  gradeIds: string[];
};

export type JobTitle = Position & {
  isTop: boolean;
};

export type Grade = {
  id: string;
  name: string;
};

export type User = {
  id: string;
  login: string;
  fullName: string;
  roles: UserRole[];
  role: UserRole;
  departmentId: string;
  postName: string;
  positionId: string;
  jobTitleId: string;
  isActive: boolean;
};

export type AdUser = {
  id: string;
  login: string;
  fullName: string;
  departmentId: string;
  postName: string;
};

export type UserPermissions = {
  canManageEmployees: boolean;
  canManageWorkTypes: boolean;
  canManagePrioritySettings: boolean;
  canViewReports: boolean;
};

export type WorkType = {
  id: string;
  name: string;
  departmentId: string;
  complexity: Complexity;
  allowedGradeIds: string[];
  allowedPositionIds: string[];
};

export type ChatMessage = {
  id: string;
  applicationId: string;
  authorId?: string;
  text: string;
  createdAt: string;
  author?: User;
};

export type Attachment = {
  id: string;
  applicationId?: string;
  name: string;
  type: "photo" | "document";
  url?: string;
};

export type Delegation = {
  id: string;
  applicationId: string;
  delegatedByDepartmentId: string;
  delegatedByEmployeeId?: string;
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
  archivedAt?: string;
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
  availableActions?: ApplicationAction[];
  attachments?: Attachment[];
  delegation?: Delegation;
  workType?: WorkType;
  author?: User;
  executor?: User;
  previousExecutor?: User;
  delegatedByEmployee?: User;
  department?: Department;
};

export type Notification = {
  id: string;
  text: string;
  applicationId?: string;
  createdAt: string;
  isRead: boolean;
};
