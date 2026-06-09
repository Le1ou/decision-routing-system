from src.application_module import (
    PgDbOperator, ActiveDirectoryAuth, configData, project_timezone
)
from fastapi import FastAPI, Depends, HTTPException, status, Query, UploadFile, File, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator, BeforeValidator, RootModel
from pydantic import model_validator
from typing import Annotated, Literal, Optional
from datetime import datetime, timedelta
import uuid
import os
import threading
import boto3
from botocore.config import Config
import psycopg
from src.seed import seed_database, seed_demo_notifications
from src import priority_settings_store as ps_store
from src import analytics_module as analytics
from src import events_module as events
import asyncio
from contextlib import asynccontextmanager

S3_BUCKET       = os.environ.get("S3_BUCKET_NAME", "")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL")
# Endpoint used to SIGN URLs handed to the browser. The backend reaches MinIO at
# the internal docker host (minio:9000), but the browser can't resolve that — so
# presigned links must be signed against a host the browser can reach
# (e.g. http://localhost:9000). Falls back to S3_ENDPOINT_URL when not set, which
# is correct for a real cloud S3 whose endpoint is public anyway.
S3_PUBLIC_ENDPOINT_URL = os.environ.get("S3_PUBLIC_ENDPOINT_URL") or S3_ENDPOINT_URL
S3_REGION       = os.environ.get("S3_REGION", "auto")
# Path-style addressing (http://endpoint/bucket/key) instead of virtual-hosted
# (http://bucket.endpoint/key). Required by self-hosted S3 like MinIO; harmless
# for providers that support both. Off by default so existing setups are unchanged.
S3_FORCE_PATH_STYLE = os.environ.get("S3_FORCE_PATH_STYLE", "").strip().lower() in ("1", "true", "yes", "on")
_s3_client: boto3.client = None
_s3_public_client: boto3.client = None


def _make_s3(endpoint_url):
    cfg = Config(s3={"addressing_style": "path"}) if S3_FORCE_PATH_STYLE else None
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
        region_name=S3_REGION,
        config=cfg,
    )


def get_s3():
    """Client for server-side operations (upload) — uses the internal endpoint."""
    global _s3_client
    if _s3_client is None:
        _s3_client = _make_s3(S3_ENDPOINT_URL)
    return _s3_client


def get_s3_public():
    """Client used only to SIGN presigned URLs for the browser — uses the public
    endpoint so the resulting links are reachable from outside the docker network."""
    global _s3_public_client
    if _s3_public_client is None:
        _s3_public_client = _make_s3(S3_PUBLIC_ENDPOINT_URL)
    return _s3_public_client

# ──────────────────── Mock Active Directory ────────────────────
# configData["MOCK_AD"] is ONE directory keyed by login. Each entry is an AD
# person with identity fields (adUserId, fullName, departmentId, position). The
# "inSystem" flag marks who has been onboarded into the routing system; only
# those entries carry the system-specific fields (password, employee_id, role).
#
# A user's AD role is cumulative along ROLE_LADDER:
#   author ⊂ executor ⊂ manager ⊂ top-manager
# Everyone is an author; an executor is also an author; a manager is also an
# executor and author; a top-manager is everything. The Postgres-only technical
# permissions (canManage…) are DERIVED from the AD role, never stored in AD.

# Postgres group roles that bundle table privileges (see setupRoleTableGrants()).
PG_TABLE_BASE   = "app_table_base"     # every user: read all + write own applications
PG_TABLE_MANAGE = "app_table_manage"   # managers & top-managers: manage directories


def _ad_directory() -> dict:
    return configData.get("MOCK_AD", {})


def _system_users() -> dict:
    """AD entries that have been onboarded into the system (can authenticate)."""
    return {login: e for login, e in _ad_directory().items() if e.get("inSystem")}


def _user_cfg(login: str) -> dict:
    return _ad_directory().get(login, {})


def _employee_id(login: str):
    """The DB employee_id for an onboarded user, or None."""
    eid = _user_cfg(login).get("employee_id")
    return int(eid) if eid is not None else None


def _find_ad_by_id(ad_user_id: str):
    """Return (login, entry) for an AD person by adUserId, or (None, None)."""
    for login, entry in _ad_directory().items():
        if str(entry.get("adUserId")) == str(ad_user_id):
            return login, entry
    return None, None


def _base_role(login: str) -> str:
    """The user's single, highest AD role (author/executor/manager/top-manager)."""
    return _user_cfg(login).get("role", "author")


def _role_ladder() -> list:
    return configData.get("ROLE_LADDER", ["author", "executor", "manager", "top-manager"])


def _expand_roles(role: str) -> list:
    """Expand a cumulative AD role into the full list of roles it implies."""
    ladder = _role_ladder()
    return ladder[: ladder.index(role) + 1] if role in ladder else ["author"]


def _permissions_for_role(role: str) -> list:
    return configData.get("ROLE_PERMISSIONS", {}).get(role, [])


def _pg_roles_for(login: str) -> list:
    """Translate a user's AD role into the Postgres roles they should be granted."""
    role = _base_role(login)
    pg_roles = [PG_TABLE_BASE]
    if role in ("manager", "top-manager"):
        pg_roles.append(PG_TABLE_MANAGE)
    pg_roles += _permissions_for_role(role)
    return pg_roles


# ─────────────────────────── App bootstrap ───────────────────────────

DBController = PgDbOperator("postgres", "postgres")
with DBController.pool.connection() as _conn:
    # Idempotent migrations so an already-created DB picks up the new contract columns
    # and tables BEFORE role grants and seed_database() (which reference them) run.
    _conn.execute("ALTER TABLE public.employee ADD COLUMN IF NOT EXISTS is_active boolean")
    _conn.execute("ALTER TABLE public.employee ADD COLUMN IF NOT EXISTS role_id integer")
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS archived_at timestamp with time zone")
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS executor_comment text")
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS manager_comment text")
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS previous_executor_id integer")
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS closed_by_id integer")
    _conn.execute("ALTER TABLE public.delegated ADD COLUMN IF NOT EXISTS delegated_by_employee integer")
    _conn.execute("ALTER TABLE public.delegated ADD COLUMN IF NOT EXISTS decision text")
    _conn.execute("ALTER TABLE public.delegated ADD COLUMN IF NOT EXISTS decided_at timestamp with time zone")
    _conn.execute("ALTER TABLE public.delegated ADD COLUMN IF NOT EXISTS application_id integer")
    _conn.execute("ALTER TABLE public.notification ADD COLUMN IF NOT EXISTS employee_id integer")
    _conn.execute("ALTER TABLE public.notification ADD COLUMN IF NOT EXISTS is_read boolean")
    _conn.execute("ALTER TABLE public.notification ADD COLUMN IF NOT EXISTS application_id integer")
    # Photo metadata for S3-backed attachments (older DBs only had value/application_id).
    _conn.execute("ALTER TABLE public.photo ADD COLUMN IF NOT EXISTS s3_key character varying(1000)")
    _conn.execute("ALTER TABLE public.photo ADD COLUMN IF NOT EXISTS name character varying(500)")
    _conn.execute("ALTER TABLE public.photo ADD COLUMN IF NOT EXISTS content_type character varying(100)")
    _conn.execute("ALTER TABLE public.photo ADD COLUMN IF NOT EXISTS size_bytes integer")
    _conn.execute("ALTER TABLE public.photo ADD COLUMN IF NOT EXISTS uploaded_at timestamp with time zone DEFAULT NOW()")
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS public.type_of_work_to_grade (
            id integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 ),
            type_of_works_id integer,
            grade_id integer,
            PRIMARY KEY (id)
        )
    """)
    # Continuous priority score (П from the formula). priority_id stays as the
    # derived display bucket; routing/recompute will populate this column.
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS priority_score real")
    # Dedup flag for the events subsystem: set once a deadline-approaching
    # notification has been sent for a `new` application (so it isn't re-sent each tick).
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS deadline_notified boolean")
    # Status-transition journal — written by the management subsystem on every
    # status change; the analytics subsystem reads it.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS public.application_status_history (
            id              integer NOT NULL GENERATED ALWAYS AS IDENTITY ( INCREMENT 1 START 1 MINVALUE 1 ),
            application_id  integer,
            from_status_id  integer,
            to_status_id    integer,
            changed_at      timestamp with time zone NOT NULL,
            by_employee_id  integer,
            reason          text,
            PRIMARY KEY (id)
        )
    """)
    # Persistent priority-calculation settings (was an in-memory dict). Single row id=1.
    _conn.execute("""
        CREATE TABLE IF NOT EXISTS public.priority_settings (
            id             integer NOT NULL,
            department     jsonb NOT NULL DEFAULT '{}'::jsonb,
            manager_author jsonb NOT NULL DEFAULT '{}'::jsonb,
            deadline       real  NOT NULL DEFAULT 0.2,
            PRIMARY KEY (id)
        )
    """)
