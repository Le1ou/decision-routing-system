import { env } from "@shared/config/env";
import type {
  ApplicationAction,
  ApplicationStatus,
  Complexity,
  PrioritySettings,
  UserPermissions,
  UserRole,
} from "@shared/model/domain";

export type ApiCredentials = {
  login: string;
  password: string;
};

export type CurrentUserDto = {
  user: {
    id: string;
    login: string;
    fullName: string;
    roles: UserRole[];
    departmentId: string;
    postName: string;
    positionId: string;
    isActive: boolean;
  };
  permissions: UserPermissions;
};

export type ListResponse<T> = {
  items: T[];
};

export type IdResponse = {
  id: string;
};

export type DepartmentDto = {
  id: string;
  name: string;
  value: number;
  delegatedToSameDepartment: boolean;
  employeeApplicationDelayMinutes: number;
  deadlineNotificationRatio: number;
};

export type PositionDto = {
  id: string;
  name: string;
  gradeIds?: string[];
};

export type GradeDto = {
  id: string;
  name: string;
};

export type WorkTypeDto = {
  id: string;
  name: string;
  departmentId: string;
  complexity: Complexity;
  allowedGradeIds: string[];
};

export type UserDto = {
  id: string;
  login: string;
  fullName: string;
  role: UserRole;
  departmentId: string;
  postName: string;
  positionId: string;
  isActive: boolean;
};

export type AdUserDto = {
  adUserId: string;
  login: string;
  fullName: string;
  departmentId: string;
  postName: string;
};

export type ApplicationListDto = {
  items: ApplicationListItemDto[];
  pagination: {
    page: number;
    pageSize: number;
    total: number;
  };
};

export type ApplicationListItemDto = {
  id: string;
  name: string;
  status: ApplicationStatus;
  priority: "low" | "medium" | "high" | "critical";
  createdAt: string;
  finishedAt?: string | null;
};

export type ApplicationDetailDto = ApplicationListItemDto & {
  description: string;
  departmentId: string;
  workTypeId: string;
  authorId: string;
  isUnfinished: boolean;
  deadlineAt: string;
  updatedAt: string;
  executorId?: string | null;
  previousExecutorId?: string | null;
  executorComment?: string | null;
  managerComment?: string | null;
  resultText?: string | null;
  archivedAt?: string | null;
  delegationId?: string | null;
  delegatedFromDepartmentId?: string | null;
  delegatedToDepartmentId?: string | null;
  assignedComplexity?: Complexity | null;
  assignedAt?: string | null;
  startedAt?: string | null;
  closedById?: string | null;
  availableActions?: ApplicationAction[];
  attachments?: Array<Record<string, unknown>>;
  delegation?: Record<string, unknown> | null;
  workType?: Record<string, unknown> | null;
  author?: Record<string, unknown> | null;
  executor?: Record<string, unknown> | null;
  previousExecutor?: Record<string, unknown> | null;
  delegatedByEmployee?: Record<string, unknown> | null;
  department?: Record<string, unknown> | null;
};

export type ApplicationDetailResponseDto = {
  application: ApplicationDetailDto;
};

export type ApplicationReportResponseDto = {
  items: Array<{
    applicationId: string;
    name: string;
    status: ApplicationStatus;
    priority: "low" | "medium" | "high" | "critical";
    createdAt: string;
    executorId?: string | null;
    executorName?: string | null;
    departmentName?: string | null;
    workTypeName?: string | null;
    startedAt?: string | null;
    finishedAt?: string | null;
  }>;
  summary: {
    total: number;
    completed: number;
    inProgressOrAssigned: number;
  };
};

export type RangeMetricDto = {
  min: number | null;
  avg: number | null;
  max: number | null;
};

export type AnalyticsPeriodDto = {
  from: string | null;
  to: string | null;
} | null;

export type AnalyticsMetaDto = {
  scope: "all" | "department";
  departmentId: string | null;
  period: AnalyticsPeriodDto;
};

export type ApplicationsAnalyticsResponseDto = AnalyticsMetaDto & {
  total: number;
  byStatus: Partial<Record<ApplicationStatus, number>>;
  byPriority: Partial<Record<"low" | "medium" | "high" | "critical", number>>;
  byComplexity: Partial<Record<Complexity, number>>;
  completionTimeSeconds: RangeMetricDto;
  timeToAssignSeconds: RangeMetricDto;
  timeWithoutExecutorSeconds: RangeMetricDto;
  timePerStatusSeconds: Partial<Record<ApplicationStatus, RangeMetricDto>>;
  delegations: {
    total: number;
    confirmed: number;
    declined: number;
    pending: number;
    decisionTimeSeconds: RangeMetricDto;
  };
};

