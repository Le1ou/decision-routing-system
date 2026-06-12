"""
applications_api.py — подсистема управления заявками: список/создание/карточка,
конечный автомат действий и вложения (вынесено из main.py при декомпозиции).

Здесь же живёт вся бизнес-логика видимости заявок (row-level scope по ролям) и
вычисление availableActions. Поведение API не менялось при переносе.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import Response
from psycopg.rows import dict_row

from src import db_helpers, priority_module, s3_module
from src.application_module import PgDbOperator, configData, project_timezone
from src.core import (
    DBController, _employee_id, _get_user_role, _raise_for_db_error,
    _user_department_id, authObj, get_db_user, login_by_employee_map, row_or_404,
)
from src.schemas import (
    ActionValues, ApplicationActionPayload, ApplicationDetailOut,
    ApplicationDetailResponse, ApplicationListItemOut, ApplicationListResponse,
    AttachmentUploadResponse, ComplexityValues, CreateApplicationPayload,
    DelegationOut, DepartmentOut, IdResponse, WorkTypeOut,
)

router = APIRouter(tags=["Applications"])

# Number of days after which a rejected application disappears from the main UI.
# Настраивается в config.json → applications.rejected_visible_days (default 7).
REJECTED_VISIBLE_DAYS = int(
    (configData.get("applications") or {}).get("rejected_visible_days", 7)
)

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


def _can_view_application(db: PgDbOperator, login: str, app_row: dict, user_role: str) -> bool:
    """Может ли пользователь видеть карточку заявки. Та же модель, что у списка
    (_visibility_conditions): top-manager — все; руководитель — свой отдел / делегированные
    к нему; автор и назначенный исполнитель — свои. Требует в app_row author_id, executor_id,
    department_id и (для руководителя) delegated_to."""
    is_author, is_exec, mgr_scope = _action_scope(db, login, app_row, user_role)
    return bool(mgr_scope or is_author or is_exec)


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


# FROM/JOIN clause shared by the list query and its COUNT twin, so the visibility
# rules and filters are guaranteed to stay in sync between the two.
_LIST_FROM = """
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


def _build_application_list_query(filters: dict) -> tuple[str, list, str, list]:
    """Build the parameterised list SELECT and its COUNT twin from ONE set of
    conditions (the count query used to be hand-maintained separately and could
    drift from the list filters). Returns (query, params, count_query, count_params)."""
    params = []
    conditions = []

    if filters.get("status"):
        conditions.append("s.name = %s")
        params.append(filters["status"])

    if filters.get("priority"):
        conditions.append("p.name = %s")
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
        conditions.append(
            "exec_link.employee_id IN "
            "(SELECT employee_id FROM public.employee WHERE fio ILIKE %s)")
        params.append(f"%{filters['executorName']}%")

    if filters.get("delegatedToMyDepartment") and filters.get("department_id") is not None:
        # Applications whose active delegation targets the current user's department.
        conditions.append(
            "a.delegated_id IN "
            "(SELECT delegated_id FROM public.delegated WHERE delegated_to = %s)")
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

    where_extra = "".join(f" AND {cond}" for cond in conditions)

    query = """
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
    """ + _LIST_FROM + where_extra

    sort_col_map = {
        "priority": "p.value",
        "status":   "s.name",
        "createdAt": "a.created_at",
        "finishedAt": "a.finished_at",
    }
    sort_col = sort_col_map.get(filters.get("sortBy", "priority"), "p.value")
    sort_dir = "DESC" if filters.get("sortDirection", "desc") == "desc" else "ASC"
    query += f" ORDER BY {sort_col} {sort_dir}"

    page     = max(1, filters.get("page", 1))
    pageSize = min(100, max(1, filters.get("pageSize", 50)))
    offset   = (page - 1) * pageSize
    query += f" LIMIT {pageSize} OFFSET {offset}"

    count_query = "SELECT COUNT(*) AS cnt" + _LIST_FROM + where_extra
    return query, params, count_query, list(params)


