"""
schemas.py — Pydantic-модели API-контракта и enum-константы.

Вынесено из main.py при декомпозиции: здесь ТОЛЬКО форма данных (вход/выход API),
конвертеры int↔str для справочных значений и обёртки ответов. Никакой работы с БД,
никаких side-effects — модуль можно импортировать из любого места без последствий.
"""

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

# ─────────────────────────── Enum constants ───────────────────────────

ComplexityValues   = ["easy", "medium", "hard"]
StatusValues       = ["new", "assigned", "delegated", "inProgress", "rejected", "completed"]
PriorityValues     = ["low", "medium", "high", "critical"]
RoleValues         = ["author", "executor", "manager", "top-manager"]
ActionValues       = [
    "editDescription", "assignExecutor", "startWork", "reject", "complete",
    "delegateInternal", "delegateExternal", "returnToNew", "cancel", "archive",
    "confirmExternalDelegation", "declineExternalDelegation", "changeWorkType",
]


def complexity_int_to_str(value) -> str:
    if isinstance(value, int):
        if 1 <= value <= len(ComplexityValues):
            return ComplexityValues[value - 1]
        raise ValueError(f"Complexity index {value} out of range")
    return value


def status_int_to_str(value) -> str:
    if isinstance(value, int):
        if 1 <= value <= len(StatusValues):
            return StatusValues[value - 1]  # status table starts at id=1
        raise ValueError(f"Status index {value} out of range")
    return value


def priority_int_to_str(value) -> str:
    if isinstance(value, int):
        if 1 <= value <= len(PriorityValues):
            return PriorityValues[value - 1]  # priority table starts at id=1
        raise ValueError(f"Priority index {value} out of range")
    return value


def coerce_str(v) -> str:
    return str(v) if v is not None else ""


def coerce_str_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(i) for i in v]
    return v


CoercedStr    = Annotated[str, BeforeValidator(coerce_str)]
ListOfStrings = Annotated[list[str], BeforeValidator(coerce_str_list)]

# ─────────────────────────── Pydantic models ─────────────────────────

# ── Auth ──

class UserPermissionsOut(BaseModel):
    canManageEmployees: bool
    canManageWorkTypes: bool
    canManagePrioritySettings: bool
    canViewReports: bool

class CurrentUserOut(BaseModel):
    """Authenticated user (/auth/me). Carries the full set of system roles."""
    id: CoercedStr        = Field(validation_alias="employee_id")
    login: CoercedStr     = Field(validation_alias="login")
    fullName: CoercedStr  = Field(validation_alias="fio")
    roles: ListOfStrings
    departmentId: CoercedStr = Field(validation_alias="department_id")
    postName: CoercedStr     = Field(validation_alias="post_name")
    positionId: CoercedStr   = Field(validation_alias="post_id")
    isActive: bool           = Field(validation_alias="is_active")

    model_config = {"populate_by_name": True}

class UserOut(BaseModel):
    """Directory employee. Per the contract this carries a single `role`."""
    id: CoercedStr        = Field(validation_alias="employee_id")
    login: CoercedStr     = Field(validation_alias="login")
    fullName: CoercedStr  = Field(validation_alias="fio")
    role: str             = Field(validation_alias="role")
    departmentId: CoercedStr = Field(validation_alias="department_id")
    postName: CoercedStr     = Field(validation_alias="post_name")
    positionId: CoercedStr   = Field(validation_alias="post_id")
    isActive: bool           = Field(validation_alias="is_active")

    model_config = {"populate_by_name": True}

# ── Departments ──

class DepartmentOut(BaseModel):
    id: CoercedStr                   = Field(validation_alias="department_id")
    name: CoercedStr                 = Field(validation_alias="name")
    value: float                     = Field(validation_alias="value")
    delegatedToSameDepartment: bool  = Field(validation_alias="delegated_to_same_dep")
    employeeApplicationDelayMinutes: int = Field(validation_alias="empl_appl_delay")
    deadlineNotificationRatio: float = Field(validation_alias="deadline_notification")

    model_config = {"populate_by_name": True}

# ── Positions (должности) ──

