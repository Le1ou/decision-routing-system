from src.application_module import (
    PgDbOperator, ActiveDirectoryAuth, configData, project_timezone
)
from fastapi import FastAPI, Depends, HTTPException, status, Query, UploadFile, File, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, BeforeValidator, RootModel
from pydantic import model_validator
from typing import Annotated, Literal, Optional
from datetime import datetime
import uuid
import os
import boto3
import psycopg
from src.seed import seed_database

S3_BUCKET       = os.environ.get("S3_BUCKET_NAME", "")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
S3_REGION       = os.environ.get("S3_REGION", "auto")
_s3_client: boto3.client = None

def get_s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
            region_name=S3_REGION,
        )
    return _s3_client

# ─────────────────────────── App bootstrap ───────────────────────────

DBController = PgDbOperator("postgres", "postgres")
DBController.fillDbRolesBasedOnADTest(configData["ROLES"])
DBController.fillPermissionRoles(configData["PERMISSIONS"])
with DBController.pool.connection() as _conn:
    _conn.execute("ALTER TABLE public.employee ADD COLUMN IF NOT EXISTS is_active boolean")
authObj = ActiveDirectoryAuth()
seed_database(DBController)
for _username, _user_cfg in configData["MOCK_USERS_DB"].items():
    DBController.createUserRole(_username, _user_cfg["password"], _user_cfg["pgRoles"])