def _dispatch_action_notifications(action, application_id, app_row, payload,
                                   user_role, actor_emp) -> None:
    """Create notifications for management events (see docs/backend-functions.md §5).

    Called AFTER the action transaction commits, best-effort: any failure is logged
    and never affects the already-committed action. Only events owned by the
    management subsystem are handled here; time-based/routing notifications belong
    to the events/routing subsystems.
    """
    try:
        now = datetime.now(project_timezone)
        name = app_row.get("name") or f"#{application_id}"
        author_id = app_row.get("author_id")
        if action == "assignExecutor" and payload.executorId:
            db_helpers.create_notification(
                DBController, f"Вам назначена заявка: «{name}».",
                int(payload.executorId), application_id, now)
        elif action == "delegateExternal" and payload.departmentId:
            for mid in db_helpers.department_manager_ids(DBController, int(payload.departmentId)):
                db_helpers.create_notification(
                    DBController, f"Заявка «{name}» делегирована в ваш отдел.",
                    mid, application_id, now)
        elif action == "delegateInternal":
            # Если у отдела включено подтверждение, внутреннее делегирование уходит в
            # статус `delegated` и ждёт решения руководителя — уведомляем его.
            dep_id = app_row.get("department_id")
            if dep_id is not None:
                with DBController.pool.connection() as conn:
                    drow = conn.execute(
                        "SELECT delegated_to_same_dep FROM public.department WHERE department_id = %s",
                        (dep_id,),
                    ).fetchone()
                if drow and drow[0]:
                    for mid in db_helpers.department_manager_ids(DBController, dep_id):
                        db_helpers.create_notification(
                            DBController,
                            f"Заявка «{name}» направлена на подтверждение делегирования внутри отдела.",
                            mid, application_id, now)
        elif action == "confirmExternalDelegation" and author_id:
            db_helpers.create_notification(
                DBController, f"Делегирование заявки «{name}» подтверждено.",
                author_id, application_id, now)
        elif action == "declineExternalDelegation" and author_id:
            db_helpers.create_notification(
                DBController, f"Делегирование заявки «{name}» отклонено.",
                author_id, application_id, now)
        elif action == "complete" and author_id:
            db_helpers.create_notification(
                DBController, f"Заявка «{name}» выполнена.", author_id, application_id, now)
        elif action == "reject" and user_role in ("manager", "top-manager") and author_id:
            db_helpers.create_notification(
                DBController, f"Заявка «{name}» отклонена руководителем.",
                author_id, application_id, now)
    except Exception as e:
        print(f"[notify] failed for action={action} app={application_id}: {e}")


# ─────────────────────────── Routes ──────────────────────────────────