class PositionOut(BaseModel):
    """A job title (должность) coming from AD; maps to the `post` table.

    gradeIds — грейды, доступные этой должности (матрица post_grade). Пересечение с
    workType.allowedGradeIds даёт должности, которые могут приступать к виду работ.
    """
    id: CoercedStr   = Field(validation_alias="post_id")
    name: CoercedStr = Field(validation_alias="name")
    gradeIds: ListOfStrings = Field(default_factory=list, validation_alias="grade_ids")

    model_config = {"populate_by_name": True}

# ── Grades (грейды) ──

class GradeOut(BaseModel):
    """A grade used only in the work-type allowed-grades matrix."""
    id: CoercedStr   = Field(validation_alias="grade_id")
    name: CoercedStr = Field(validation_alias="name")

    model_config = {"populate_by_name": True}

# ── Work-types ──

class WorkTypeOut(BaseModel):
    id: CoercedStr           = Field(validation_alias="type_of_works_id")
    name: CoercedStr         = Field(validation_alias="name")
    departmentId: CoercedStr = Field(validation_alias="department_id")
    complexity: Literal["easy", "medium", "hard"] = Field(
        validation_alias="complexity_value"
    )
    allowedGradeIds: ListOfStrings = Field(validation_alias="grade_ids")
    # Допустимые должности (вторая ось матрицы допуска). ПУСТОЙ список = ограничения
    # по должности нет (любая должность с подходящим грейдом).
    allowedPositionIds: ListOfStrings = Field(default_factory=list, validation_alias="post_ids")

    @field_validator("complexity", mode="before")
    @classmethod
    def parse_complexity(cls, v):
        return complexity_int_to_str(v)

    model_config = {"populate_by_name": True}

class CreateWorkTypePayload(BaseModel):
    name: str = Field(min_length=1)
    departmentId: str
    complexity: Literal["easy", "medium", "hard"]
    allowedGradeIds: list[str] = Field(min_length=1)
    # Необязательно (старый фронт не присылает): пустой список = любая должность.
    allowedPositionIds: list[str] = Field(default_factory=list)

