import type {
  AdUserDto,
  ApplicationDetailDto,
  CurrentUserDto,
  NotificationDto,
  UserDto,
} from "./client";
import type { AdUser, Application, Attachment, Delegation, Department, User, WorkType } from "@shared/model/domain";
import { getPrimaryRole, normalizeRoles } from "@shared/model/roles";

export function mapCurrentUser(dto: CurrentUserDto): User {
  const roles = normalizeRoles(dto.user.roles);
  const role = getPrimaryRole(roles);

  return {
    id: dto.user.id,
    login: dto.user.login,
    fullName: dto.user.fullName,
    roles,
    role,
    departmentId: dto.user.departmentId,
    postName: dto.user.postName,
    positionId: dto.user.positionId,
    jobTitleId: dto.user.positionId,
    isActive: dto.user.isActive,
  };
}

export function mapUser(dto: UserDto | Record<string, unknown>): User {
  const raw = dto as Record<string, unknown>;
  const role = String(raw.role ?? "author") as User["role"];

  return {
    id: String(raw.id ?? raw.employee_id ?? ""),
    login: String(raw.login ?? ""),
    fullName: String(raw.fullName ?? raw.fio ?? ""),
    roles: [role],
    role,
    departmentId: String(raw.departmentId ?? raw.department_id ?? ""),
    postName: String(raw.postName ?? raw.post_name ?? ""),
    positionId: String(raw.positionId ?? raw.post_id ?? ""),
    jobTitleId: String(raw.positionId ?? raw.post_id ?? ""),
    isActive: Boolean(raw.isActive ?? raw.is_active ?? true),
  };
}

export function mapAdUser(dto: AdUserDto): AdUser {
  return {
    id: dto.adUserId,
    login: dto.login,
    fullName: dto.fullName,
    departmentId: dto.departmentId,
    postName: dto.postName,
  };
}

export function mapApplication(dto: ApplicationDetailDto): Application {
  const attachments = Array.isArray(dto.attachments)
    ? dto.attachments.map((attachment, index) => mapAttachment(attachment, dto.id, index))
    : [];

  return {
    id: dto.id,
    title: dto.name,
    description: dto.description ?? "",
    status: dto.status,
    priority: dto.priority,
    departmentId: dto.departmentId,
    workTypeId: dto.workTypeId,
    authorId: dto.authorId,
    executorId: dto.executorId ?? undefined,
    previousExecutorId: dto.previousExecutorId ?? undefined,
    executorComment: dto.executorComment ?? undefined,
    managerComment: dto.managerComment ?? undefined,
    resultText: dto.resultText ?? undefined,
    archivedAt: dto.archivedAt ?? undefined,
    delegationId: dto.delegationId ?? undefined,
    delegatedFromDepartmentId: dto.delegatedFromDepartmentId ?? undefined,
    delegatedToDepartmentId: dto.delegatedToDepartmentId ?? undefined,
    attachmentNames: attachments.map((attachment) => attachment.name),
    assignedComplexity: dto.assignedComplexity ?? undefined,
    assignedAt: dto.assignedAt ?? undefined,
    isUnfinished: dto.isUnfinished,
    createdAt: dto.createdAt,
    deadlineAt: dto.deadlineAt,
    updatedAt: dto.updatedAt,
    startedAt: dto.startedAt ?? undefined,
    finishedAt: dto.finishedAt ?? undefined,
    closedById: dto.closedById ?? undefined,
    availableActions: dto.availableActions ?? [],
    attachments,
    delegation: dto.delegation ? mapDelegation(dto.delegation, dto) : undefined,
    workType: dto.workType ? mapWorkTypeRecord(dto.workType) : undefined,
    author: dto.author ? mapUser(dto.author) : undefined,
    executor: dto.executor ? mapUser(dto.executor) : undefined,
    department: dto.department ? mapDepartmentRecord(dto.department) : undefined,
  };
}

export function mapNotification(dto: NotificationDto) {
  return {
    id: dto.id,
    text: dto.text,
    applicationId: dto.applicationId ?? undefined,
    createdAt: dto.createdAt,
    isRead: dto.isRead,
  };
}

function mapAttachment(raw: Record<string, unknown>, applicationId: string, index: number): Attachment {
  return {
    id: String(raw.id ?? raw.photo_id ?? raw.attachment_id ?? `${applicationId}-${index}`),
    applicationId,
    name: String(raw.name ?? raw.fileName ?? raw.value ?? "Вложение"),
    type: String(raw.content_type ?? raw.contentType ?? "").startsWith("image/") ? "photo" : "document",
  };
}

function mapDelegation(raw: Record<string, unknown>, application: ApplicationDetailDto): Delegation {
  return {
    id: String(raw.id ?? raw.delegationId ?? raw.delegated_id ?? application.delegationId ?? ""),
    applicationId: application.id,
    delegatedByDepartmentId: String(raw.delegatedByDepartmentId ?? raw.delegated_by ?? application.delegatedFromDepartmentId ?? ""),
    delegatedByEmployeeId: optionalString(raw.delegatedByEmployeeId ?? raw.delegated_by_employee),
    delegatedFromDepartmentId: String(raw.delegatedFromDepartmentId ?? raw.delegated_from ?? application.delegatedFromDepartmentId ?? ""),
    delegatedToDepartmentId: String(raw.delegatedToDepartmentId ?? raw.delegated_to ?? application.delegatedToDepartmentId ?? ""),
    comment: String(raw.comment ?? application.executorComment ?? ""),
    createdAt: String(raw.createdAt ?? raw.created_at ?? application.updatedAt),
    decision: optionalString(raw.decision) as Delegation["decision"],
    decidedAt: optionalString(raw.decidedAt ?? raw.decided_at),
  };
}

function mapWorkTypeRecord(raw: Record<string, unknown>): WorkType {
  return {
    id: String(raw.id ?? raw.type_of_works_id ?? ""),
    name: String(raw.name ?? ""),
    departmentId: String(raw.departmentId ?? raw.department_id ?? ""),
    complexity: String(raw.complexity ?? "medium") as WorkType["complexity"],
    allowedGradeIds: Array.isArray(raw.allowedGradeIds) ? raw.allowedGradeIds.map(String) : [],
  };
}

function mapDepartmentRecord(raw: Record<string, unknown>): Department {
  return {
    id: String(raw.id ?? raw.department_id ?? ""),
    name: String(raw.name ?? ""),
    value: Number(raw.value ?? 0),
    delegatedToSameDepartment: Boolean(raw.delegatedToSameDepartment ?? raw.delegated_to_same_dep),
    employeeApplicationDelayMinutes: Number(raw.employeeApplicationDelayMinutes ?? raw.empl_appl_delay ?? 0),
    deadlineNotificationRatio: Number(raw.deadlineNotificationRatio ?? raw.deadline_notification ?? 0),
  };
}

function optionalString(value: unknown) {
  return value === undefined || value === null ? undefined : String(value);
}