app = FastAPI(
    title="Decision Routing System API",
    version="0.1.0",
    openapi_tags=[
        {"name": "Auth",          "description": "Текущий пользователь"},
        {"name": "Applications",  "description": "Производственные заявки"},
        {"name": "Directories",   "description": "Отделы, сотрудники, должности и виды работ"},
        {"name": "Priority",      "description": "Настройки расчета приоритета"},
        {"name": "Notifications", "description": "Уведомления текущего пользователя"},
        {"name": "Reports",       "description": "Отчеты и XLS-выгрузка"},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────── Enum constants ───────────────────────────

ComplexityValues   = ["easy", "medium", "hard", "critical"]
StatusValues       = ["new", "assigned", "delegated", "inProgress", "rejected", "completed"]
PriorityValues     = ["low", "medium", "high", "critical"]
RoleValues         = ["author", "executor", "manager"]
ActionValues       = [
    "editDescription", "assignExecutor", "startWork", "reject", "complete",
    "delegateInternal", "delegateExternal", "returnToNew",
    "confirmExternalDelegation", "declineExternalDelegation", "changeWorkType",
]

# ─────────────────────────── Helpers ─────────────────────────────────

def get_db_user(userData) -> PgDbOperator:
    """Create a per-request DB operator using the authenticated user credentials."""
    DBController.createUserRole(
        userData[0], userData[1],
        configData["MOCK_USERS_DB"][userData[0]]["pgRoles"]
    )
    return PgDbOperator(userData[0], userData[1])


def require_permission(userData, permission: str):
    """Raise 403 if the user does not hold the given permission role in the database."""
    with DBController.pool.connection() as conn:
        row = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_auth_members m
                JOIN pg_roles r ON r.oid = m.roleid
                JOIN pg_roles u ON u.oid = m.member
                WHERE r.rolname = %s AND u.rolname = %s
            )
            """,
            (permission.lower(), userData[0].lower()),
        ).fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Insufficient permissions")


def row_or_404(row, detail="Not found"):
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return row


def _raise_for_db_error(e: Exception) -> None:
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        raise HTTPException(status_code=400, detail="Referenced entity does not exist")
    if isinstance(e, psycopg.errors.UniqueViolation):
        raise HTTPException(status_code=409, detail="Entity already exists")
    if isinstance(e, ValueError):
        raise HTTPException(status_code=422, detail=str(e))
    raise HTTPException(status_code=500, detail=str(e))


def complexity_int_to_str(value) -> str:
    if isinstance(value, int):
        if 0 <= value < len(ComplexityValues):
            return ComplexityValues[value]
        raise ValueError(f"Complexity index {value} out of range")
    return value


def status_int_to_str(value) -> str:
    if isinstance(value, int):
        if 0 <= value < len(StatusValues):
            return StatusValues[value - 1]  # status table starts at id=1
        raise ValueError(f"Status index {value} out of range")
    return value


def priority_int_to_str(value) -> str:
    if isinstance(value, int):
        if 0 <= value < len(PriorityValues):
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
    canCreateApplications: bool
    canExecuteApplications: bool
    canManageDepartment: bool
    canManageEmployees: bool
    canManageWorkTypes: bool
    canManagePrioritySettings: bool
    canViewReports: bool

class UserOut(BaseModel):
    id: CoercedStr        = Field(validation_alias="employee_id")
    login: CoercedStr     = Field(validation_alias="login")
    fullName: CoercedStr  = Field(validation_alias="fio")
    roles: ListOfStrings
    departmentId: CoercedStr = Field(validation_alias="department_id")
    postName: CoercedStr     = Field(validation_alias="post_name")
    positionId: CoercedStr   = Field(validation_alias="post_grade_id")
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

# ── Positions ──

class PositionOut(BaseModel):
    id: CoercedStr   = Field(validation_alias="post_grade_id")
    name: CoercedStr = Field(validation_alias="pg_name")   # assembled by query

    model_config = {"populate_by_name": True}

# ── Work-types ──

class WorkTypeOut(BaseModel):
    id: CoercedStr           = Field(validation_alias="type_of_works_id")
    name: CoercedStr         = Field(validation_alias="name")
    departmentId: CoercedStr = Field(validation_alias="department_id")
    complexity: Literal["easy", "medium", "hard", "critical"] = Field(
        validation_alias="complexity_value"
    )
    allowedPositionIds: ListOfStrings = Field(validation_alias="post_grade_ids")

    @field_validator("complexity", mode="before")
    @classmethod
    def parse_complexity(cls, v):
        return complexity_int_to_str(v)

    model_config = {"populate_by_name": True}

class CreateWorkTypePayload(BaseModel):
    name: str
    departmentId: int
    complexity: Literal["easy", "medium", "hard", "critical"]

# ── Employees ──

class CreateEmployeePayload(BaseModel):
    adUserId: str
    positionId: str
    isActive: bool

class UpdateEmployeePayload(BaseModel):
    positionId: Optional[str] = None
    isActive: Optional[bool] = None

    @model_validator(mode="after")
    def at_least_one(self):
        if self.positionId is None and self.isActive is None:
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
    executorComment: Optional[str]   = Field(default=None, validation_alias="executor_comment")
    managerComment: Optional[str]    = Field(default=None, validation_alias="manager_comment")
    resultText: Optional[str]        = Field(default=None, validation_alias="result_text")
    delegationId: Optional[CoercedStr] = Field(default=None, validation_alias="delegated_id")
    assignedComplexity: Optional[CoercedStr] = Field(default=None, validation_alias="empl_assigned_complexity")
    assignedAt: Optional[str]     = Field(default=None, validation_alias="executor_at")
    startedAt: Optional[str]      = Field(default=None, validation_alias="work_at")
    availableActions: list[str]   = Field(default_factory=list)
    attachments: list[dict]       = Field(default_factory=list)
    delegation: Optional[dict]    = Field(default=None)

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
    name: str
    departmentId: str
    workTypeId: str
    deadlineAt: datetime
    description: str = Field(max_length=1000)

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
    complexity: Optional[str]  = None
    resultText: Optional[str]  = None
    description: Optional[str] = None


# ── Priority settings ──

class PrioritySettingsModel(BaseModel):
    department:   float = Field(ge=0, le=1)
    position:     float = Field(ge=0, le=1)
    workType:     float = Field(ge=0, le=1)
    deadline:     float = Field(ge=0, le=1)
    managerAuthor:float = Field(ge=0, le=1)

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

# ─────────────────────────── Response wrappers ───────────────────────

class PaginationOut(BaseModel):
    page: int     = Field(ge=1)
    pageSize: int = Field(ge=1)
    total: int    = Field(ge=0)

class IdResponse(BaseModel):
    id: str

class CurrentUserResponse(BaseModel):
    user: UserOut
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

# ─────────────────────────── Business logic helpers ──────────────────

def _available_actions(app_row: dict, user_role: str) -> list[str]:
    """Derive which actions are available based on current status and user role."""
    status_name = app_row.get("status_name", "")
    actions = []

    if user_role == "manager":
        if status_name == "new":
            actions += ["assignExecutor", "delegateExternal", "editDescription", "changeWorkType"]
        elif status_name == "assigned":
            actions += ["assignExecutor", "delegateExternal", "reject"]
        elif status_name == "delegated":
            actions += ["assignExecutor", "confirmExternalDelegation", "declineExternalDelegation"]
        elif status_name == "inProgress":
            actions += ["assignExecutor", "reject"]
        elif status_name in ("completed", "rejected"):
            pass

    elif user_role == "executor":
        if status_name == "assigned":
            actions += ["startWork", "reject"]
        elif status_name == "inProgress":
            actions += ["complete", "reject"]

    elif user_role == "author":
        if status_name == "new":
            actions += ["editDescription"]

    return actions


def _resolve_employee_id(db: PgDbOperator, login: str) -> Optional[int]:
    """Return the employee_id for a given login from the mock config."""
    user_cfg = configData["MOCK_USERS_DB"].get(login, {})
    emp_id = user_cfg.get("employee_id")
    return int(emp_id) if emp_id is not None else None


def _get_user_role(login: str) -> str:
    """Return the highest-privilege role a user holds (manager > executor > author)."""
    roles = configData["MOCK_USERS_DB"].get(login, {}).get("roles", ["author"])
    for r in ("manager", "executor", "author"):
        if r in roles:
            return r
    return "author"


def _build_application_list_query(filters: dict) -> tuple[str, list]:
    """Build a parameterised SELECT for the applications list."""
    base = """
        SELECT
            a.application_id,
            a.name,
            a.description,
            a.is_unfinished,
            a.department_id,
            a.types_of_works,
            a.delegated_id,
            a.empl_assigned_complexity,
            a.deadline,
            a.created_at,
            a.updated_at,
            a.finished_at,
            a.executor_at,
            a.work_at,
            a.result_text,
            s.name  AS status_name,
            p.name  AS priority_name,
            p.value AS priority_value,
            author_link.employee_id AS author_id,
            exec_link.employee_id   AS executor_id
        FROM public.application a
        LEFT JOIN public.status   s ON s.status_id   = a.status_id
        LEFT JOIN public.priority p ON p.priority_id = a.priority_id
        LEFT JOIN public.employee_to_application author_link
               ON author_link.application_id = a.application_id
              AND author_link.role_id = (SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1)
        LEFT JOIN public.employee_to_application exec_link
               ON exec_link.application_id = a.application_id
              AND exec_link.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
        WHERE 1=1
    """
    params = []
    conditions = []

    if filters.get("status"):
        conditions.append(f"s.name = %s")
        params.append(filters["status"])

    if filters.get("priority"):
        conditions.append(f"p.name = %s")
        params.append(filters["priority"])

    if filters.get("applicationId"):
        conditions.append("a.application_id = %s")
        params.append(filters["applicationId"])

    if filters.get("assignedToMe") and filters.get("employee_id"):
        conditions.append("exec_link.employee_id = %s")
        params.append(filters["employee_id"])

    if filters.get("createdByMe") and filters.get("employee_id"):
        conditions.append("author_link.employee_id = %s")
        params.append(filters["employee_id"])

    if filters.get("executorName"):
        conditions.append("""
            exec_link.employee_id IN (
                SELECT employee_id FROM public.employee WHERE fio ILIKE %s
            )
        """)
        params.append(f"%{filters['executorName']}%")

    for cond in conditions:
        base += f" AND {cond}"

    sort_col_map = {
        "priority": "p.value",
        "status":   "s.name",
        "createdAt": "a.created_at",
        "finishedAt": "a.finished_at",
    }
    sort_col = sort_col_map.get(filters.get("sortBy", "priority"), "p.value")
    sort_dir = "DESC" if filters.get("sortDirection", "desc") == "desc" else "ASC"
    base += f" ORDER BY {sort_col} {sort_dir}"

    page     = max(1, filters.get("page", 1))
    pageSize = min(100, max(1, filters.get("pageSize", 50)))
    offset   = (page - 1) * pageSize
    base += f" LIMIT {pageSize} OFFSET {offset}"

    return base, params


# ═══════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════

# ─── Health ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ─── Auth ───────────────────────────────────────────────────────

@app.get("/auth/me", tags=["Auth"], summary="Получить текущего пользователя",
         description="Возвращает пользователя, найденного через Basic Auth/Active Directory, его роль, отдел, должность и права frontend.",
         response_model=CurrentUserResponse)
def get_current_user(userData=Depends(authObj.authenticate_user_test)):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_cfg = configData["MOCK_USERS_DB"].get(login, {})

        emp_id = user_cfg.get("employee_id")
        rows = db.getRowFromTable("employee", "employee_id", emp_id)
        row_or_404(rows, "Employee not found")
        row = rows[0]

        # Enrich with login and roles from config (not stored in DB)
        row["login"]  = login
        row["roles"]  = user_cfg.get("roles", ["author"])

        # Resolve post_name from post_grade → post
        pg_rows = db.getRowFromTable("post_grade", "post_grade_id", row.get("post_grade_id"))
        if pg_rows:
            post_rows = db.getRowFromTable("post", "post_id", pg_rows[0]["post_post_id"])
            row["post_name"] = post_rows[0]["name"] if post_rows else ""
        else:
            row["post_name"] = ""

        user_out = UserOut.model_validate(row)
        perms = {}
        for perm in configData["PERMISSIONS"]:
            with DBController.pool.connection() as conn:
                perm_row = conn.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM pg_auth_members m
                        JOIN pg_roles r ON r.oid = m.roleid
                        JOIN pg_roles u ON u.oid = m.member
                        WHERE r.rolname = %s AND u.rolname = %s
                    )
                    """,
                    (perm.lower(), login.lower()),
                ).fetchone()
            perms[perm] = bool(perm_row and perm_row[0])
        return {"user": user_out.model_dump(), "permissions": perms}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