class UpdateWorkTypePayload(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    departmentId: Optional[str] = None
    complexity: Optional[Literal["easy", "medium", "hard"]] = None
    allowedGradeIds: Optional[list[str]] = None
    # None = не менять; [] = снять ограничение по должности (любая должность).
    allowedPositionIds: Optional[list[str]] = None

    @model_validator(mode="after")
    def at_least_one(self):
        if (self.name is None and self.departmentId is None
                and self.complexity is None and self.allowedGradeIds is None
                and self.allowedPositionIds is None):
            raise ValueError("At least one field must be provided")
        if self.allowedGradeIds is not None and len(self.allowedGradeIds) < 1:
            raise ValueError("allowedGradeIds must contain at least one grade")
        return self

# ── Employees ──

class CreateEmployeePayload(BaseModel):
    adUserId: str
    role: Literal["author", "executor", "manager", "top-manager"]
    isActive: bool

class UpdateEmployeePayload(BaseModel):
    role: Optional[Literal["author", "executor", "manager", "top-manager"]] = None
    isActive: Optional[bool] = None

    @model_validator(mode="after")
    def at_least_one(self):
        if self.role is None and self.isActive is None:
            raise ValueError("At least one field must be provided")
        return self

# ── Department settings ──

class UpdateDepartmentDelegationSettingsPayload(BaseModel):
    delegatedToSameDepartment: bool

class UpdateDepartmentPayload(BaseModel):
    # Department settings editable by its manager: assignment cooldown (minutes) and
    # the deadline-notification ratio (share of remaining time that triggers the alert).
    employeeApplicationDelayMinutes: Optional[int] = Field(default=None, ge=0)
    deadlineNotificationRatio: Optional[float] = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def at_least_one(self):
        if self.employeeApplicationDelayMinutes is None and self.deadlineNotificationRatio is None:
            raise ValueError("At least one field must be provided")
        return self

# ── AD users ──

class AdUserOut(BaseModel):
    adUserId: CoercedStr   = Field(validation_alias="ad_user_id")
    login: CoercedStr      = Field(validation_alias="login")
    fullName: CoercedStr   = Field(validation_alias="fio")
    departmentId: CoercedStr = Field(validation_alias="department_id")
    postName: CoercedStr   = Field(validation_alias="post_name")

    model_config = {"populate_by_name": True}

# ── Applications ──

class AttachmentOut(BaseModel):
    id: CoercedStr            = Field(validation_alias="photo_id")
    applicationId: CoercedStr = Field(validation_alias="application_id")
    name: CoercedStr          = Field(validation_alias="name")
    type: str                 = Field(default="photo")
    url: Optional[str]        = Field(default=None, validation_alias="url")

    model_config = {"populate_by_name": True}

class DelegationOut(BaseModel):
    id: CoercedStr                    = Field(validation_alias="delegated_id")
    applicationId: CoercedStr         = Field(validation_alias="application_id")
    delegatedByDepartmentId: CoercedStr  = Field(validation_alias="delegated_by")
    delegatedByEmployeeId: Optional[CoercedStr] = Field(default=None, validation_alias="delegated_by_employee")
    delegatedFromDepartmentId: CoercedStr = Field(validation_alias="delegated_from")
    delegatedToDepartmentId: CoercedStr   = Field(validation_alias="delegated_to")
    comment: CoercedStr               = Field(validation_alias="comment")
    createdAt: str                    = Field(validation_alias="created_at")
    decision: Optional[str]           = Field(default=None, validation_alias="decision")
    decidedAt: Optional[str]          = Field(default=None, validation_alias="decided_at")

    @field_validator("createdAt", mode="before")
    @classmethod
    def fmt_dt(cls, v):
        return v.isoformat() if isinstance(v, datetime) else str(v)

    @field_validator("decidedAt", mode="before")
    @classmethod
    def fmt_decided(cls, v):
        # decided_at is set once a delegation is confirmed/declined; format the
        # datetime to ISO (it was previously left as a datetime → 422 on GET).
        if v is None:
            return None
        return v.isoformat() if isinstance(v, datetime) else str(v)

    model_config = {"populate_by_name": True}

class ApplicationListItemOut(BaseModel):
    id: CoercedStr       = Field(validation_alias="application_id")
    name: CoercedStr     = Field(validation_alias="name")
    status: str          = Field(validation_alias="status_name")
    priority: str        = Field(validation_alias="priority_name")
    createdAt: str       = Field(validation_alias="created_at")
    finishedAt: Optional[str] = Field(default=None, validation_alias="finished_at")

    @field_validator("createdAt", mode="before")
    @classmethod
    def fmt_created(cls, v):
        return v.isoformat() if isinstance(v, datetime) else str(v)

    @field_validator("finishedAt", mode="before")
    @classmethod
    def fmt_finished(cls, v):
        if v is None:
            return None
        return v.isoformat() if isinstance(v, datetime) else str(v)

    model_config = {"populate_by_name": True}

class ApplicationDetailOut(ApplicationListItemOut):
    description: CoercedStr       = Field(validation_alias="description")
    departmentId: CoercedStr      = Field(validation_alias="department_id")
    workTypeId: CoercedStr        = Field(validation_alias="types_of_works")
    authorId: CoercedStr          = Field(validation_alias="author_id")
    isUnfinished: bool            = Field(validation_alias="is_unfinished")
    deadlineAt: str               = Field(validation_alias="deadline")
    updatedAt: str                = Field(validation_alias="updated_at")
    executorId: Optional[CoercedStr]  = Field(default=None, validation_alias="executor_id")
    previousExecutorId: Optional[CoercedStr] = Field(default=None, validation_alias="previous_executor_id")
    executorComment: Optional[str]   = Field(default=None, validation_alias="executor_comment")
    managerComment: Optional[str]    = Field(default=None, validation_alias="manager_comment")
    resultText: Optional[str]        = Field(default=None, validation_alias="result_text")
    archivedAt: Optional[str]        = Field(default=None, validation_alias="archived_at")
    delegationId: Optional[CoercedStr] = Field(default=None, validation_alias="delegated_id")
    delegatedFromDepartmentId: Optional[CoercedStr] = Field(default=None, validation_alias="delegated_from_department_id")
    delegatedToDepartmentId: Optional[CoercedStr]   = Field(default=None, validation_alias="delegated_to_department_id")
    assignedComplexity: Optional[CoercedStr] = Field(default=None, validation_alias="empl_assigned_complexity")
    assignedAt: Optional[str]     = Field(default=None, validation_alias="executor_at")
    startedAt: Optional[str]      = Field(default=None, validation_alias="work_at")
    closedById: Optional[CoercedStr] = Field(default=None, validation_alias="closed_by_id")
    availableActions: list[str]   = Field(default_factory=list)
    attachments: list[dict]       = Field(default_factory=list)
    delegation: Optional[dict]    = Field(default=None)
    workType: Optional[dict]      = Field(default=None)
    author: Optional[dict]        = Field(default=None)
    executor: Optional[dict]      = Field(default=None)
    previousExecutor: Optional[dict] = Field(default=None)
    delegatedByEmployee: Optional[dict] = Field(default=None)
    department: Optional[dict]    = Field(default=None)

    @field_validator("archivedAt", mode="before")
    @classmethod
    def fmt_archived(cls, v):
        if v is None:
            return None
        return v.isoformat() if isinstance(v, datetime) else str(v)

    @field_validator("deadlineAt", "updatedAt", mode="before")
    @classmethod
    def fmt_required_dt(cls, v):
        if v is None:
            return ""
        return v.isoformat() if isinstance(v, datetime) else str(v)

    @field_validator("assignedComplexity", mode="before")
    @classmethod
    def parse_complexity(cls, v):
        if v is None:
            return None
        return complexity_int_to_str(v)

    @field_validator("assignedAt", "startedAt", mode="before")
    @classmethod
    def fmt_optional_dt(cls, v):
        if v is None:
            return None
        return v.isoformat() if isinstance(v, datetime) else str(v)

    model_config = {"populate_by_name": True}

class CreateApplicationPayload(BaseModel):
    name: str = Field(min_length=1)
    departmentId: str
    workTypeId: str
    deadlineAt: datetime
    description: str = Field(min_length=1, max_length=1000)

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Настройка сервера",
                "departmentId": "1",
                "workTypeId": "2",
                "deadlineAt": "2026-06-15T12:00:00Z",
                "description": "Необходимо настроить новый сервер отдела.",
            }
        }
    }

