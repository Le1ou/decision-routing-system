export type UserRole = "author" | "executor" | "manager";

export type RequestStatus =
  | "new"
  | "assigned"
  | "delegated"
  | "inProgress"
  | "rejected"
  | "completed";

export type RequestPriority = "low" | "medium" | "high" | "critical";

export type Department = {
  id: string;
  name: string;
  value: number;
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
};

export type WorkType = {
  id: string;
  name: string;
  departmentId: string;
  complexity: "easy" | "medium" | "hard" | "critical";
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
  isUnfinished: boolean;
  createdAt: string;
  deadlineAt: string;
  startedAt?: string;
  finishedAt?: string;
};

export type Notification = {
  id: string;
  text: string;
  requestId?: string;
  createdAt: string;
  isRead: boolean;
};