# ─── Applications ────────────────────────────────────────────────

@app.get("/applications", tags=["Applications"], summary="Получить список заявок",
         description="Без query-параметров backend возвращает список заявок по умолчанию для текущего пользователя. Видимость заявок определяется ролью пользователя на backend.",
         response_model=ApplicationListResponse)
def list_applications(
    userData=Depends(authObj.authenticate_user_test),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    priority: Optional[str]      = Query(default=None),
    createdByMe: Optional[bool]  = Query(default=None),
    assignedToMe: Optional[bool] = Query(default=None),
    delegatedToMyDepartment: Optional[bool] = Query(default=None),
    executorName: Optional[str]  = Query(default=None),
    applicationId: Optional[str] = Query(default=None),
    sortBy: str                  = Query(default="priority"),
    sortDirection: str           = Query(default="desc"),
    page: int                    = Query(default=1, ge=1),
    pageSize: int                = Query(default=50, ge=1, le=100),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = configData["MOCK_USERS_DB"].get(login, {}).get("employee_id")

        filters = dict(
            status=status_filter, priority=priority,
            createdByMe=createdByMe, assignedToMe=assignedToMe,
            executorName=executorName, applicationId=applicationId,
            sortBy=sortBy, sortDirection=sortDirection,
            page=page, pageSize=pageSize, employee_id=emp_id,
        )

        query, params = _build_application_list_query(filters)

        # Count total (without LIMIT/OFFSET)
        count_query = query.split("ORDER BY")[0].replace(
            "SELECT\n        a.application_id", "SELECT COUNT(*) AS cnt", 1
        )

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                # total count
                count_q = f"""
                    SELECT COUNT(*) AS cnt
                    FROM public.application a
                    LEFT JOIN public.status   s ON s.status_id   = a.status_id
                    LEFT JOIN public.priority p ON p.priority_id = a.priority_id
                    LEFT JOIN public.employee_to_application author_link
                           ON author_link.application_id = a.application_id
                          AND author_link.role_id = (SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1)
                    LEFT JOIN public.employee_to_application exec_link
                           ON exec_link.application_id = a.application_id
                          AND exec_link.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
                    WHERE 1=1
                """
                # Re-add conditions from filters (without LIMIT/ORDER)
                count_params = []
                if status_filter:
                    count_q += " AND s.name = %s"; count_params.append(status_filter)
                if priority:
                    count_q += " AND p.name = %s"; count_params.append(priority)
                if applicationId:
                    count_q += " AND a.application_id = %s"; count_params.append(applicationId)
                if assignedToMe and emp_id:
                    count_q += " AND exec_link.employee_id = %s"; count_params.append(emp_id)
                if createdByMe and emp_id:
                    count_q += " AND author_link.employee_id = %s"; count_params.append(emp_id)
                if executorName:
                    count_q += " AND exec_link.employee_id IN (SELECT employee_id FROM public.employee WHERE fio ILIKE %s)"
                    count_params.append(f"%{executorName}%")

                total = cur.execute(count_q, count_params).fetchone()["cnt"]
                rows  = cur.execute(query, params).fetchall()

        items = [ApplicationListItemOut.model_validate(r).model_dump() for r in rows]
        return {
            "items": items,
            "pagination": {"page": page, "pageSize": pageSize, "total": total},
        }

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/applications", status_code=201, tags=["Applications"], summary="Создать заявку",
          description="Создает заявку от имени текущего пользователя. Приоритет и статус рассчитывает backend. Вложения загружаются отдельным запросом, если frontend реально отправляет файлы.",
          response_model=IdResponse)