class ApplicationActionPayload(BaseModel):
    action: str
    executorId: Optional[str]  = None
    departmentId: Optional[str]= None
    workTypeId: Optional[str]  = None
    comment: Optional[str]     = None
    complexity: Optional[Literal["easy", "medium", "hard"]] = None
    resultText: Optional[str]  = None
    description: Optional[str] = Field(default=None, max_length=1000)


# ── Priority settings ──

class PrioritySettingsModel(BaseModel):
    # Per-department coefficients keyed by departmentId (required per contract).
    department:    dict[str, float]
    # Per-department "manager as author" coefficients keyed by departmentId.
    managerAuthor: dict[str, float]
    # Single global coefficient for the deadline factor.
    deadline:      float = Field(ge=0, le=1)

    @field_validator("department", "managerAuthor")
    @classmethod
    def validate_coeffs(cls, v, info):
        # Коэффициент отдела (важность) — до 1.25; надбавка руководителя — до 1.0.
        max_val = 1.25 if info.field_name == "department" else 1.0
        for k, val in v.items():
            if val < 0 or val > max_val:
                raise ValueError(f"Coefficient for '{k}' must be between 0 and {max_val}")
        return v

class UrgentSettingsOut(BaseModel):
    """Параметры бонуса срочности (read-only, из config.json → priority)."""
    thresholdHours: float
    bonus: float

class PrioritySettingsResponse(PrioritySettingsModel):
    # GET дополнительно отдаёт read-only параметры срочности, чтобы предпросмотр на
    # фронте мог точно повторить формулу бэкенда. Через PUT не редактируются.
    urgent: UrgentSettingsOut

# ── Notifications ──