@router.get("/applications", summary="Получить список заявок",
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

        query, params, count_query, count_params = _build_application_list_query(filters)

        with db.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                total = cur.execute(count_query, count_params).fetchone()["cnt"]
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


@router.post("/applications", status_code=201, summary="Создать заявку",
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
            with conn.cursor(row_factory=dict_row) as cur:
                status_row = cur.execute(
                    "SELECT status_id FROM public.status WHERE name = 'new' LIMIT 1"
                ).fetchone()
                if not status_row:
                    raise HTTPException(status_code=500, detail="Status 'new' not seeded")

                # Temporary priority_id for the insert; the real priority_score and
                # derived priority_id are computed right after the author is linked
                # (priority depends on the author's department/role — see below).
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
                db_helpers.record_status_change(
                    cur, app_id, None, status_row["status_id"], emp_id, "create", now
                )

                # Compute the real priority (score + derived level) now that the author
                # link exists; overwrites the temporary priority_id above. Same cursor →
                # reads the just-inserted rows within this transaction.
                prio = priority_module.recompute_and_store(db, cur, app_id, now)

        # Немедленная маршрутизация критичной заявки — не ждём фоновый тик (до 30с).
        # Запускается ПОСЛЕ коммита транзакции создания (routing читает заявку из БД),
        # под системным соединением, хирургично по одной заявке. Best-effort: сбой
        # распределения не должен валить создание заявки (её подхватит фоновый тик).
        if prio and prio[1] == "critical":
            try:
                from src import routing_module
                routing_module.run_routing(DBController, only_application_id=app_id)
            except Exception as _re:
                print(f"[routing] immediate critical trigger error: {_re}")

        return {"id": str(app_id)}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.get("/applications/{applicationId}", summary="Получить карточку заявки",
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
                d = None
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

                # Row-level visibility (та же модель, что у списка): чужую заявку не
                # отдаём (межотдельная утечка карточки). delegated_to уже проставлен выше.
                if not _can_view_application(db, login, row, user_role):
                    raise HTTPException(status_code=404, detail="Application not found")

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
                            COALESCE(json_agg(DISTINCT tg.grade_id) FILTER (WHERE tg.grade_id IS NOT NULL), '[]'::json) AS grade_ids,
                            COALESCE(json_agg(DISTINCT tp.post_id) FILTER (WHERE tp.post_id IS NOT NULL), '[]'::json) AS post_ids
                        FROM public.types_of_works t
                        LEFT JOIN public.type_of_work_to_grade tg
                               ON tg.type_of_works_id = t.type_of_works_id
                        LEFT JOIN public.type_of_work_to_post tp
                               ON tp.type_of_works_id = t.type_of_works_id
                        WHERE t.type_of_works_id = %s
                        GROUP BY t.type_of_works_id, t.name, t.department_id, t.complexity_value
                        """,
                        (row["types_of_works"],)
                    ).fetchone()
                    if wt:
                        work_type = WorkTypeOut.model_validate(wt).model_dump()

                # Nested employees (contract `User`) and department.
                login_by_emp = login_by_employee_map()
                author_user = _user_dict(cur, row.get("author_id"), login_by_emp)
                executor_user = _user_dict(cur, row.get("executor_id"), login_by_emp)
                previous_executor_user = _user_dict(cur, row.get("previous_executor_id"), login_by_emp)
                delegated_by_employee_user = _user_dict(
                    cur,
                    d.get("delegated_by_employee") if d else None,
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
        row["attachments"] = s3_module.build_attachment_items(photos)
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


@router.post("/applications/{applicationId}/actions", status_code=204, summary="Выполнить действие над заявкой",
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
                    db_helpers.record_status_change(
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
                        "UPDATE public.application SET executor_at = %s, is_unfinished = false, "
                        "updated_at = %s WHERE application_id = %s",
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
                                db_helpers.record_status_change(
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
                        "UPDATE public.application SET result_text = %s, finished_at = %s, "
                        "is_unfinished = false, updated_at = %s WHERE application_id = %s",
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
                            (delegated_by, delegated_by_employee, delegated_from, delegated_to,
                             comment, created_at, application_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING delegated_id
                        """,
                        (
                            str(app_row["department_id"]),
                            emp_id,
                            str(app_row["department_id"]),
                            payload.departmentId,
                            payload.comment or "",
                            now,
                            int(applicationId),
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
                                (delegated_by, delegated_by_employee, delegated_from, delegated_to,
                                 comment, created_at, application_id)
                            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING delegated_id
                            """,
                            (own_dep, emp_id, own_dep, own_dep, payload.comment or "", now,
                             int(applicationId))
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
                        # delegated_id очищается (делегирование разрешено): иначе следующий
                        # assignExecutor перепишет уже подтверждённую запись delegated в
                        # 'declined' (порча истории/аналитики). Связь с заявкой для истории
                        # сохраняется через delegated.application_id.
                        new_dep = deleg.get("delegated_to") if deleg else None
                        if new_dep is not None:
                            cur.execute(
                                "UPDATE public.application SET department_id = %s, delegated_id = NULL, "
                                "updated_at = %s WHERE application_id = %s",
                                (int(new_dep), now, int(applicationId))
                            )
                        else:
                            cur.execute(
                                "UPDATE public.application SET delegated_id = NULL, updated_at = %s "
                                "WHERE application_id = %s",
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
                db_helpers.create_notification(
                    DBController,
                    f"Заявка «{_b_name}» снята с вас и возвращена в статус «Новый» "
                    f"(вы назначены на другую заявку).",
                    _b_emp, _b_id, datetime.now(project_timezone),
                )
            except Exception as e:
                print(f"[notify] bump notify failed for app={_b_id}: {e}")

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.post("/applications/{applicationId}/attachments", status_code=201, summary="Загрузить вложения к заявке",
             description="Frontend отправляет файлы на backend через multipart/form-data. Backend проверяет права, загружает файлы в S3 и сохраняет метаданные вложений в БД. Frontend не работает с S3 напрямую.",
             response_model=AttachmentUploadResponse)
async def upload_attachments(
    applicationId: int = Path(...),
    files: list[UploadFile] = File(...),
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_role = _get_user_role(login)
        ids = []

        if not s3_module.is_configured():
            raise HTTPException(status_code=503, detail="File storage is not configured")
        s3 = s3_module.get_s3()
        with db.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Заявка существует + проверка причастности: грузить файлы может только
                # автор / назначенный исполнитель / руководитель-в-scope (иначе 403).
                app_row = cur.execute(
                    """
                    SELECT a.application_id, a.department_id,
                           author_link.employee_id AS author_id,
                           exec_link.employee_id   AS executor_id,
                           dl.delegated_to          AS delegated_to
                    FROM public.application a
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
                is_author, is_exec, mgr_scope = _action_scope(db, login, app_row, user_role)
                if not (mgr_scope or is_author or is_exec):
                    raise HTTPException(status_code=403,
                                        detail="Not permitted to attach files to this application")

                for f in files:
                    content = await f.read()
                    filename     = f.filename or "upload"
                    content_type = f.content_type or "application/octet-stream"
                    s3_key = f"applications/{applicationId}/{uuid.uuid4()}-{filename}"

                    s3.put_object(
                        Bucket=s3_module.S3_BUCKET,
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