def create_application(
    payload: CreateApplicationPayload,
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = configData["MOCK_USERS_DB"].get(login, {}).get("employee_id")

        now = datetime.now(project_timezone)

        # Resolve status "new" → status_id
        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                status_row = cur.execute(
                    "SELECT status_id FROM public.status WHERE name = 'new' LIMIT 1"
                ).fetchone()
                if not status_row:
                    raise HTTPException(status_code=500, detail="Status 'new' not seeded")

                # Calculate initial priority (placeholder: lowest priority_id)
                priority_row = cur.execute(
                    "SELECT priority_id FROM public.priority ORDER BY priority_id ASC LIMIT 1"
                ).fetchone()
                priority_id = priority_row["priority_id"] if priority_row else None

                app_id = cur.execute(
                    """
                    INSERT INTO public.application
                        (name, priority_id, status_id, description, department_id,
                         types_of_works, is_unfinished, is_expired, deadline,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, false, false, %s, %s, %s)
                    RETURNING application_id
                    """,
                    (
                        payload.name, priority_id, status_row["status_id"],
                        payload.description, int(payload.departmentId),
                        int(payload.workTypeId),
                        payload.deadlineAt, now, now,
                    )
                ).fetchone()["application_id"]

                # Link author
                author_role = cur.execute(
                    "SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1"
                ).fetchone()
                if author_role and emp_id:
                    cur.execute(
                        "INSERT INTO public.employee_to_application (role_id, application_id, employee_id) VALUES (%s, %s, %s)",
                        (author_role["role_id"], app_id, emp_id)
                    )

        return {"id": str(app_id)}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/applications/{applicationId}", tags=["Applications"], summary="Получить карточку заявки",
         response_model=ApplicationDetailResponse)
def get_application(
    applicationId: int = Path(...),
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_role = _get_user_role(login)

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                row = cur.execute(
                    """
                    SELECT
                        a.*,
                        s.name  AS status_name,
                        p.name  AS priority_name,
                        author_link.employee_id AS author_id,
                        exec_link.employee_id   AS executor_id
                    FROM public.application a
                    LEFT JOIN public.status   s ON s.status_id   = a.status_id
                    LEFT JOIN public.priority p ON p.priority_id = a.priority_id
                    LEFT JOIN public.employee_to_application author_link
                           ON author_link.application_id = a.application_id
                          AND author_link.role_id = (SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1)
                    LEFT JOIN public.employee_to_application exec_link
                           ON exec_link.application_id = a.application_id
                          AND exec_link.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
                    WHERE a.application_id = %s
                    """,
                    (int(applicationId),)
                ).fetchone()

                row_or_404(row, "Application not found")

                # Attachments (photos)
                photos = cur.execute(
                    "SELECT * FROM public.photo WHERE application_id = %s",
                    (int(applicationId),)
                ).fetchall()

                # Delegation
                delegation = None
                if row.get("delegated_id"):
                    d = cur.execute(
                        "SELECT * FROM public.delegated WHERE delegated_id = %s",
                        (row["delegated_id"],)
                    ).fetchone()
                    if d:
                        d["application_id"] = applicationId
                        delegation = DelegationOut.model_validate(d).model_dump()

        row["availableActions"] = _available_actions(row, user_role)
        if S3_ENDPOINT_URL and S3_BUCKET:
            s3 = get_s3()
            row["attachments"] = [
                {
                    "id": str(p["photo_id"]),
                    "applicationId": str(p["application_id"]),
                    "name": p["name"],
                    "type": "photo",
                    "url": s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": S3_BUCKET, "Key": p["s3_key"]},
                        ExpiresIn=3600,
                    ),
                }
                for p in photos
            ]
        else:
            row["attachments"] = [
                {
                    "id": str(p["photo_id"]),
                    "applicationId": str(p["application_id"]),
                    "name": p["name"],
                    "type": "photo",
                    "url": None,
                }
                for p in photos
            ]
        row["delegation"] = delegation

        detail = ApplicationDetailOut.model_validate(row)
        return {"application": detail.model_dump()}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/applications/{applicationId}/actions", status_code=204, tags=["Applications"], summary="Выполнить действие над заявкой",
          description="Единая точка для действий из карточки заявки. Backend проверяет роль пользователя, текущий статус заявки и обязательность полей для конкретного action.",
          openapi_extra={
              "requestBody": {
                  "required": True,
                  "content": {
                      "application/json": {
                          "schema": {"$ref": "#/components/schemas/ApplicationActionPayload"},
                          "examples": {
                              "assignExecutor":   {"summary": "assignExecutor",   "value": {"action": "assignExecutor",   "executorId": "2",        "comment": "Назначено вручную руководителем."}},
                              "delegateExternal": {"summary": "delegateExternal", "value": {"action": "delegateExternal", "departmentId": "oge",     "comment": "Работы относятся к ОГЭ."}},
                              "complete":         {"summary": "complete",         "value": {"action": "complete",         "resultText": "Работы выполнены, доступ проверен."}},
                              "changeWorkType":   {"summary": "changeWorkType",   "value": {"action": "changeWorkType",   "workTypeId": "3"}},
                          },
                      }
                  },
              }
          })
