"""
core.py — общая инфраструктура backend, вынесена из main.py при декомпозиции.

Содержит:
  • справочник Mock AD / роли пользователей (config.json → MOCK_AD, ROLE_LADDER);
  • bootstrap на импорте: системное подключение DBController, idempotent-миграции,
    Postgres-роли и гранты, сидинг БД, пре-создание login-ролей пользователей;
  • кэш per-user подключений (get_db_user) и общие проверки прав/скоупа.

ВАЖНО: импорт модуля выполняет миграции и сидинг (как раньше делал импорт main.py).
Модули, которые тестируются отдельно от приложения (events/routing/priority), НЕ должны
импортировать core — иначе импорт в тестах перезатрёт базу сидом.
"""

import threading

import psycopg
from fastapi import HTTPException, status

from src.application_module import (
    ActiveDirectoryAuth, PgDbOperator, configData,
)
from src.seed import seed_database, seed_demo_notifications

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


def _get_user_role(login: str) -> str:
    """Return the user's highest AD role (top-manager > manager > executor > author)."""
    return _base_role(login)


def _is_top_manager(login: str) -> bool:
    return _base_role(login) == "top-manager"


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
    # Dedup flag for the routing subsystem: set once a critical application was escalated
    # to the manager (нет свободных и некого вытеснять); сбрасывается при назначении.
    _conn.execute("ALTER TABLE public.application ADD COLUMN IF NOT EXISTS escalation_notified boolean")
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

# ─────────────────────────── Per-user DB access ───────────────────────────

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


# ─────────────────────────── Shared guards / helpers ───────────────────────────

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


def held_permissions(login: str, permissions: list) -> dict:
    """Map each permission name to whether the user holds it (one query for all)."""
    lowered = {p.lower(): p for p in permissions}
    with DBController.pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT r.rolname FROM pg_auth_members m
            JOIN pg_roles r ON r.oid = m.roleid
            JOIN pg_roles u ON u.oid = m.member
            WHERE u.rolname = %s AND r.rolname = ANY(%s)
            """,
            (login.lower(), list(lowered.keys())),
        ).fetchall()
    held = {r[0] for r in rows}
    return {orig: (low in held) for low, orig in lowered.items()}


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


def _user_department_id(db: PgDbOperator, login: str):
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


def login_by_employee_map() -> dict:
    """employee_id → login map from the AD directory (login is not stored in the DB)."""
    return {
        int(c["employee_id"]): uname
        for uname, c in _ad_directory().items()
        if c.get("employee_id") is not None
    }