export type ExecutorsAnalyticsResponseDto = AnalyticsMetaDto & {
  executors: Array<{
    employeeId: string;
    fullName: string;
    departmentId: string;
    assignedCount: number;
    completedCount: number;
    inProgressCount: number;
    takenInWorkCount: number;
    rejectedCount: number;
    delegatedCount: number;
    byPriority: Partial<Record<"low" | "medium" | "high" | "critical", number>>;
    avgReactionTimeSeconds: number | null;
    avgHandlingTimeSeconds: number | null;
    totalWorkSeconds: number | null;
    idleTimeSeconds: number | null;
    occupancyRatio: number | null;
  }>;
};

export type WorkTypesAnalyticsResponseDto = AnalyticsMetaDto & {
  workTypes: Array<{
    workTypeId: string;
    name: string;
    departmentId: string;
    createdCount: number;
    completedCount: number;
    delegatedCount: number;
    byPriority: Partial<Record<"low" | "medium" | "high" | "critical", number>>;
    avgCompletionTimeSeconds: number | null;
    topExecutorId: string | null;
    topExecutorName: string | null;
  }>;
};

export type DepartmentsAnalyticsResponseDto = AnalyticsMetaDto & {
  departments: Array<{
    departmentId: string;
    name: string;
    employeeCount: number;
    applicationCount: number;
    completedCount: number;
    avgReactionTimeSeconds: number | null;
    idleTimeSeconds: number | null;
    occupancyRatio: number | null;
    delegations: {
      sent: number;
      received: number;
    };
  }>;
};

export type NotificationDto = {
  id: string;
  text: string;
  applicationId?: string | null;
  createdAt: string;
  isRead: boolean;
};