def application_action(
    applicationId: int = Path(...),
    payload: ApplicationActionPayload = ...,
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_role = _get_user_role(login)
        emp_id = configData["MOCK_USERS_DB"].get(login, {}).get("employee_id")
        now = datetime.now(project_timezone)

        if payload.action not in ActionValues:
            raise HTTPException(status_code=400, detail=f"Unknown action: {payload.action}")

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                app_row = cur.execute(
                    """
                    SELECT a.*, s.name AS status_name
                    FROM public.application a
                    LEFT JOIN public.status s ON s.status_id = a.status_id
                    WHERE a.application_id = %s
                    """,
                    (int(applicationId),)
                ).fetchone()
                row_or_404(app_row, "Application not found")

                available = _available_actions(app_row, user_role)
                if payload.action not in available:
                    raise HTTPException(status_code=403, detail="Action not permitted in current state")

                def set_status(name: str):
                    st = cur.execute(
                        "SELECT status_id FROM public.status WHERE name = %s LIMIT 1", (name,)
                    ).fetchone()
                    if not st:
                        raise HTTPException(status_code=500, detail=f"Status '{name}' not seeded")
                    cur.execute(
                        "UPDATE public.application SET status_id = %s, updated_at = %s WHERE application_id = %s",
                        (st["status_id"], now, int(applicationId))
                    )

                action = payload.action

                if action == "assignExecutor":
                    if not payload.executorId:
                        raise HTTPException(status_code=400, detail="executorId required")
                    # Cancel active delegation if the application is currently delegated
                    if app_row.get("delegated_id"):
                        cur.execute(
                            "UPDATE public.delegated SET decision = 'declined', decided_at = %s WHERE delegated_id = %s",
                            (now, app_row["delegated_id"])
                        )
                        cur.execute(
                            "UPDATE public.application SET delegated_id = NULL, updated_at = %s WHERE application_id = %s",
                            (now, int(applicationId))
                        )
                    set_status("assigned")
                    cur.execute(
                        "UPDATE public.application SET executor_at = %s, updated_at = %s WHERE application_id = %s",
                        (now, now, int(applicationId))
                    )
                    exec_role = cur.execute(
                        "SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1"
                    ).fetchone()
                    if exec_role:
                        # Remove existing executor link if any
                        cur.execute(
                            "DELETE FROM public.employee_to_application WHERE application_id = %s AND role_id = %s",
                            (int(applicationId), exec_role["role_id"])
                        )
                        cur.execute(
                            "INSERT INTO public.employee_to_application (role_id, application_id, employee_id) VALUES (%s, %s, %s)",
                            (exec_role["role_id"], int(applicationId), int(payload.executorId))
                        )

                elif action == "startWork":
                    set_status("inProgress")
                    cur.execute(
                        "UPDATE public.application SET work_at = %s, updated_at = %s WHERE application_id = %s",
                        (now, now, int(applicationId))
                    )

                elif action == "complete":
                    if not payload.resultText:
                        raise HTTPException(status_code=400, detail="resultText required")
                    set_status("completed")
                    cur.execute(
                        "UPDATE public.application SET result_text = %s, finished_at = %s, updated_at = %s WHERE application_id = %s",
                        (payload.resultText, now, now, int(applicationId))
                    )

                elif action == "reject":
                    if user_role == "executor":
                        # Executor "reject" → returnToNew with isUnfinished flag
                        set_status("new")
                        cur.execute(
                            "UPDATE public.application SET is_unfinished = true, updated_at = %s WHERE application_id = %s",
                            (now, int(applicationId))
                        )
                    else:
                        set_status("rejected")
                        cur.execute(
                            "UPDATE public.application SET finished_at = %s, updated_at = %s WHERE application_id = %s",
                            (now, now, int(applicationId))
                        )

                elif action == "returnToNew":
                    set_status("new")
                    cur.execute(
                        "UPDATE public.application SET updated_at = %s WHERE application_id = %s",
                        (now, int(applicationId))
                    )

                elif action == "delegateExternal":
                    if not payload.departmentId:
                        raise HTTPException(status_code=400, detail="departmentId required")
                    set_status("delegated")
                    delegated_id = cur.execute(
                        """
                        INSERT INTO public.delegated (delegated_by, delegated_from, delegated_to, comment, created_at)
                        VALUES (%s, %s, %s, %s, %s) RETURNING delegated_id
                        """,
                        (
                            str(app_row["department_id"]),
                            str(app_row["department_id"]),
                            payload.departmentId,
                            payload.comment or "",
                            now,
                        )
                    ).fetchone()["delegated_id"]
                    cur.execute(
                        "UPDATE public.application SET delegated_id = %s, updated_at = %s WHERE application_id = %s",
                        (delegated_id, now, int(applicationId))
                    )

                elif action == "confirmExternalDelegation":
                    if app_row.get("delegated_id"):
                        cur.execute(
                            "UPDATE public.delegated SET decision = 'confirmed' WHERE delegated_id = %s",
                            (app_row["delegated_id"],)
                        )
                    set_status("new")
                    cur.execute(
                        "UPDATE public.application SET updated_at = %s WHERE application_id = %s",
                        (now, int(applicationId))
                    )

                elif action == "declineExternalDelegation":
                    if app_row.get("delegated_id"):
                        cur.execute(
                            "UPDATE public.delegated SET decision = 'declined' WHERE delegated_id = %s",
                            (app_row["delegated_id"],)
                        )
                    set_status("new")
                    cur.execute(
                        "UPDATE public.application SET delegated_id = NULL, updated_at = %s WHERE application_id = %s",
                        (now, int(applicationId))
                    )

                elif action == "changeWorkType":
                    if not payload.workTypeId:
                        raise HTTPException(status_code=400, detail="workTypeId required")
                    cur.execute(
                        "UPDATE public.application SET types_of_works = %s, updated_at = %s WHERE application_id = %s",
                        (int(payload.workTypeId), now, int(applicationId))
                    )

                elif action == "editDescription":
                    if not payload.description:
                        raise HTTPException(status_code=400, detail="description required")
                    cur.execute(
                        "UPDATE public.application SET description = %s, updated_at = %s WHERE application_id = %s",
                        (payload.description, now, int(applicationId))
                    )

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/applications/{applicationId}/attachments", status_code=201, tags=["Applications"], summary="Загрузить вложения к заявке",
          description="Frontend отправляет файлы на backend через multipart/form-data. Backend проверяет права, загружает файлы в S3 и сохраняет метаданные вложений в БД. Frontend не работает с S3 напрямую.",
          response_model=AttachmentUploadResponse)
