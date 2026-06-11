"""
directories_api.py — справочники: отделы (и их настройки), сотрудники, должности,
грейды, кандидаты из AD и виды работ (вынесено из main.py при декомпозиции).
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from psycopg.rows import dict_row

from src import backup_module
from src.application_module import project_timezone
from src.core import (
    DBController, _ad_directory, _find_ad_by_id, _get_user_role, _is_top_manager,
    _raise_for_db_error, _require_department_scope, _role_ladder,
    _user_department_id, authObj, get_db_user, login_by_employee_map,
    require_manager_role, require_permission, row_or_404,
)
from src.schemas import (
    AdUserListResponse, ComplexityValues, CreateEmployeePayload,
    CreateWorkTypePayload, DepartmentListResponse, DepartmentOut,
    EmployeeListResponse, GradeListResponse, GradeOut, IdResponse,
    PositionListResponse, PositionOut, UpdateDepartmentDelegationSettingsPayload,
    UpdateDepartmentPayload, UpdateEmployeePayload, UpdateWorkTypePayload,
    WorkTypeListResponse, WorkTypeOut,
)

router = APIRouter(tags=["Directories"])


def _persist_directory_state() -> None:
    """Best-effort снимок onboarding-состояния каталога в S3 после его изменения,
    чтобы привязка логин ↔ employee_id переживала рестарт в релизном режиме
    (seed_on_start=false). Сбой снимка не ломает уже применённую операцию."""
    try:
        backup_module.save_directory_snapshot(_ad_directory())
    except Exception as e:
        print(f"[backup] directory snapshot after change failed: {e}")


@router.get("/departments", summary="Получить отделы", response_model=DepartmentListResponse)
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


@router.get("/employees", summary="Получить сотрудников, подключенных к системе",
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
        login_by_emp = login_by_employee_map()

        with db.pool.connection() as conn:
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


@router.post("/employees", status_code=201, summary="Добавить AD-пользователя в систему",
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
        # stops appearing as an addable AD candidate; the S3 snapshot makes the
        # binding survive a restart when seeding is disabled (release mode).
        ad_user["inSystem"] = True
        ad_user["employee_id"] = emp_id
        ad_user["role"] = payload.role
        _persist_directory_state()

        return {"id": str(emp_id)}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.patch("/employees/{employeeId}", status_code=204, summary="Изменить роль сотрудника или участие в распределении",
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
            with conn.cursor(row_factory=dict_row) as cur:
                if payload.role is not None:
                    # Нельзя выдать роль выше собственной (обычный руководитель не может
                    # создать top-manager → эскалация привилегий).
                    ladder = _role_ladder()
                    caller_role = _get_user_role(login)
                    if (payload.role in ladder and caller_role in ladder
                            and ladder.index(payload.role) > ladder.index(caller_role)):
                        raise HTTPException(status_code=403,
                                            detail="Cannot assign a role higher than your own")
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

        if payload.role is not None:
            # Зеркалим роль в каталог пользователей: именно из него берутся роль и
            # права при аутентификации (раньше смена роли влияла только на справочник
            # и не меняла фактические права до перезапуска).
            for _entry in _ad_directory().values():
                if _entry.get("employee_id") == int(employeeId):
                    _entry["role"] = payload.role
                    break
            _persist_directory_state()

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.delete("/employees/{employeeId}", status_code=204, summary="Удалить сотрудника из системы",
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
        # they reappear as an addable candidate in /ad/users (not deleted from AD).
        # The S3 snapshot persists this across restarts when seeding is disabled.
        for _entry in _ad_directory().values():
            if _entry.get("employee_id") == int(employeeId):
                _entry["inSystem"] = False
                _entry.pop("employee_id", None)
                _entry.pop("role", None)
                break
        _persist_directory_state()

        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.patch("/departments/{departmentId}/delegation-settings", status_code=204,
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


@router.patch("/departments/{departmentId}", status_code=204,
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


@router.get("/positions", summary="Получить должности",
            description="Должность сотрудника приходит из AD и не редактируется руководителем вручную. Соответствует таблице post.",
            response_model=PositionListResponse)
def get_positions(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        with db.pool.connection() as conn:
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


@router.get("/grades", summary="Получить грейды",
            description="Грейды используются только в матрице допустимости вида работ и не являются должностью сотрудника. Соответствует таблице grade.",
            response_model=GradeListResponse)
def get_grades(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        with db.pool.connection() as conn:
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


@router.get("/ad/users", summary="Найти пользователей AD для добавления в систему",
            description="Кандидаты из AD для добавления в систему. Требует право canManageEmployees "
                        "(руководитель/top-manager) — рядовым пользователям каталог AD не отдаётся.",
            response_model=AdUserListResponse)
def get_ad_users(
    userData=Depends(authObj.authenticate),
    query: Optional[str]        = Query(default=None),
    departmentId: Optional[str] = Query(default=None),
):
    """Returns AD people not yet onboarded into the system (addable candidates)."""
    try:
        # Каталог AD (ФИО/логины не заведённых людей) — данные для управления
        # сотрудниками; фронтенд запрашивает его только при canManageEmployees.
        require_permission(userData, "canManageEmployees")
        get_db_user(userData)
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


@router.get("/work-types", summary="Получить виды работ",
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
            with conn.cursor(row_factory=dict_row) as cur:
                data = cur.execute(query, params).fetchall()

        items = [WorkTypeOut.model_validate(r).model_dump() for r in (data or [])]
        return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.post("/work-types", status_code=201, summary="Создать вид работ",
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


@router.patch("/work-types/{workTypeId}", status_code=204, summary="Изменить вид работ, сложность или допустимые грейды",
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


@router.delete("/work-types/{workTypeId}", status_code=204, summary="Удалить вид работ",
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