class NotificationOut(BaseModel):
    id: CoercedStr             = Field(validation_alias="notification_id")
    text: CoercedStr           = Field(validation_alias="text")
    applicationId: Optional[str] = Field(default=None, validation_alias="application_id")
    createdAt: str             = Field(validation_alias="created_at")
    isRead: bool               = Field(validation_alias="is_read")

    @field_validator("createdAt", mode="before")
    @classmethod
    def fmt_dt(cls, v):
        return v.isoformat() if isinstance(v, datetime) else str(v)

    @field_validator("applicationId", mode="before")
    @classmethod
    def coerce_app_id(cls, v):
        return str(v) if v is not None else None

    model_config = {"populate_by_name": True}

# ── Reports ──

class ApplicationReportRowOut(BaseModel):
    applicationId: CoercedStr     = Field(validation_alias="application_id")
    name: CoercedStr              = Field(validation_alias="name")
    status: str                   = Field(validation_alias="status_name")
    priority: str                 = Field(validation_alias="priority_name")
    createdAt: str                = Field(validation_alias="created_at")
    executorId: Optional[CoercedStr] = Field(default=None, validation_alias="executor_id")
    executorName: Optional[str]   = Field(default=None, validation_alias="executor_name")
    departmentName: Optional[str] = Field(default=None, validation_alias="department_name")
    workTypeName: Optional[str]   = Field(default=None, validation_alias="work_type_name")
    startedAt: Optional[str]      = Field(default=None, validation_alias="work_at")
    finishedAt: Optional[str]     = Field(default=None, validation_alias="finished_at")

    @field_validator("createdAt", mode="before")
    @classmethod
    def fmt_created(cls, v):
        return v.isoformat() if isinstance(v, datetime) else str(v)

    @field_validator("startedAt", "finishedAt", mode="before")
    @classmethod
    def fmt_opt(cls, v):
        if v is None:
            return None
        return v.isoformat() if isinstance(v, datetime) else str(v)

    model_config = {"populate_by_name": True}

# ── Application chat ──

class ChatMessageOut(BaseModel):
    id: CoercedStr            = Field(validation_alias="message_id")
    applicationId: CoercedStr = Field(validation_alias="application_id")
    authorId: Optional[CoercedStr] = Field(default=None, validation_alias="author_employee_id")
    text: CoercedStr          = Field(validation_alias="text")
    createdAt: str            = Field(validation_alias="created_at")
    author: Optional[dict]    = Field(default=None)

    @field_validator("createdAt", mode="before")
    @classmethod
    def fmt_dt(cls, v):
        return v.isoformat() if isinstance(v, datetime) else str(v)

    model_config = {"populate_by_name": True}

class CreateChatMessagePayload(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

class ChatMessagesResponse(BaseModel):
    items: list[ChatMessageOut]
    unreadCount: int = Field(ge=0)

# ─────────────────────────── Response wrappers ───────────────────────

class PaginationOut(BaseModel):
    page: int     = Field(ge=1)
    pageSize: int = Field(ge=1)
    total: int    = Field(ge=0)

class IdResponse(BaseModel):
    id: str

class CurrentUserResponse(BaseModel):
    user: CurrentUserOut
    permissions: UserPermissionsOut

class ApplicationListResponse(BaseModel):
    items: list[ApplicationListItemOut]
    pagination: PaginationOut

class ApplicationDetailResponse(BaseModel):
    application: ApplicationDetailOut

class AttachmentUploadResponse(BaseModel):
    items: list[IdResponse]

class DepartmentListResponse(BaseModel):
    items: list[DepartmentOut]

class EmployeeListResponse(BaseModel):
    items: list[UserOut]

class PositionListResponse(BaseModel):
    items: list[PositionOut]

class GradeListResponse(BaseModel):
    items: list[GradeOut]

class AdUserListResponse(BaseModel):
    items: list[AdUserOut]

class WorkTypeListResponse(BaseModel):
    items: list[WorkTypeOut]

class NotificationsResponse(BaseModel):
    items: list[NotificationOut]
    unreadCount: int = Field(ge=0)

class ReportSummaryOut(BaseModel):
    total: int              = Field(ge=0)
    completed: int          = Field(ge=0)
    inProgressOrAssigned: int = Field(ge=0)

class ApplicationReportResponse(BaseModel):
    items: list[ApplicationReportRowOut]
    summary: ReportSummaryOut