async def upload_attachments(
    applicationId: int = Path(...),
    files: list[UploadFile] = File(...),
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        db = get_db_user(userData)
        ids = []

        # Verify application exists
        rows = db.getRowFromTable("application", "application_id", int(applicationId))
        row_or_404(rows, "Application not found")

        s3 = get_s3()
        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                for f in files:
                    content = await f.read()
                    filename     = f.filename or "upload"
                    content_type = f.content_type or "application/octet-stream"
                    s3_key = f"applications/{applicationId}/{uuid.uuid4()}-{filename}"

                    s3.put_object(
                        Bucket=S3_BUCKET,
                        Key=s3_key,
                        Body=content,
                        ContentType=content_type,
                    )

                    photo_id = cur.execute(
                        """INSERT INTO public.photo (s3_key, name, content_type, size_bytes, application_id)
                           VALUES (%s, %s, %s, %s, %s) RETURNING photo_id""",
                        (s3_key, filename, content_type, len(content), int(applicationId))
                    ).fetchone()["photo_id"]
                    ids.append({"id": str(photo_id)})

        return {"items": ids}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


# ─── Directories ────────────────────────────────────────────────

@app.get("/departments", tags=["Directories"], summary="Получить отделы", response_model=DepartmentListResponse)
def get_departments(userData=Depends(authObj.authenticate_user_test)):
    try:
        db = get_db_user(userData)
        rows = db.getAllRowsFromTable("department")
        items = [DepartmentOut.model_validate(r).model_dump() for r in (rows or [])]
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/employees", tags=["Directories"], summary="Получить сотрудников, подключенных к системе", response_model=EmployeeListResponse)
def get_employees(
    userData=Depends(authObj.authenticate_user_test),
    departmentId: Optional[str] = Query(default=None),
    isActive: Optional[bool]    = Query(default=None),
    role: Optional[str]         = Query(default=None),
):
    try:
        db = get_db_user(userData)
        login = userData[0]

        # Build employee list from mock config (real system would query AD-linked table)
        result = []
        for uname, ucfg in configData["MOCK_USERS_DB"].items():
            emp_id = ucfg.get("employee_id")
            if emp_id is None:
                continue
            rows = db.getRowFromTable("employee", "employee_id", emp_id)
            if not rows:
                continue
            row = rows[0]

            if departmentId and str(row.get("department_id")) != departmentId:
                continue

            is_active = row.get("is_active", True)
            if isActive is not None and is_active != isActive:
                continue

            user_roles = ucfg.get("roles", ["author"])
            if role and role not in user_roles:
                continue

            pg_rows   = db.getRowFromTable("post_grade", "post_grade_id", row.get("post_grade_id"))
            post_name = ""
            if pg_rows:
                post_rows = db.getRowFromTable("post", "post_id", pg_rows[0]["post_post_id"])
                post_name = post_rows[0]["name"] if post_rows else ""

            result.append({
                "id":           str(emp_id),
                "login":        uname,
                "fullName":     row.get("fio", ""),
                "roles":        user_roles,
                "departmentId": str(row.get("department_id", "")),
                "postName":     post_name,
                "positionId":   str(row.get("post_grade_id", "")),
                "isActive":     is_active,
            })

        return {"items": result}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/employees", status_code=201, tags=["Directories"], summary="Добавить AD-пользователя в систему как исполнителя",
          description="Не создает человека в AD. Backend создает локальную запись участия в системе для уже существующего AD-пользователя. Роль нового сотрудника — executor.",
          response_model=IdResponse)
def add_employee(
    payload: CreateEmployeePayload,
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        require_permission(userData, "canManageEmployees")
        db = get_db_user(userData)
        now = datetime.now(project_timezone)

        # adUserId is treated as employee_id for existing AD users in this mock
        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                emp_id = cur.execute(
                    """
                    INSERT INTO public.employee (employee_id, post_grade_id, fio, created_at, updated_at, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (employee_id) DO UPDATE SET post_grade_id = EXCLUDED.post_grade_id, updated_at = EXCLUDED.updated_at, is_active = EXCLUDED.is_active
                    RETURNING employee_id
                    """,
                    (int(payload.adUserId), int(payload.positionId), "AD User", now, now, payload.isActive)
                ).fetchone()["employee_id"]

        return {"id": str(emp_id)}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.patch("/employees/{employeeId}", status_code=204, tags=["Directories"], summary="Изменить позицию сотрудника или участие в распределении")
def update_employee(
    payload: UpdateEmployeePayload,
    employeeId: int = Path(...),
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        require_permission(userData, "canManageEmployees")
        db = get_db_user(userData)
        now = datetime.now(project_timezone)

        rows = db.getRowFromTable("employee", "employee_id", int(employeeId))
        row_or_404(rows, "Employee not found")

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                if payload.positionId is not None:
                    cur.execute(
                        "UPDATE public.employee SET post_grade_id = %s, updated_at = %s WHERE employee_id = %s",
                        (int(payload.positionId), now, int(employeeId))
                    )
                if payload.isActive is not None:
                    cur.execute(
                        "UPDATE public.employee SET is_active = %s, updated_at = %s WHERE employee_id = %s",
                        (payload.isActive, now, int(employeeId))
                    )

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/positions", tags=["Directories"], summary="Получить позиции",
         description="Позиции — это грейды из post_grade, которые руководитель назначает сотрудникам.",
         response_model=PositionListResponse)
def get_positions(userData=Depends(authObj.authenticate_user_test)):
    try:
        db = get_db_user(userData)
        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    """
                    SELECT pg.post_grade_id,
                           p.name || ' / ' || g.name AS pg_name
                    FROM public.post_grade pg
                    LEFT JOIN public.post  p ON p.post_id   = pg.post_post_id
                    LEFT JOIN public.grade g ON g.grade_id  = pg.grade_grade_id
                    ORDER BY pg.post_grade_id
                    """
                ).fetchall()

        items = [PositionOut.model_validate(r).model_dump() for r in rows]
        return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/ad/users", tags=["Directories"], summary="Найти пользователей AD для добавления в систему", response_model=AdUserListResponse)