# Create the Postgres group roles that back the application's role model and the
# technical permission marker roles. Both are derived from the AD role, not stored.
DBController.setupRoleTableGrants()
DBController.fillPermissionRoles(configData["PERMISSIONS"])
authObj = ActiveDirectoryAuth()
seed_database(DBController)
# In mock mode, drop in a few demo applications whose deadlines trigger the events
# subsystem (overdue / deadline-approaching notifications) for the IT manager so the
# behaviour is visible right after the project starts. Not seeded in AD mode.
if authObj.mode == "mock":
    seed_demo_notifications(DBController)
# Pre-create a Postgres login role for every onboarded user that has a known
# password. In mock mode that's everyone; in AD mode (where AD owns the password)
# entries may have none — those roles are created lazily on first login instead.
for _username, _ucfg in _system_users().items():
    if _ucfg.get("password"):
        DBController.createUserRole(_username, _ucfg["password"], _pg_roles_for(_username))
# ─────────────────────────── Events subsystem loop ───────────────────────────
# Background loop that drives the events subsystem (deadline notifications +
# overdue marking). Runs in-process via asyncio; the synchronous DB tick is
# offloaded to a thread so it never blocks the event loop. Configured via the
# "events" block in apps/backend/config.json:
#   "enabled"      — true/false (default true)
#   "tick_seconds" — interval between ticks (default 60)
# The first tick happens AFTER one interval, so the fast test suite never triggers it.
_events_cfg = configData.get("events", {}) or {}
_events_enabled = _events_cfg.get("enabled", True)
EVENTS_ENABLED = (_events_enabled if isinstance(_events_enabled, bool)
                  else str(_events_enabled).strip().lower() in ("1", "true", "yes", "on"))
try:
    EVENTS_TICK_SECONDS = max(1, int(_events_cfg.get("tick_seconds", 60)))
except (TypeError, ValueError):
    EVENTS_TICK_SECONDS = 60