export type NotificationsResponseDto = {
  items: NotificationDto[];
  unreadCount: number;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

type QueryParams = Record<string, string | number | boolean | null | undefined>;

function authHeader(credentials: ApiCredentials) {
  return `Basic ${window.btoa(`${credentials.login}:${credentials.password}`)}`;
}

export async function apiRequest<T>(path: string, credentials: ApiCredentials, options: RequestInit = {}) {
  const response = await fetch(`${env.apiUrl}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      Authorization: authHeader(credentials),
      ...options.headers,
    },
  });

  if (!response.ok) {
    throw new ApiError(response.statusText || "API request failed", response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

function withQuery(path: string, params: QueryParams = {}) {
  const searchParams = new URLSearchParams();

  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, String(value));
    }
  });

  const query = searchParams.toString();

  return query ? `${path}?${query}` : path;
}

function jsonRequest<T>(path: string, credentials: ApiCredentials, body: unknown, options: RequestInit = {}) {
  return apiRequest<T>(path, credentials, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    body: JSON.stringify(body),
  });
}

export const apiClient = {
  getCurrentUser: (credentials: ApiCredentials) => apiRequest<CurrentUserDto>("/auth/me", credentials),
  getApplications: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<ApplicationListDto>(withQuery("/applications", params), credentials),
  getApplication: (credentials: ApiCredentials, applicationId: string) =>
    apiRequest<ApplicationDetailResponseDto>(`/applications/${applicationId}`, credentials),
  createApplication: (
    credentials: ApiCredentials,
    payload: { name: string; departmentId: string; workTypeId: string; deadlineAt: string; description: string },
  ) => jsonRequest<IdResponse>("/applications", credentials, payload, { method: "POST" }),
  uploadAttachments: (credentials: ApiCredentials, applicationId: string, files: File[]) => {
    const formData = new FormData();

    files.forEach((file) => formData.append("files", file));

    return apiRequest<{ items: IdResponse[] }>(`/applications/${applicationId}/attachments`, credentials, {
      method: "POST",
      body: formData,
    });
  },
  performApplicationAction: (
    credentials: ApiCredentials,
    applicationId: string,
    payload: {
      action: ApplicationAction;
      executorId?: string;
      departmentId?: string;
      workTypeId?: string;
      comment?: string;
      complexity?: Complexity;
      resultText?: string;
      description?: string;
    },
  ) => jsonRequest<void>(`/applications/${applicationId}/actions`, credentials, payload, { method: "POST" }),
  getDepartments: (credentials: ApiCredentials) => apiRequest<ListResponse<DepartmentDto>>("/departments", credentials),
  getPositions: (credentials: ApiCredentials) => apiRequest<ListResponse<PositionDto>>("/positions", credentials),
  getGrades: (credentials: ApiCredentials) => apiRequest<ListResponse<GradeDto>>("/grades", credentials),
  getWorkTypes: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<ListResponse<WorkTypeDto>>(withQuery("/work-types", params), credentials),
  createWorkType: (
    credentials: ApiCredentials,
    payload: { name: string; departmentId: string; complexity: Complexity; allowedGradeIds: string[] },
  ) => jsonRequest<IdResponse>("/work-types", credentials, payload, { method: "POST" }),
  updateWorkType: (
    credentials: ApiCredentials,
    workTypeId: string,
    payload: Partial<{ name: string; departmentId: string; complexity: Complexity; allowedGradeIds: string[] }>,
  ) => jsonRequest<void>(`/work-types/${workTypeId}`, credentials, payload, { method: "PATCH" }),
  deleteWorkType: (credentials: ApiCredentials, workTypeId: string) =>
    apiRequest<void>(`/work-types/${workTypeId}`, credentials, { method: "DELETE" }),
  getEmployees: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<ListResponse<UserDto>>(withQuery("/employees", params), credentials),
  getAdUsers: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<ListResponse<AdUserDto>>(withQuery("/ad/users", params), credentials),
  createEmployee: (credentials: ApiCredentials, payload: { adUserId: string; role: UserRole; isActive: boolean }) =>
    jsonRequest<IdResponse>("/employees", credentials, payload, { method: "POST" }),
  updateEmployee: (credentials: ApiCredentials, employeeId: string, payload: Partial<{ role: UserRole; isActive: boolean }>) =>
    jsonRequest<void>(`/employees/${employeeId}`, credentials, payload, { method: "PATCH" }),
  deleteEmployee: (credentials: ApiCredentials, employeeId: string) =>
    apiRequest<void>(`/employees/${employeeId}`, credentials, { method: "DELETE" }),
  updateDepartmentDelegationSettings: (
    credentials: ApiCredentials,
    departmentId: string,
    payload: { delegatedToSameDepartment: boolean },
  ) => jsonRequest<void>(`/departments/${departmentId}/delegation-settings`, credentials, payload, { method: "PATCH" }),
  updateDepartmentSettings: (
    credentials: ApiCredentials,
    departmentId: string,
    payload: Partial<{ employeeApplicationDelayMinutes: number; deadlineNotificationRatio: number }>,
  ) => jsonRequest<void>(`/departments/${departmentId}`, credentials, payload, { method: "PATCH" }),
  getPrioritySettings: (credentials: ApiCredentials) => apiRequest<PrioritySettings>("/priority-settings", credentials),
  updatePrioritySettings: (credentials: ApiCredentials, payload: Omit<PrioritySettings, "urgent">) =>
    jsonRequest<PrioritySettings>("/priority-settings", credentials, payload, { method: "PUT" }),
  getNotifications: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<NotificationsResponseDto>(withQuery("/notifications", params), credentials),
  markNotificationRead: (credentials: ApiCredentials, notificationId: string) =>
    apiRequest<void>(`/notifications/${notificationId}/read`, credentials, { method: "POST" }),
  markAllNotificationsRead: (credentials: ApiCredentials) =>
    apiRequest<void>("/notifications/read-all", credentials, { method: "POST" }),
  getApplicationReport: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<ApplicationReportResponseDto>(withQuery("/reports/applications", params), credentials),
  getApplicationReportXlsUrl: (params?: QueryParams) => `${env.apiUrl}${withQuery("/reports/applications.xls", params)}`,
  getApplicationsAnalytics: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<ApplicationsAnalyticsResponseDto>(withQuery("/analytics/applications", params), credentials),
  getExecutorsAnalytics: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<ExecutorsAnalyticsResponseDto>(withQuery("/analytics/executors", params), credentials),
  getWorkTypesAnalytics: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<WorkTypesAnalyticsResponseDto>(withQuery("/analytics/work-types", params), credentials),
  getDepartmentsAnalytics: (credentials: ApiCredentials, params?: QueryParams) =>
    apiRequest<DepartmentsAnalyticsResponseDto>(withQuery("/analytics/departments", params), credentials),
};