def get_ad_users(
    userData=Depends(authObj.authenticate_user_test),
    query: Optional[str]        = Query(default=None),
    departmentId: Optional[str] = Query(default=None),
):
    """Returns mock AD users that can be added to the system."""
    try:
        db = get_db_user(userData)
        result = []
        for ad_id, ucfg in configData.get("MOCK_AD_USERS", {}).items():
            if query and query.lower() not in ucfg.get("fio", "").lower():
                continue
            if departmentId and str(ucfg.get("department_id", "")) != departmentId:
                continue
            result.append({
                "adUserId":    str(ad_id),
                "login":       ucfg.get("login", ""),
                "fullName":    ucfg.get("fio", ""),
                "departmentId":str(ucfg.get("department_id", "")),
                "postName":    ucfg.get("post_name", ""),
            })
        return {"items": result}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/work-types", tags=["Directories"], summary="Получить виды работ", response_model=WorkTypeListResponse)
def get_work_types_all(
    userData=Depends(authObj.authenticate_user_test),
    departmentId: Optional[str] = Query(default=None),
):
    try:
        db = get_db_user(userData)
        join_str = """
            LEFT JOIN (
                SELECT
                    type_of_works_id AS sub_id,
                    COALESCE(json_agg(post_grade_id) FILTER (WHERE post_grade_id IS NOT NULL), '[]'::json) AS post_grade_ids
                FROM public.type_of_work_to_post_grade
                GROUP BY type_of_works_id
            ) tow_pg ON types_of_works.type_of_works_id = tow_pg.sub_id
        """
        if departmentId:
            data = db.getRowsFromTableWithJoin("types_of_works", join_str, "department_id", departmentId)
        else:
            data = db.getAllRowsFromTableWithJoin("types_of_works", join_str)

        if not data:
            return {"items": []}

        from pydantic import RootModel
        class WTList(RootModel[list[WorkTypeOut]]):
            pass

        items = WTList.model_validate(data).model_dump()
        return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/work-types", status_code=201, tags=["Directories"], summary="Создать вид работ", response_model=IdResponse)