@asynccontextmanager
async def lifespan(_app):
    task = None
    if EVENTS_ENABLED:
        async def _loop():
            while True:
                await asyncio.sleep(EVENTS_TICK_SECONDS)
                try:
                    result = await asyncio.to_thread(events.run_tick, DBController)
                    if result.get("expired") or result.get("deadlineNotifications"):
                        print(f"[events] tick: {result}")
                except Exception as e:
                    print(f"[events] tick error: {e}")
        task = asyncio.create_task(_loop())
        print(f"[events] background loop enabled (tick={EVENTS_TICK_SECONDS}s).")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(
    title="Decision Routing System API",
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Auth",          "description": "Текущий пользователь"},
        {"name": "Applications",  "description": "Производственные заявки"},
        {"name": "Directories",   "description": "Отделы, сотрудники, должности и виды работ"},
        {"name": "Priority",      "description": "Настройки расчета приоритета"},
        {"name": "Notifications", "description": "Уведомления текущего пользователя"},
        {"name": "Reports",       "description": "Отчеты и XLS-выгрузка"},
        {"name": "Analytics",     "description": "Статистика по заявкам, исполнителям, видам работ и отделам"},
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
RoleValues         = ["author", "executor", "manager", "top-manager"]
ActionValues       = [
    "editDescription", "assignExecutor", "startWork", "reject", "complete",
    "delegateInternal", "delegateExternal", "returnToNew", "cancel", "archive",
    "confirmExternalDelegation", "declineExternalDelegation", "changeWorkType",
]

# Number of days after which a rejected application disappears from the main UI.
REJECTED_VISIBLE_DAYS = 7

# ─────────────────────────── Helpers ─────────────────────────────────

# One connection pool per authenticated user, created lazily and reused across
# requests. Building a fresh PgDbOperator (and therefore a new pool) on every
# request leaked Postgres connections until they were exhausted.
_user_db_cache: dict = {}
_user_db_lock = threading.Lock()


def get_db_user(userData) -> PgDbOperator:
    """Return a cached per-user DB operator (one connection pool per login)."""
    login, password = userData[0], userData[1]
    db = _user_db_cache.get(login)
    if db is not None:
        return db
    with _user_db_lock:
        db = _user_db_cache.get(login)          # re-check inside the lock
        if db is None:
            # Ensure the Postgres login role exists before connecting as it.
            DBController.createUserRole(login, password, _pg_roles_for(login))
            db = PgDbOperator(login, password)
            _user_db_cache[login] = db
        return db


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


def require_manager_role(login: str):
    """Raise 403 unless the user is a manager or top-manager."""
    if _get_user_role(login) not in ("manager", "top-manager"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Manager role required")


def require_top_manager(login: str):
    """Raise 403 unless the user is a top-manager."""
    if not _is_top_manager(login):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Top-manager role required")


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
    """A job title (должность) coming from AD; maps to the `post` table."""
    id: CoercedStr   = Field(validation_alias="post_id")
    name: CoercedStr = Field(validation_alias="name")

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
    complexity: Literal["easy", "medium", "hard", "critical"] = Field(
        validation_alias="complexity_value"
    )
    allowedGradeIds: ListOfStrings = Field(validation_alias="grade_ids")

    @field_validator("complexity", mode="before")
    @classmethod
    def parse_complexity(cls, v):
        return complexity_int_to_str(v)

    model_config = {"populate_by_name": True}

class CreateWorkTypePayload(BaseModel):
    name: str = Field(min_length=1)
    departmentId: str
    complexity: Literal["easy", "medium", "hard", "critical"]
    allowedGradeIds: list[str] = Field(min_length=1)

class UpdateWorkTypePayload(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    departmentId: Optional[str] = None
    complexity: Optional[Literal["easy", "medium", "hard", "critical"]] = None
    allowedGradeIds: Optional[list[str]] = None

    @model_validator(mode="after")
    def at_least_one(self):
        if (self.name is None and self.departmentId is None
                and self.complexity is None and self.allowedGradeIds is None):
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

# ── Departments ──

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
    complexity: Optional[Literal["easy", "medium", "hard", "critical"]] = None
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
    def validate_coeffs(cls, v):
        for k, val in v.items():
            if val < 0 or val > 1:
                raise ValueError(f"Coefficient for '{k}' must be between 0 and 1")
        return v

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

# ─────────────────────────── Business logic helpers ──────────────────

def _available_actions(app_row: dict, user_role: str, *,
                       is_author: bool = False,
                       is_assigned_executor: bool = False,
                       manager_in_scope: bool = False) -> list[str]:
    """Derive available actions from status + the caller's *involvement*.

    Action tiers are unioned, not exclusive — the roles are cumulative, so a
    manager who happens to be the assigned executor of an application gets both
    the manager-tier and the executor-tier actions for it.

      - manager tier:  granted to a manager/top-manager acting within scope
                       (own department, the delegation target, or top-manager);
      - executor tier: granted to whoever is the assigned executor;
      - author tier:   granted to whoever authored the application.
    """
    status_name = app_row.get("status_name", "")
    is_archived = app_row.get("archived_at") is not None
    actions: set[str] = set()

    # ── Manager tier (in-scope managers / top-managers) ──
    if user_role in ("manager", "top-manager") and manager_in_scope:
        if status_name == "new":
            actions |= {"assignExecutor", "delegateExternal", "editDescription", "changeWorkType", "cancel"}
        elif status_name == "assigned":
            actions |= {"assignExecutor", "delegateExternal", "reject", "returnToNew"}
        elif status_name == "delegated":
            actions |= {"assignExecutor", "confirmExternalDelegation", "declineExternalDelegation"}
        elif status_name == "inProgress":
            actions |= {"assignExecutor", "reject", "returnToNew"}
        if not is_archived and status_name in ("completed", "rejected"):
            actions.add("archive")

    # ── Executor tier (the assigned executor, whatever their role) ──
    # External delegation only from `assigned` (§7.3); internal delegation up to
    # `inProgress` inclusive (§7.2).
    if is_assigned_executor:
        if status_name == "assigned":
            actions |= {"startWork", "reject", "delegateInternal", "delegateExternal"}
        elif status_name == "inProgress":
            actions |= {"complete", "reject", "delegateInternal"}

    # ── Author tier (the author, whatever their role) ──
    if is_author and status_name == "new":
        actions |= {"editDescription", "cancel"}

    return sorted(actions)


def _action_scope(db: PgDbOperator, login: str, app_row: dict, user_role: str) -> tuple[bool, bool, bool]:
    """Compute the caller's involvement with an application for action gating.

    Returns (is_author, is_assigned_executor, manager_in_scope). A manager is in
    scope for their own department, for an application delegated to their
    department, or always if they are a top-manager.
    """
    caller_emp = _employee_id(login)
    is_author = caller_emp is not None and app_row.get("author_id") == caller_emp
    is_exec   = caller_emp is not None and app_row.get("executor_id") == caller_emp

    manager_in_scope = False
    if user_role == "top-manager":
        manager_in_scope = True
    elif user_role == "manager":
        own = _user_department_id(db, login)
        if own is not None:
            deleg_to = app_row.get("delegated_to")
            manager_in_scope = (app_row.get("department_id") == own) or \
                               (deleg_to is not None and str(deleg_to) == str(own))
    return is_author, is_exec, manager_in_scope


def _effective_complexity_index(cur, app_row: dict) -> Optional[int]:
    """Current complexity of an application as an int index into ComplexityValues:
    the executor-assigned value if present, otherwise the work type's base complexity."""
    assigned = app_row.get("empl_assigned_complexity")
    if assigned is not None:
        return assigned
    tow_id = app_row.get("types_of_works")
    if tow_id is None:
        return None
    row = cur.execute(
        "SELECT complexity_value FROM public.types_of_works WHERE type_of_works_id = %s",
        (tow_id,)
    ).fetchone()
    return row["complexity_value"] if row else None


def _user_dict(cur, employee_id, login_by_emp: dict) -> Optional[dict]:
    """Build a contract `User` dict for an employee_id, or None. Shaped like UserOut."""
    if employee_id is None:
        return None
    row = cur.execute(
        """
        SELECT e.employee_id, e.department_id, e.fio, e.is_active,
               r.name AS role, po.post_id AS post_id, po.name AS post_name
        FROM public.employee e
        LEFT JOIN public.role r        ON r.role_id = e.role_id
        LEFT JOIN public.post_grade pg ON pg.post_grade_id = e.post_grade_id
        LEFT JOIN public.post po       ON po.post_id = pg.post_post_id
        WHERE e.employee_id = %s
        """,
        (int(employee_id),)
    ).fetchone()
    if not row:
        return None
    return {
        "id":           str(row["employee_id"]),
        "login":        login_by_emp.get(row["employee_id"], ""),
        "fullName":     row.get("fio") or "",
        "role":         row.get("role") or "author",
        "departmentId": str(row.get("department_id") or ""),
        "postName":     row.get("post_name") or "",
        "positionId":   str(row.get("post_id") or ""),
        "isActive":     row.get("is_active", True),
    }


def _resolve_employee_id(db: PgDbOperator, login: str) -> Optional[int]:
    """Return the employee_id for a given login from the mock config."""
    user_cfg = _user_cfg(login)
    emp_id = user_cfg.get("employee_id")
    return int(emp_id) if emp_id is not None else None


def _get_user_role(login: str) -> str:
    """Return the user's highest AD role (top-manager > manager > executor > author)."""
    return _base_role(login)


def _is_top_manager(login: str) -> bool:
    return _base_role(login) == "top-manager"


def _user_department_id(db: PgDbOperator, login: str) -> Optional[int]:
    """Return the department_id of the authenticated user (from their employee row)."""
    emp_id = _employee_id(login)
    if emp_id is None:
        return None
    rows = db.getRowFromTable("employee", "employee_id", int(emp_id))
    if not rows:
        return None
    return rows[0].get("department_id")


def _require_department_scope(db: PgDbOperator, login: str, target_department_id) -> None:
    """
    Enforce the department access rule:
      - top-manager: full access to every department;
      - manager: only their own department.
    Raises 403 otherwise.
    """
    if _is_top_manager(login):
        return
    own = _user_department_id(db, login)
    if target_department_id is None or own is None or int(target_department_id) != int(own):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="Out of your department scope")


def _visibility_conditions(role: str, emp_id, my_dept) -> tuple[list, list]:
    """Mandatory row-level visibility filter applied to every application listing.

      - top-manager: sees all applications;
      - manager: own department, or applications delegated into their department;
      - author/executor: only applications where they are the author or executor.

    Relies on the `author_link` / `exec_link` joins being present in the query.
    Returns (conditions, params) where each condition is ANDed into the WHERE.
    """
    if role == "top-manager":
        return [], []
    if role == "manager":
        if my_dept is None:
            return ["1=0"], []  # a manager with no department sees nothing
        # a.department_id is integer; delegated.delegated_to stores the id as text.
        return (
            ["(a.department_id = %s OR a.delegated_id IN "
             "(SELECT delegated_id FROM public.delegated WHERE delegated_to = %s))"],
            [int(my_dept), str(my_dept)],
        )
    # author / executor (and any other non-manager role)
    if emp_id is None:
        return ["1=0"], []
    return (["(author_link.employee_id = %s OR exec_link.employee_id = %s)"],
            [emp_id, emp_id])


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

    if filters.get("delegatedToMyDepartment") and filters.get("department_id") is not None:
        # Applications whose active delegation targets the current user's department.
        conditions.append("""
            a.delegated_id IN (
                SELECT delegated_id FROM public.delegated WHERE delegated_to = %s
            )
        """)
        params.append(str(filters["department_id"]))

    # Mandatory role-based visibility (department/involvement scoping).
    vis_conditions, vis_params = _visibility_conditions(
        filters.get("role", "author"), filters.get("employee_id"), filters.get("department_id")
    )
    conditions += vis_conditions
    params += vis_params

    # Always hide archived applications and rejected ones older than N days.
    cutoff = datetime.now(project_timezone) - timedelta(days=REJECTED_VISIBLE_DAYS)
    conditions.append("a.archived_at IS NULL")
    conditions.append("(s.name <> 'rejected' OR a.finished_at IS NULL OR a.finished_at >= %s)")
    params.append(cutoff)

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


def _record_status_change(cur, application_id, from_status_id, to_status_id,
                          by_employee_id, reason, at):
    """Append a row to public.application_status_history.

    Called inside the same transaction/cursor as the status change so the journal
    stays consistent with application state. `from_status_id` is None on creation.
    The analytics subsystem reads this journal for lifecycle-time metrics.
    """
    cur.execute(
        """
        INSERT INTO public.application_status_history
            (application_id, from_status_id, to_status_id, changed_at, by_employee_id, reason)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (int(application_id), from_status_id, to_status_id, at, by_employee_id, reason),
    )


def _create_notification(text: str, employee_id, application_id) -> None:
    """Insert one notification via the system connection (DBController).

    Uses the superuser pool, not the caller's per-user connection, so notification
    creation never depends on the user's table privileges. Best-effort: called only
    from _dispatch_action_notifications, which swallows errors.
    """
    if employee_id is None:
        return
    with DBController.pool.connection() as conn:
        conn.execute(
            "INSERT INTO public.notification (text, created_at, employee_id, is_read, application_id) "
            "VALUES (%s, %s, %s, false, %s)",
            (text, datetime.now(project_timezone), int(employee_id), int(application_id)),
        )


def _department_manager_ids(dept_id) -> list:
    """Active managers/top-managers of a department (recipients for delegation)."""
    from psycopg.rows import dict_row
    with DBController.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                "SELECT e.employee_id FROM public.employee e "
                "JOIN public.role r ON r.role_id = e.role_id "
                "WHERE e.department_id = %s AND r.name IN ('manager', 'top-manager') "
                "AND e.is_active = true",
                (int(dept_id),),
            ).fetchall()
    return [r["employee_id"] for r in rows]


def _dispatch_action_notifications(action, application_id, app_row, payload,
                                   user_role, actor_emp) -> None:
    """Create notifications for management events (see docs/backend-functions.md §5).

    Called AFTER the action transaction commits, best-effort: any failure is logged
    and never affects the already-committed action. Only events owned by the
    management subsystem are handled here; time-based/routing notifications belong
    to the events/routing subsystems.
    """
    try:
        name = app_row.get("name") or f"#{application_id}"
        author_id = app_row.get("author_id")
        if action == "assignExecutor" and payload.executorId:
            _create_notification(f"Вам назначена заявка: «{name}».",
                                 int(payload.executorId), application_id)
        elif action == "delegateExternal" and payload.departmentId:
            for mid in _department_manager_ids(int(payload.departmentId)):
                _create_notification(f"Заявка «{name}» делегирована в ваш отдел.",
                                     mid, application_id)
        elif action == "confirmExternalDelegation" and author_id:
            _create_notification(f"Делегирование заявки «{name}» подтверждено.",
                                 author_id, application_id)
        elif action == "declineExternalDelegation" and author_id:
            _create_notification(f"Делегирование заявки «{name}» отклонено.",
                                 author_id, application_id)
        elif action == "complete" and author_id:
            _create_notification(f"Заявка «{name}» выполнена.", author_id, application_id)
        elif action == "reject" and user_role in ("manager", "top-manager") and author_id:
            _create_notification(f"Заявка «{name}» отклонена руководителем.",
                                 author_id, application_id)
    except Exception as e:
        print(f"[notify] failed for action={action} app={application_id}: {e}")


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
def get_current_user(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_cfg = _user_cfg(login)

        emp_id = user_cfg.get("employee_id")
        rows = db.getRowFromTable("employee", "employee_id", emp_id)
        row_or_404(rows, "Employee not found")
        row = rows[0]

        # Enrich with login and the cumulative AD roles (not stored in DB).
        # A manager, for example, also implicitly holds author + executor.
        row["login"]  = login
        row["roles"]  = _expand_roles(_base_role(login))

        # Resolve job title (должность) from post_grade → post.
        # positionId is the post_id; postName is the post name (both come from AD).
        row["post_id"]   = ""
        row["post_name"] = ""
        pg_rows = db.getRowFromTable("post_grade", "post_grade_id", row.get("post_grade_id"))
        if pg_rows:
            post_rows = db.getRowFromTable("post", "post_id", pg_rows[0]["post_post_id"])
            if post_rows:
                row["post_id"]   = post_rows[0]["post_id"]
                row["post_name"] = post_rows[0]["name"]

        user_out = CurrentUserOut.model_validate(row)
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
    userData=Depends(authObj.authenticate),
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
        emp_id = _employee_id(login)
        my_dept = _user_department_id(db, login)
        role = _get_user_role(login)

        filters = dict(
            status=status_filter, priority=priority,
            createdByMe=createdByMe, assignedToMe=assignedToMe,
            delegatedToMyDepartment=delegatedToMyDepartment,
            executorName=executorName, applicationId=applicationId,
            sortBy=sortBy, sortDirection=sortDirection,
            page=page, pageSize=pageSize,
            employee_id=emp_id, department_id=my_dept, role=role,
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
                if delegatedToMyDepartment and my_dept is not None:
                    count_q += " AND a.delegated_id IN (SELECT delegated_id FROM public.delegated WHERE delegated_to = %s)"
                    count_params.append(str(my_dept))

                # Mandatory role-based visibility — identical to the list query.
                vis_conditions, vis_params = _visibility_conditions(role, emp_id, my_dept)
                for cond in vis_conditions:
                    count_q += f" AND {cond}"
                count_params += vis_params

                # Mirror the visibility rules applied in _build_application_list_query.
                cutoff = datetime.now(project_timezone) - timedelta(days=REJECTED_VISIBLE_DAYS)
                count_q += " AND a.archived_at IS NULL"
                count_q += " AND (s.name <> 'rejected' OR a.finished_at IS NULL OR a.finished_at >= %s)"
                count_params.append(cutoff)

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
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = _employee_id(login)

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

                # Journal the initial transition (— → new) for analytics.
                _record_status_change(
                    cur, app_id, None, status_row["status_id"], emp_id, "create", now
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
    userData=Depends(authObj.authenticate),
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
                        # Surface the cross-department ids on the application itself.
                        row["delegated_from_department_id"] = d.get("delegated_from")
                        row["delegated_to_department_id"]   = d.get("delegated_to")
                        row["delegated_to"]                 = d.get("delegated_to")

                # Work type (nested) — lets the UI fall back to workType.complexity
                # when the application has no assigned complexity yet.
                work_type = None
                if row.get("types_of_works"):
                    wt = cur.execute(
                        """
                        SELECT
                            t.type_of_works_id,
                            t.name,
                            t.department_id,
                            t.complexity_value,
                            COALESCE(json_agg(tg.grade_id) FILTER (WHERE tg.grade_id IS NOT NULL), '[]'::json) AS grade_ids
                        FROM public.types_of_works t
                        LEFT JOIN public.type_of_work_to_grade tg
                               ON tg.type_of_works_id = t.type_of_works_id
                        WHERE t.type_of_works_id = %s
                        GROUP BY t.type_of_works_id, t.name, t.department_id, t.complexity_value
                        """,
                        (row["types_of_works"],)
                    ).fetchone()
                    if wt:
                        work_type = WorkTypeOut.model_validate(wt).model_dump()

                # Nested employees (contract `User`) and department.
                login_by_emp = {
                    int(c["employee_id"]): uname
                    for uname, c in _ad_directory().items()
                    if c.get("employee_id") is not None
                }
                author_user = _user_dict(cur, row.get("author_id"), login_by_emp)
                executor_user = _user_dict(cur, row.get("executor_id"), login_by_emp)
                previous_executor_user = _user_dict(cur, row.get("previous_executor_id"), login_by_emp)
                delegated_by_employee_user = _user_dict(
                    cur,
                    d.get("delegated_by_employee") if row.get("delegated_id") and d else None,
                    login_by_emp,
                )

                department = None
                if row.get("department_id") is not None:
                    drow = cur.execute(
                        "SELECT * FROM public.department WHERE department_id = %s",
                        (row["department_id"],)
                    ).fetchone()
                    if drow:
                        department = DepartmentOut.model_validate(drow).model_dump()

        is_author, is_exec, mgr_scope = _action_scope(db, login, row, user_role)
        row["availableActions"] = _available_actions(
            row, user_role,
            is_author=is_author, is_assigned_executor=is_exec, manager_in_scope=mgr_scope,
        )
        if S3_ENDPOINT_URL and S3_BUCKET:
            s3 = get_s3_public()   # sign links against the browser-reachable endpoint
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
        row["workType"]   = work_type
        row["author"]     = author_user
        row["executor"]   = executor_user
        row["previousExecutor"] = previous_executor_user
        row["delegatedByEmployee"] = delegated_by_employee_user
        row["department"] = department

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
                              "cancel":           {"summary": "cancel",           "value": {"action": "cancel",           "comment": "Заявка создана ошибочно."}},
                              "archive":          {"summary": "archive",          "value": {"action": "archive"}},
                          },
                      }
                  },
              }
          })
def application_action(
    applicationId: int = Path(...),
    payload: ApplicationActionPayload = ...,
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_role = _get_user_role(login)
        emp_id = _employee_id(login)
        now = datetime.now(project_timezone)

        if payload.action not in ActionValues:
            raise HTTPException(status_code=400, detail=f"Unknown action: {payload.action}")

        # Applications bumped off a now-reassigned executor (one-app-per-executor rule),
        # collected for a best-effort notification after the transaction commits.
        bumped_for_notify: list = []

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                app_row = cur.execute(
                    """
                    SELECT a.*, s.name AS status_name,
                           author_link.employee_id AS author_id,
                           exec_link.employee_id   AS executor_id,
                           dl.delegated_to          AS delegated_to
                    FROM public.application a
                    LEFT JOIN public.status s ON s.status_id = a.status_id
                    LEFT JOIN public.employee_to_application author_link
                           ON author_link.application_id = a.application_id
                          AND author_link.role_id = (SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1)
                    LEFT JOIN public.employee_to_application exec_link
                           ON exec_link.application_id = a.application_id
                          AND exec_link.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
                    LEFT JOIN public.delegated dl ON dl.delegated_id = a.delegated_id
                    WHERE a.application_id = %s
                    """,
                    (int(applicationId),)
                ).fetchone()
                row_or_404(app_row, "Application not found")

                # Gate the action by the caller's actual involvement, not role alone:
                # this also enforces that only the assigned executor can run executor
                # actions and that managers act only within their department scope.
                is_author, is_exec, mgr_scope = _action_scope(db, login, app_row, user_role)
                available = _available_actions(
                    app_row, user_role,
                    is_author=is_author, is_assigned_executor=is_exec, manager_in_scope=mgr_scope,
                )
                if payload.action not in available:
                    raise HTTPException(status_code=403, detail="Action not permitted in current state")

                # Status at the start of the action — the "from" of any transition
                # journalled below. Each action calls set_status at most once.
                _prev_status_id = app_row.get("status_id")

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
                    # Journal the transition for analytics (reason = the action name).
                    _record_status_change(
                        cur, int(applicationId), _prev_status_id, st["status_id"],
                        emp_id, payload.action, now,
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

                        # One application per executor: any OTHER active application of
                        # this executor (assigned / inProgress) is released — returned to
                        # `new` with the Unfinished flag and previous_executor_id set, so
                        # it goes back into distribution. The executor link is kept (like
                        # returnToNew) so the previous executor still sees it.
                        busy = cur.execute(
                            """
                            SELECT a.application_id, a.name, a.status_id
                            FROM public.application a
                            JOIN public.employee_to_application eta
                              ON eta.application_id = a.application_id AND eta.role_id = %s
                            JOIN public.status s ON s.status_id = a.status_id
                            WHERE eta.employee_id = %s
                              AND s.name IN ('assigned', 'inProgress')
                              AND a.application_id <> %s
                            """,
                            (exec_role["role_id"], int(payload.executorId), int(applicationId))
                        ).fetchall()
                        if busy:
                            new_st = cur.execute(
                                "SELECT status_id FROM public.status WHERE name = 'new' LIMIT 1"
                            ).fetchone()
                            for b in busy:
                                cur.execute(
                                    "UPDATE public.application SET status_id = %s, is_unfinished = true, "
                                    "previous_executor_id = %s, updated_at = %s WHERE application_id = %s",
                                    (new_st["status_id"], int(payload.executorId), now, b["application_id"])
                                )
                                _record_status_change(
                                    cur, b["application_id"], b["status_id"], new_st["status_id"],
                                    emp_id, "reassigned_busy", now,
                                )
                                bumped_for_notify.append((b["application_id"], b["name"], int(payload.executorId)))

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
                    # Manager returns an assigned/in-progress application to `new`
                    # for redistribution (§8.1), flagging it Unfinished and recording
                    # who had it (§6.5 / status model row "→ Новый").
                    exec_role = cur.execute(
                        "SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1"
                    ).fetchone()
                    prev_exec = None
                    if exec_role:
                        link = cur.execute(
                            "SELECT employee_id FROM public.employee_to_application WHERE application_id = %s AND role_id = %s",
                            (int(applicationId), exec_role["role_id"])
                        ).fetchone()
                        prev_exec = link["employee_id"] if link else None
                    set_status("new")
                    cur.execute(
                        "UPDATE public.application SET is_unfinished = true, previous_executor_id = %s, updated_at = %s WHERE application_id = %s",
                        (prev_exec, now, int(applicationId))
                    )

                elif action == "delegateExternal":
                    if not payload.departmentId:
                        raise HTTPException(status_code=400, detail="departmentId required")
                    set_status("delegated")
                    delegated_id = cur.execute(
                        """
                        INSERT INTO public.delegated
                            (delegated_by, delegated_by_employee, delegated_from, delegated_to, comment, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING delegated_id
                        """,
                        (
                            str(app_row["department_id"]),
                            emp_id,
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

                elif action == "delegateInternal":
                    # Executor re-addresses a task within their own department because
                    # they can't handle it (§7.2, §9.2, §13.1). They set a complexity
                    # (not lower than the current one) directly on the application and
                    # may change the work type. If the department requires manager
                    # confirmation (§7.6, delegated_to_same_dep) it goes to `delegated`
                    # first; otherwise it returns straight to `new` for redistribution.
                    if not payload.complexity:
                        raise HTTPException(status_code=400, detail="complexity required")
                    new_value = ComplexityValues.index(payload.complexity) + 1
                    cur_idx = _effective_complexity_index(cur, app_row)
                    if cur_idx is not None and new_value < cur_idx:
                        raise HTTPException(
                            status_code=400,
                            detail="complexity cannot be lower than the current complexity")

                    # Complexity (and optional work type) are set on the application now.
                    cur.execute(
                        "UPDATE public.application SET empl_assigned_complexity = %s, updated_at = %s WHERE application_id = %s",
                        (new_value, now, int(applicationId))
                    )
                    if payload.workTypeId:
                        cur.execute(
                            "UPDATE public.application SET types_of_works = %s WHERE application_id = %s",
                            (int(payload.workTypeId), int(applicationId))
                        )

                    dep_row = cur.execute(
                        "SELECT delegated_to_same_dep FROM public.department WHERE department_id = %s",
                        (app_row["department_id"],)
                    ).fetchone()
                    needs_confirmation = bool(dep_row and dep_row.get("delegated_to_same_dep"))

                    if needs_confirmation:
                        # Pending manager confirmation: record an internal delegation
                        # (delegated_from == delegated_to == own department).
                        own_dep = str(app_row["department_id"])
                        delegated_id = cur.execute(
                            """
                            INSERT INTO public.delegated
                                (delegated_by, delegated_by_employee, delegated_from, delegated_to, comment, created_at)
                            VALUES (%s, %s, %s, %s, %s, %s) RETURNING delegated_id
                            """,
                            (own_dep, emp_id, own_dep, own_dep, payload.comment or "", now)
                        ).fetchone()["delegated_id"]
                        set_status("delegated")
                        cur.execute(
                            "UPDATE public.application SET delegated_id = %s, updated_at = %s WHERE application_id = %s",
                            (delegated_id, now, int(applicationId))
                        )
                    else:
                        # No confirmation needed → return to `new` for redistribution.
                        set_status("new")
                        cur.execute(
                            "UPDATE public.application SET is_unfinished = true, previous_executor_id = %s, updated_at = %s WHERE application_id = %s",
                            (emp_id, now, int(applicationId))
                        )

                elif action == "confirmExternalDelegation":
                    deleg = None
                    if app_row.get("delegated_id"):
                        deleg = cur.execute(
                            "SELECT * FROM public.delegated WHERE delegated_id = %s",
                            (app_row["delegated_id"],)
                        ).fetchone()
                        cur.execute(
                            "UPDATE public.delegated SET decision = 'confirmed', decided_at = %s WHERE delegated_id = %s",
                            (now, app_row["delegated_id"])
                        )
                    is_internal = bool(deleg and deleg.get("delegated_from") == deleg.get("delegated_to"))
                    set_status("new")
                    if is_internal:
                        # Internal re-addressing confirmed → redistribute within the dept.
                        cur.execute(
                            "UPDATE public.application SET is_unfinished = true, previous_executor_id = %s, delegated_id = NULL, updated_at = %s WHERE application_id = %s",
                            (deleg.get("delegated_by_employee"), now, int(applicationId))
                        )
                    else:
                        # External delegation confirmed → the application now BELONGS
                        # to the target department, so it joins that department's
                        # queue/visibility. (Previously department_id was left
                        # pointing at the original department — a routing/visibility bug.)
                        new_dep = deleg.get("delegated_to") if deleg else None
                        if new_dep is not None:
                            cur.execute(
                                "UPDATE public.application SET department_id = %s, updated_at = %s WHERE application_id = %s",
                                (int(new_dep), now, int(applicationId))
                            )
                        else:
                            cur.execute(
                                "UPDATE public.application SET updated_at = %s WHERE application_id = %s",
                                (now, int(applicationId))
                            )

                elif action == "declineExternalDelegation":
                    deleg = None
                    if app_row.get("delegated_id"):
                        deleg = cur.execute(
                            "SELECT * FROM public.delegated WHERE delegated_id = %s",
                            (app_row["delegated_id"],)
                        ).fetchone()
                        cur.execute(
                            "UPDATE public.delegated SET decision = 'declined', decided_at = %s WHERE delegated_id = %s",
                            (now, app_row["delegated_id"])
                        )
                    is_internal = bool(deleg and deleg.get("delegated_from") == deleg.get("delegated_to"))
                    if is_internal:
                        # Manager refused the internal re-addressing → the executor keeps
                        # it. Restore the working status (inProgress if work had started).
                        set_status("inProgress" if app_row.get("work_at") else "assigned")
                    else:
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

                elif action == "cancel":
                    # Cancel a `new` application → becomes `rejected`. Author or a
                    # manager/top-manager may cancel (enforced by _available_actions).
                    set_status("rejected")
                    cur.execute(
                        "UPDATE public.application SET finished_at = %s, closed_by_id = %s, updated_at = %s WHERE application_id = %s",
                        (now, emp_id, now, int(applicationId))
                    )

                elif action == "archive":
                    # Hide a finished application from the main UI without changing
                    # its status. Only allowed for rejected/completed (via _available_actions).
                    cur.execute(
                        "UPDATE public.application SET archived_at = %s, updated_at = %s WHERE application_id = %s",
                        (now, now, int(applicationId))
                    )

                # Persist a free-text comment to the actor's column (overwrite).
                # Managers/top-managers write manager_comment; executors write
                # executor_comment. (`complete` carries its note in resultText.)
                if payload.comment:
                    if user_role in ("manager", "top-manager"):
                        cur.execute(
                            "UPDATE public.application SET manager_comment = %s, updated_at = %s WHERE application_id = %s",
                            (payload.comment, now, int(applicationId))
                        )
                    elif user_role == "executor":
                        cur.execute(
                            "UPDATE public.application SET executor_comment = %s, updated_at = %s WHERE application_id = %s",
                            (payload.comment, now, int(applicationId))
                        )

        # Transaction committed — fire management-event notifications (best-effort,
        # via the system connection; never breaks the already-applied action).
        _dispatch_action_notifications(action, int(applicationId), app_row, payload, user_role, emp_id)

        # Notify executors whose application was released by the one-app-per-executor rule.
        for _b_id, _b_name, _b_emp in bumped_for_notify:
            try:
                _create_notification(
                    f"Заявка «{_b_name}» снята с вас и возвращена в статус «Новый» "
                    f"(вы назначены на другую заявку).",
                    _b_emp, _b_id,
                )
            except Exception as e:
                print(f"[notify] bump notify failed for app={_b_id}: {e}")

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
    userData=Depends(authObj.authenticate),
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
def get_departments(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        rows = db.getAllRowsFromTable("department")
        items = [DepartmentOut.model_validate(r).model_dump() for r in (rows or [])]
        return {"items": items}
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/employees", tags=["Directories"], summary="Получить сотрудников, подключенных к системе",
         description="Обычный руководитель получает сотрудников только своего отдела. top-manager может получать сотрудников всех отделов и фильтровать по departmentId.",
         response_model=EmployeeListResponse)
def get_employees(
    userData=Depends(authObj.authenticate),
    departmentId: Optional[str] = Query(default=None),
    isActive: Optional[bool]    = Query(default=None),
    role: Optional[str]         = Query(default=None),
):
    try:
        db = get_db_user(userData)
        login = userData[0]

        # Department scope: a non-top-manager only ever sees their own department.
        scope_department_id: Optional[int] = None
        if not _is_top_manager(login):
            scope_department_id = _user_department_id(db, login)

        # login is not stored in the DB — map employee_id → login from the directory.
        login_by_emp = {
            int(c["employee_id"]): uname
            for uname, c in _ad_directory().items()
            if c.get("employee_id") is not None
        }

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    """
                    SELECT
                        e.employee_id,
                        e.department_id,
                        e.fio,
                        e.is_active,
                        r.name      AS role,
                        po.post_id  AS post_id,
                        po.name     AS post_name
                    FROM public.employee e
                    LEFT JOIN public.role r        ON r.role_id = e.role_id
                    LEFT JOIN public.post_grade pg ON pg.post_grade_id = e.post_grade_id
                    LEFT JOIN public.post po       ON po.post_id = pg.post_post_id
                    WHERE e.deleted_at IS NULL
                    ORDER BY e.employee_id
                    """
                ).fetchall()

        result = []
        for row in rows:
            dep_id = row.get("department_id")
            if scope_department_id is not None and dep_id != scope_department_id:
                continue
            if departmentId and str(dep_id) != departmentId:
                continue

            is_active = row.get("is_active", True)
            if isActive is not None and is_active != isActive:
                continue

            emp_role = row.get("role") or "author"
            if role and role != emp_role:
                continue

            result.append({
                "id":           str(row["employee_id"]),
                "login":        login_by_emp.get(row["employee_id"], ""),
                "fullName":     row.get("fio", ""),
                "role":         emp_role,
                "departmentId": str(dep_id or ""),
                "postName":     row.get("post_name") or "",
                "positionId":   str(row.get("post_id") or ""),
                "isActive":     is_active,
            })

        return {"items": result}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/employees", status_code=201, tags=["Directories"], summary="Добавить AD-пользователя в систему",
          description="Не создает человека в AD. Backend создает локальную запись участия в системе для уже существующего AD-пользователя. Роль выбирает руководитель, должность приходит из AD. Обычный руководитель может добавлять сотрудников только в свой отдел, top-manager — в любой.",
          response_model=IdResponse)
def add_employee(
    payload: CreateEmployeePayload,
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManageEmployees")
        db = get_db_user(userData)
        login = userData[0]
        now = datetime.now(project_timezone)

        # The job title (должность) is not sent by the UI — it comes from AD.
        ad_login, ad_user = _find_ad_by_id(payload.adUserId)
        if not ad_user or ad_user.get("inSystem"):
            # Unknown AD person, or already onboarded into the system.
            raise HTTPException(status_code=400, detail="AD user not found")

        ad_department_id = ad_user.get("departmentId")
        ad_post_name     = ad_user.get("position", "")

        # Managers can only add employees to their own department.
        _require_department_scope(db, login, ad_department_id)

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                # Resolve the role.
                role_row = cur.execute(
                    "SELECT role_id FROM public.role WHERE name = %s LIMIT 1", (payload.role,)
                ).fetchone()
                if not role_row:
                    raise HTTPException(status_code=400, detail=f"Unknown role: {payload.role}")

                # Resolve a post_grade for the AD job title (должность).
                pg_row = cur.execute(
                    """
                    SELECT pg.post_grade_id
                    FROM public.post_grade pg
                    JOIN public.post po ON po.post_id = pg.post_post_id
                    WHERE po.name = %s
                    ORDER BY pg.post_grade_id
                    LIMIT 1
                    """,
                    (ad_post_name,)
                ).fetchone()
                post_grade_id = pg_row["post_grade_id"] if pg_row else None

                emp_id = cur.execute(
                    """
                    INSERT INTO public.employee
                        (department_id, post_grade_id, role_id, fio, created_at, updated_at, is_active)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING employee_id
                    """,
                    (ad_department_id, post_grade_id, role_row["role_id"],
                     ad_user.get("fullName", "AD User"), now, now, payload.isActive)
                ).fetchone()["employee_id"]

        # Reflect the onboarding back into the in-memory directory so this person
        # stops appearing as an addable AD candidate (mock-only; resets on restart).
        ad_user["inSystem"] = True
        ad_user["employee_id"] = emp_id
        ad_user["role"] = payload.role

        return {"id": str(emp_id)}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.patch("/employees/{employeeId}", status_code=204, tags=["Directories"], summary="Изменить роль сотрудника или участие в распределении",
           description="Обычный руководитель может менять только сотрудников своего отдела, top-manager — любого отдела.")
def update_employee(
    payload: UpdateEmployeePayload,
    employeeId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManageEmployees")
        db = get_db_user(userData)
        login = userData[0]
        now = datetime.now(project_timezone)

        rows = db.getRowFromTable("employee", "employee_id", int(employeeId))
        row_or_404(rows, "Employee not found")

        # Managers can only touch employees of their own department.
        _require_department_scope(db, login, rows[0].get("department_id"))

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                if payload.role is not None:
                    role_row = cur.execute(
                        "SELECT role_id FROM public.role WHERE name = %s LIMIT 1", (payload.role,)
                    ).fetchone()
                    if not role_row:
                        raise HTTPException(status_code=400, detail=f"Unknown role: {payload.role}")
                    cur.execute(
                        "UPDATE public.employee SET role_id = %s, updated_at = %s WHERE employee_id = %s",
                        (role_row["role_id"], now, int(employeeId))
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


@app.delete("/employees/{employeeId}", status_code=204, tags=["Directories"], summary="Удалить сотрудника из системы",
            description="Удаляет локальную запись участия сотрудника в системе, не удаляя пользователя из AD. Обычный руководитель может удалять только сотрудников своего отдела, top-manager — любого отдела.")
def delete_employee(
    employeeId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManageEmployees")
        db = get_db_user(userData)
        login = userData[0]
        now = datetime.now(project_timezone)

        rows = db.getRowFromTable("employee", "employee_id", int(employeeId))
        row_or_404(rows, "Employee not found")

        _require_department_scope(db, login, rows[0].get("department_id"))

        # Soft-delete: drop the system participation (deactivate + mark deleted),
        # but keep the row so historical application links stay intact.
        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.employee SET deleted_at = %s, is_active = false, updated_at = %s WHERE employee_id = %s",
                    (now, now, int(employeeId))
                )

        # Reverse of add_employee: free the AD person in the in-memory directory so
        # they reappear as an addable candidate in /ad/users (mock-only; not deleted
        # from AD). Resets on restart.
        for _entry in _ad_directory().values():
            if _entry.get("employee_id") == int(employeeId):
                _entry["inSystem"] = False
                _entry.pop("employee_id", None)
                _entry.pop("role", None)
                break

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.patch("/departments/{departmentId}/delegation-settings", status_code=204, tags=["Directories"],
           summary="Изменить подтверждение делегирования внутри отдела",
           description="Обычный руководитель меняет только свой отдел, top-manager — любой.")
def update_department_delegation_settings(
    payload: UpdateDepartmentDelegationSettingsPayload,
    departmentId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        require_manager_role(login)

        rows = db.getRowFromTable("department", "department_id", int(departmentId))
        row_or_404(rows, "Department not found")

        _require_department_scope(db, login, int(departmentId))

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.department SET delegated_to_same_dep = %s WHERE department_id = %s",
                    (payload.delegatedToSameDepartment, int(departmentId))
                )

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.patch("/departments/{departmentId}", status_code=204, tags=["Directories"],
           summary="Изменить настройки отдела (кулдаун назначения, порог уведомления о дедлайне)",
           description="Меняет empl_appl_delay (минуты) и/или deadline_notification (0..1). "
                       "Обычный руководитель — только свой отдел, top-manager — любой.")
def update_department(
    payload: UpdateDepartmentPayload,
    departmentId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        require_manager_role(login)

        rows = db.getRowFromTable("department", "department_id", int(departmentId))
        row_or_404(rows, "Department not found")

        _require_department_scope(db, login, int(departmentId))

        sets, params = [], []
        if payload.employeeApplicationDelayMinutes is not None:
            sets.append("empl_appl_delay = %s"); params.append(int(payload.employeeApplicationDelayMinutes))
        if payload.deadlineNotificationRatio is not None:
            sets.append("deadline_notification = %s"); params.append(float(payload.deadlineNotificationRatio))

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.department SET " + ", ".join(sets) + " WHERE department_id = %s",
                    params + [int(departmentId)],
                )

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/positions", tags=["Directories"], summary="Получить должности",
         description="Должность сотрудника приходит из AD и не редактируется руководителем вручную. Соответствует таблице post.",
         response_model=PositionListResponse)
def get_positions(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    "SELECT post_id, name FROM public.post ORDER BY post_id"
                ).fetchall()

        items = [PositionOut.model_validate(r).model_dump() for r in rows]
        return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/grades", tags=["Directories"], summary="Получить грейды",
         description="Грейды используются только в матрице допустимости вида работ и не являются должностью сотрудника. Соответствует таблице grade.",
         response_model=GradeListResponse)
def get_grades(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    "SELECT grade_id, name FROM public.grade ORDER BY grade_id"
                ).fetchall()

        items = [GradeOut.model_validate(r).model_dump() for r in rows]
        return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/ad/users", tags=["Directories"], summary="Найти пользователей AD для добавления в систему", response_model=AdUserListResponse)
def get_ad_users(
    userData=Depends(authObj.authenticate),
    query: Optional[str]        = Query(default=None),
    departmentId: Optional[str] = Query(default=None),
):
    """Returns AD people not yet onboarded into the system (addable candidates)."""
    try:
        db = get_db_user(userData)
        result = []
        for ad_login, ucfg in _ad_directory().items():
            if ucfg.get("inSystem"):
                continue  # already a system participant — not an addable candidate
            if query and query.lower() not in ucfg.get("fullName", "").lower():
                continue
            if departmentId and str(ucfg.get("departmentId", "")) != departmentId:
                continue
            result.append({
                "adUserId":    str(ucfg.get("adUserId", "")),
                "login":       ad_login,
                "fullName":    ucfg.get("fullName", ""),
                "departmentId":str(ucfg.get("departmentId", "")),
                "postName":    ucfg.get("position", ""),
            })
        return {"items": result}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/work-types", tags=["Directories"], summary="Получить виды работ",
         description="Все авторизованные пользователи видят виды работ всех отделов и могут фильтровать их по departmentId.",
         response_model=WorkTypeListResponse)
def get_work_types_all(
    userData=Depends(authObj.authenticate),
    departmentId: Optional[str] = Query(default=None),
):
    try:
        db = get_db_user(userData)
        query = """
            SELECT
                t.type_of_works_id,
                t.name,
                t.department_id,
                t.complexity_value,
                COALESCE(json_agg(tg.grade_id) FILTER (WHERE tg.grade_id IS NOT NULL), '[]'::json) AS grade_ids
            FROM public.types_of_works t
            LEFT JOIN public.type_of_work_to_grade tg
                   ON tg.type_of_works_id = t.type_of_works_id
        """
        params = []
        if departmentId:
            query += " WHERE t.department_id = %s"
            params.append(int(departmentId))
        query += " GROUP BY t.type_of_works_id, t.name, t.department_id, t.complexity_value ORDER BY t.type_of_works_id"

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                data = cur.execute(query, params).fetchall()

        items = [WorkTypeOut.model_validate(r).model_dump() for r in (data or [])]
        return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.post("/work-types", status_code=201, tags=["Directories"], summary="Создать вид работ",
          description="Обычный руководитель создает виды работ только для своего отдела, top-manager — для любого.",
          response_model=IdResponse)
def create_work_type(
    payload: CreateWorkTypePayload,
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManageWorkTypes")
        db = get_db_user(userData)
        login = userData[0]

        dep = DBController.getRowFromTable("department", "department_id", int(payload.departmentId))
        if not dep:
            raise HTTPException(status_code=400, detail="Department not found")

        # Managers can only create work types for their own department.
        _require_department_scope(db, login, int(payload.departmentId))

        with db.pool.connection() as conn:
            from psycopg.rows import dict_row
            with conn.cursor(row_factory=dict_row) as cur:
                tow_id = cur.execute(
                    """
                    INSERT INTO public.types_of_works (name, complexity_value, department_id)
                    VALUES (%s, %s, %s)
                    RETURNING type_of_works_id
                    """,
                    (payload.name, ComplexityValues.index(payload.complexity) + 1, int(payload.departmentId))
                ).fetchone()["type_of_works_id"]

                for grade_id in payload.allowedGradeIds:
                    cur.execute(
                        "INSERT INTO public.type_of_work_to_grade (type_of_works_id, grade_id) VALUES (%s, %s)",
                        (tow_id, int(grade_id))
                    )

        return {"id": str(tow_id)}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.patch("/work-types/{workTypeId}", status_code=204, tags=["Directories"], summary="Изменить вид работ, сложность или допустимые грейды",
           description="Обычный руководитель может менять виды работ только своего отдела, top-manager — любого.")
def update_work_type(
    payload: UpdateWorkTypePayload,
    workTypeId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManageWorkTypes")
        db = get_db_user(userData)
        login = userData[0]

        rows = db.getRowFromTable("types_of_works", "type_of_works_id", int(workTypeId))
        row_or_404(rows, "Work type not found")

        # Scope check against the work type's current department.
        _require_department_scope(db, login, rows[0].get("department_id"))
        # If moving to another department, that target must also be in scope.
        if payload.departmentId is not None:
            dep = DBController.getRowFromTable("department", "department_id", int(payload.departmentId))
            if not dep:
                raise HTTPException(status_code=400, detail="Department not found")
            _require_department_scope(db, login, int(payload.departmentId))

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                if payload.name is not None:
                    cur.execute(
                        "UPDATE public.types_of_works SET name = %s WHERE type_of_works_id = %s",
                        (payload.name, int(workTypeId))
                    )
                if payload.departmentId is not None:
                    cur.execute(
                        "UPDATE public.types_of_works SET department_id = %s WHERE type_of_works_id = %s",
                        (int(payload.departmentId), int(workTypeId))
                    )
                if payload.complexity is not None:
                    cur.execute(
                        "UPDATE public.types_of_works SET complexity_value = %s WHERE type_of_works_id = %s",
                        (ComplexityValues.index(payload.complexity) + 1, int(workTypeId))
                    )
                if payload.allowedGradeIds is not None:
                    # Replace the allowed-grade matrix wholesale.
                    cur.execute(
                        "DELETE FROM public.type_of_work_to_grade WHERE type_of_works_id = %s",
                        (int(workTypeId),)
                    )
                    for grade_id in payload.allowedGradeIds:
                        cur.execute(
                            "INSERT INTO public.type_of_work_to_grade (type_of_works_id, grade_id) VALUES (%s, %s)",
                            (int(workTypeId), int(grade_id))
                        )

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.delete("/work-types/{workTypeId}", status_code=204, tags=["Directories"], summary="Удалить вид работ",
            description="Обычный руководитель может удалять виды работ только своего отдела, top-manager — любого.")
def delete_work_type(
    workTypeId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManageWorkTypes")
        db = get_db_user(userData)
        login = userData[0]

        rows = db.getRowFromTable("types_of_works", "type_of_works_id", int(workTypeId))
        row_or_404(rows, "Work type not found")

        _require_department_scope(db, login, rows[0].get("department_id"))

        # Check if any application references this work type (conflict)
        apps = db.getRowFromTable("application", "types_of_works", int(workTypeId))
        if apps:
            raise HTTPException(status_code=409, detail="Work type is referenced by existing applications")

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM public.type_of_work_to_grade WHERE type_of_works_id = %s",
                    (int(workTypeId),)
                )
                cur.execute(
                    "DELETE FROM public.types_of_works WHERE type_of_works_id = %s",
                    (int(workTypeId),)
                )
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


# ─── Priority settings ───────────────────────────────────────────

# In-memory store (replace with a DB table in production).
# New shape per contract:
#   department:    { departmentId: coeff }   — department factor
#   managerAuthor: { departmentId: coeff }   — "manager as author" factor
#   deadline:      single global coeff
# The "author position" factor was removed from the priority calculation.
# Settings are persisted in public.priority_settings via priority_settings_store
# (was an in-memory dict that reset on restart). The API contract is unchanged.


@app.get("/priority-settings", tags=["Priority"], summary="Получить коэффициенты расчета приоритета",
         description="Обычный руководитель получает настройки только своего отдела в режиме чтения. top-manager получает все отделы и может редактировать.",
         response_model=PrioritySettingsModel)
def get_priority_settings(userData=Depends(authObj.authenticate)):
    try:
        require_permission(userData, "canManagePrioritySettings")
        db = get_db_user(userData)
        login = userData[0]
        settings = ps_store.load_effective(db)

        if _is_top_manager(login):
            return settings

        # A regular manager only sees their own department's coefficients.
        own = _user_department_id(db, login)
        own_key = str(own) if own is not None else None
        return {
            "department":    {own_key: settings["department"].get(own_key, ps_store.DEFAULT_COEFF)} if own_key else {},
            "managerAuthor": {own_key: settings["managerAuthor"].get(own_key, ps_store.DEFAULT_COEFF)} if own_key else {},
            "deadline":      settings["deadline"],
        }
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.put("/priority-settings", tags=["Priority"], summary="Сохранить коэффициенты расчета приоритета",
         description="Доступно только top-manager.",
         response_model=PrioritySettingsModel)
def update_priority_settings(
    payload: PrioritySettingsModel,
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManagePrioritySettings")
        db = get_db_user(userData)
        login = userData[0]
        require_top_manager(login)   # only a top-manager may persist settings
        ps_store.save(db, dict(payload.department), dict(payload.managerAuthor), payload.deadline)
        # Echo back exactly what was saved (unchanged API contract); the merged
        # per-department defaults are applied on read (GET /priority-settings).
        return {
            "department":    dict(payload.department),
            "managerAuthor": dict(payload.managerAuthor),
            "deadline":      payload.deadline,
        }
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


# ─── Notifications ───────────────────────────────────────────────

@app.get("/notifications", tags=["Notifications"], summary="Получить уведомления текущего пользователя",
         description="Текущий контракт рассчитан на pull-модель: backend создает уведомления при событиях системы, frontend периодически запрашивает список или обновляет его после действий пользователя.",
         response_model=NotificationsResponse)
def get_notifications(
    userData=Depends(authObj.authenticate),
    unreadOnly: bool = Query(default=False),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = _employee_id(login)

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
    userData=Depends(authObj.authenticate),
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
def mark_all_notifications_read(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = _employee_id(login)

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
    department_id=None,
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
    # Department scope: a regular manager only sees their own department's
    # applications (top-manager passes None → no restriction).
    if department_id is not None:
        base += " AND a.department_id = %s"; params.append(int(department_id))
    base += " ORDER BY a.created_at DESC"
    return base, params


@app.get("/reports/applications", tags=["Reports"], summary="Сформировать предварительный отчет по заявкам",
         description="Возвращает JSON-данные для предпросмотра отчета. Фильтры передаются query-параметрами, потому что формирование отчета не меняет состояние backend.",
         response_model=ApplicationReportResponse)
def report_applications(
    userData=Depends(authObj.authenticate),
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
        login = userData[0]
        # A regular manager only reports on their own department; top-manager on all.
        report_dept = None if _is_top_manager(login) else _user_department_id(db, login)

        query, params = _build_report_query(
            createdFrom, createdTo, finishedFrom, finishedTo, status_filter, executorId,
            department_id=report_dept,
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
    userData=Depends(authObj.authenticate),
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
        login = userData[0]
        # A regular manager only reports on their own department; top-manager on all.
        report_dept = None if _is_top_manager(login) else _user_department_id(db, login)

        query, params = _build_report_query(
            createdFrom, createdTo, finishedFrom, finishedTo, status_filter, executorId,
            department_id=report_dept,
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


# ─── Analytics ──────────────────────────────────────────────────
# Предварительная версия (см. docs/backend-functions.md §4). У фронтенда пока нет
# потребителя — формат JSON может измениться после согласования. Доступ: право
# canViewReports; обычный руководитель — только свой отдел, top-manager — все.
# from/to фильтруют по дате создания заявки; их отсутствие = «за всё время».

def _analytics_scope(userData):
    """(db, department_id) — None для top-manager (все отделы), иначе свой отдел."""
    require_permission(userData, "canViewReports")
    db = get_db_user(userData)
    login = userData[0]
    dept = None if _is_top_manager(login) else _user_department_id(db, login)
    return db, dept


@app.get("/analytics/applications", tags=["Analytics"], summary="Аналитика по заявкам")
def analytics_applications(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.applications_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/analytics/executors", tags=["Analytics"], summary="Аналитика по исполнителям")
def analytics_executors(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.executors_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/analytics/work-types", tags=["Analytics"], summary="Аналитика по видам работ")
def analytics_work_types(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.work_types_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@app.get("/analytics/departments", tags=["Analytics"], summary="Аналитика по отделам")
def analytics_departments(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.departments_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)