def create_work_type(
    payload: CreateWorkTypePayload,
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        require_permission(userData, "canManageWorkTypes")
        dep = DBController.getRowFromTable("department", "department_id", int(payload.departmentId))
        if not dep:
            raise HTTPException(status_code=400, detail="Department not found")
        data = DBController.tryWriteNewTypeOfWork(
            payload.name,
            payload.departmentId,
            ComplexityValues.index(payload.complexity),
        )
        if not data or isinstance(data, str):
            raise HTTPException(status_code=500, detail="Failed to create work type")
        return {"id": str(data[0][0])}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.delete("/work-types/{workTypeId}", status_code=204, tags=["Directories"], summary="Удалить вид работ")
def delete_work_type(
    workTypeId: int = Path(...),
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        require_permission(userData, "canManageWorkTypes")
        db = get_db_user(userData)

        rows = db.getRowFromTable("types_of_works", "type_of_works_id", int(workTypeId))
        row_or_404(rows, "Work type not found")

        # Check if any application references this work type (conflict)
        apps = db.getRowFromTable("application", "types_of_works", int(workTypeId))
        if apps:
            raise HTTPException(status_code=409, detail="Work type is referenced by existing applications")

        db.deleteDataFromTable("types_of_works", f"type_of_works_id = {int(workTypeId)}")
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


# ─── Priority settings ───────────────────────────────────────────

# In-memory store (replace with a DB table in production)
_priority_settings: dict = {
    "department":    0.2,
    "position":      0.2,
    "workType":      0.2,
    "deadline":      0.2,
    "managerAuthor": 0.2,
}

@app.get("/priority-settings", tags=["Priority"], summary="Получить коэффициенты расчета приоритета", response_model=PrioritySettingsModel)
def get_priority_settings(userData=Depends(authObj.authenticate_user_test)):
    try:
        require_permission(userData, "canManagePrioritySettings")
        return _priority_settings
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.put("/priority-settings", tags=["Priority"], summary="Сохранить коэффициенты расчета приоритета", response_model=PrioritySettingsModel)
def update_priority_settings(
    payload: PrioritySettingsModel,
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        require_permission(userData, "canManagePrioritySettings")
        _priority_settings.update(payload.model_dump())
        return _priority_settings
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


# ─── Notifications ───────────────────────────────────────────────

@app.get("/notifications", tags=["Notifications"], summary="Получить уведомления текущего пользователя",
         description="Текущий контракт рассчитан на pull-модель: backend создает уведомления при событиях системы, frontend периодически запрашивает список или обновляет его после действий пользователя.",
         response_model=NotificationsResponse)
def get_notifications(
    userData=Depends(authObj.authenticate_user_test),
    unreadOnly: bool = Query(default=False),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = configData["MOCK_USERS_DB"].get(login, {}).get("employee_id")

        if emp_id is None:
            return {"items": [], "unreadCount": 0}

        where = f"employee_id = {emp_id}"
        if unreadOnly:
            where += " AND is_read = false"

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    f"SELECT * FROM public.notification WHERE {where} ORDER BY created_at DESC"
                ).fetchall()
                unread_count = cur.execute(
                    "SELECT COUNT(*) AS cnt FROM public.notification WHERE employee_id = %s AND is_read = false",
                    (emp_id,)
                ).fetchone()["cnt"]

        items = [NotificationOut.model_validate(r).model_dump() for r in rows]
        return {"items": items, "unreadCount": unread_count}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/notifications/{notificationId}/read", status_code=204, tags=["Notifications"], summary="Отметить уведомление прочитанным")
def mark_notification_read(
    notificationId: int = Path(...),
    userData=Depends(authObj.authenticate_user_test),
):
    try:
        db = get_db_user(userData)
        rows = db.getRowFromTable("notification", "notification_id", int(notificationId))
        row_or_404(rows, "Notification not found")

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.notification SET is_read = true WHERE notification_id = %s",
                    (int(notificationId),)
                )
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/notifications/read-all", status_code=204, tags=["Notifications"], summary="Отметить все уведомления текущего пользователя прочитанными")
def mark_all_notifications_read(userData=Depends(authObj.authenticate_user_test)):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = configData["MOCK_USERS_DB"].get(login, {}).get("employee_id")

        if emp_id is not None:
            with db.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE public.notification SET is_read = true WHERE employee_id = %s",
                        (emp_id,)
                    )
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


# ─── Reports ────────────────────────────────────────────────────

def _build_report_query(
    createdFrom=None, createdTo=None,
    finishedFrom=None, finishedTo=None,
    status_filter=None, executorId=None,
):
    base = """
        SELECT
            a.application_id,
            a.name,
            a.created_at,
            a.work_at,
            a.finished_at,
            s.name  AS status_name,
            p.name  AS priority_name,
            e.fio   AS executor_name,
            exec_link.employee_id AS executor_id,
            d.name  AS department_name,
            tw.name AS work_type_name
        FROM public.application a
        LEFT JOIN public.status   s  ON s.status_id   = a.status_id
        LEFT JOIN public.priority p  ON p.priority_id = a.priority_id
        LEFT JOIN public.department d ON d.department_id = a.department_id
        LEFT JOIN public.types_of_works tw ON tw.type_of_works_id = a.types_of_works
        LEFT JOIN public.employee_to_application exec_link
               ON exec_link.application_id = a.application_id
              AND exec_link.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
        LEFT JOIN public.employee e ON e.employee_id = exec_link.employee_id
        WHERE 1=1
    """
    params = []
    if createdFrom:
        base += " AND a.created_at >= %s"; params.append(createdFrom)
    if createdTo:
        base += " AND a.created_at <= %s"; params.append(createdTo)
    if finishedFrom:
        base += " AND a.finished_at >= %s"; params.append(finishedFrom)
    if finishedTo:
        base += " AND a.finished_at <= %s"; params.append(finishedTo)
    if status_filter:
        base += " AND s.name = %s"; params.append(status_filter)
    if executorId:
        base += " AND exec_link.employee_id = %s"; params.append(int(executorId))
    base += " ORDER BY a.created_at DESC"
    return base, params


@app.get("/reports/applications", tags=["Reports"], summary="Сформировать предварительный отчет по заявкам",
         description="Возвращает JSON-данные для предпросмотра отчета. Фильтры передаются query-параметрами, потому что формирование отчета не меняет состояние backend.",
         response_model=ApplicationReportResponse)
def report_applications(
    userData=Depends(authObj.authenticate_user_test),
    createdFrom:  Optional[str] = Query(default=None),
    createdTo:    Optional[str] = Query(default=None),
    finishedFrom: Optional[str] = Query(default=None),
    finishedTo:   Optional[str] = Query(default=None),
    status_filter:Optional[str] = Query(default=None, alias="status"),
    executorId:   Optional[str] = Query(default=None),
):
    try:
        require_permission(userData, "canViewReports")
        db = get_db_user(userData)

        query, params = _build_report_query(
            createdFrom, createdTo, finishedFrom, finishedTo, status_filter, executorId
        )

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(query, params).fetchall()

        items = [ApplicationReportRowOut.model_validate(r).model_dump() for r in rows]
        total     = len(items)
        completed = sum(1 for i in items if i.get("status") == "completed")
        in_prog   = sum(1 for i in items if i.get("status") in ("inProgress", "assigned"))

        return {
            "items": items,
            "summary": {
                "total": total,
                "completed": completed,
                "inProgressOrAssigned": in_prog,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/reports/applications.xls", tags=["Reports"], summary="Скачать XLS-отчет по заявкам",
         description="Возвращает готовый XLS-файл по тем же фильтрам, что и предпросмотр отчета. Генерация файла выполняется на backend.")
def report_applications_xls(
    userData=Depends(authObj.authenticate_user_test),
    createdFrom:  Optional[str] = Query(default=None),
    createdTo:    Optional[str] = Query(default=None),
    finishedFrom: Optional[str] = Query(default=None),
    finishedTo:   Optional[str] = Query(default=None),
    status_filter:Optional[str] = Query(default=None, alias="status"),
    executorId:   Optional[str] = Query(default=None),
):
    try:
        require_permission(userData, "canViewReports")
        db = get_db_user(userData)

        query, params = _build_report_query(
            createdFrom, createdTo, finishedFrom, finishedTo, status_filter, executorId
        )

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(query, params).fetchall()

        import io
        try:
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Applications"
            headers = [
                "ID", "Название", "Статус", "Приоритет",
                "Создана", "Начата", "Завершена",
                "Исполнитель", "Отдел", "Вид работ",
            ]
            ws.append(headers)
            for r in rows:
                def _s(v):
                    if isinstance(v, datetime):
                        return v.isoformat()
                    return str(v) if v is not None else ""

                ws.append([
                    _s(r.get("application_id")),
                    _s(r.get("name")),
                    _s(r.get("status_name")),
                    _s(r.get("priority_name")),
                    _s(r.get("created_at")),
                    _s(r.get("work_at")),
                    _s(r.get("finished_at")),
                    _s(r.get("executor_name")),
                    _s(r.get("department_name")),
                    _s(r.get("work_type_name")),
                ])
            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            xls_bytes = buf.read()
        except ImportError:
            # Fallback: CSV as plain text if openpyxl not installed
            import csv, io as sio
            out = sio.StringIO()
            writer = csv.writer(out)
            writer.writerow(["ID", "Name", "Status", "Priority",
                             "Created", "Started", "Finished",
                             "Executor", "Department", "WorkType"])
            for r in rows:
                writer.writerow([
                    r.get("application_id"), r.get("name"),
                    r.get("status_name"), r.get("priority_name"),
                    r.get("created_at"), r.get("work_at"), r.get("finished_at"),
                    r.get("executor_name"), r.get("department_name"), r.get("work_type_name"),
                ])
            xls_bytes = out.getvalue().encode("utf-8-sig")

        return Response(
            content=xls_bytes,
            media_type="application/vnd.ms-excel",
            headers={"Content-Disposition": "attachment; filename=applications.xls"},
        )

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)
