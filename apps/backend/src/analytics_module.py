"""
analytics_module.py — подсистема аналитики (docs/backend-functions.md §4).

Чистая вычислительная логика: функции принимают PgDbOperator (соединение текущего
пользователя), scope по отделу (None = все отделы, для top-manager) и необязательный
период [dt_from, dt_to] по дате создания заявки. Авторизация/scope считаются в main.py.

Источники метрик:
  • временные поля заявки (created_at, executor_at=assignedAt, work_at=startedAt,
    finished_at) — для времени текущего цикла (реагирование, обработка, выполнение);
  • журнал public.application_status_history — для времени по статусам и времени без
    исполнителя (учитывает повторные циклы new→assigned→new);
  • public.delegated — для статистики делегирования.

Полный контракт полей — docs/analytics-contract.md.

ПОКА НЕ РЕАЛИЗОВАНО (требует помодельного таймлайна занятости сотрудника с обрезкой по
окну периода): время простоя сотрудника и доля занятости (idle/occupancy). Поля
зарезервированы как null и помечены в контракт-доке.
"""

from psycopg.rows import dict_row

_COMPLEXITY = {1: "easy", 2: "medium", 3: "hard", 4: "critical"}
_PRIORITIES = ("low", "medium", "high", "critical")
_COMPLEXITIES = ("easy", "medium", "hard", "critical")


def _n(v):
    return round(float(v), 2) if v is not None else None


def _stat(row, prefix):
    return {"min": _n(row[f"{prefix}_min"]), "avg": _n(row[f"{prefix}_avg"]), "max": _n(row[f"{prefix}_max"])}


def _app_where(alias, department_id, dt_from, dt_to):
    conds, params = [], []
    if department_id is not None:
        conds.append(f"{alias}.department_id = %s"); params.append(int(department_id))
    if dt_from:
        conds.append(f"{alias}.created_at >= %s"); params.append(dt_from)
    if dt_to:
        conds.append(f"{alias}.created_at <= %s"); params.append(dt_to)
    return conds, params


def _and(conds):
    return (" AND " + " AND ".join(conds)) if conds else ""


def _meta(department_id, dt_from, dt_to):
    return {
        "scope": "all" if department_id is None else "department",
        "departmentId": None if department_id is None else str(department_id),
        "period": None if not (dt_from or dt_to) else {"from": dt_from, "to": dt_to},
    }


def _time_per_status(cur, app_where, app_params):
    """Среднее/мин/макс время, проведённое заявками в каждом статусе, по журналу
    переходов (сегмент = время от входа в статус до следующего перехода)."""
    rows = cur.execute(
        f"""
        WITH seg AS (
            SELECT h.application_id, s2.name AS status_name, h.changed_at,
                   LEAD(h.changed_at) OVER (PARTITION BY h.application_id
                                            ORDER BY h.changed_at, h.id) AS next_at
            FROM public.application_status_history h
            JOIN public.status s2 ON s2.status_id = h.to_status_id
            WHERE h.application_id IN (
                SELECT a.application_id FROM public.application a WHERE 1=1{app_where}
            )
        )
        SELECT status_name,
               MIN(EXTRACT(EPOCH FROM (next_at - changed_at))) AS d_min,
               AVG(EXTRACT(EPOCH FROM (next_at - changed_at))) AS d_avg,
               MAX(EXTRACT(EPOCH FROM (next_at - changed_at))) AS d_max
        FROM seg WHERE next_at IS NOT NULL
        GROUP BY status_name
        """,
        app_params,
    ).fetchall()
    return {r["status_name"]: _stat(r, "d") for r in rows}


# ─────────────────────────── Applications ───────────────────────────

def applications_stats(db, department_id=None, dt_from=None, dt_to=None) -> dict:
    conds, params = _app_where("a", department_id, dt_from, dt_to)
    where = _and(conds)
    out = _meta(department_id, dt_from, dt_to)

    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            out["total"] = cur.execute(
                f"SELECT COUNT(*) AS c FROM public.application a WHERE 1=1{where}", params
            ).fetchone()["c"]

            out["byStatus"] = {
                r["name"]: r["c"] for r in cur.execute(
                    f"""SELECT s.name, COUNT(*) AS c FROM public.application a
                        JOIN public.status s ON s.status_id = a.status_id
                        WHERE 1=1{where} GROUP BY s.name""", params).fetchall()
            }
            out["byPriority"] = {
                r["name"]: r["c"] for r in cur.execute(
                    f"""SELECT p.name, COUNT(*) AS c FROM public.application a
                        JOIN public.priority p ON p.priority_id = a.priority_id
                        WHERE 1=1{where} GROUP BY p.name""", params).fetchall()
            }
            comp_rows = cur.execute(
                f"""SELECT COALESCE(a.empl_assigned_complexity, t.complexity_value) AS cv, COUNT(*) AS c
                    FROM public.application a
                    LEFT JOIN public.types_of_works t ON t.type_of_works_id = a.types_of_works
                    WHERE 1=1{where}
                    GROUP BY COALESCE(a.empl_assigned_complexity, t.complexity_value)""",
                params).fetchall()
            by_complexity = {k: 0 for k in _COMPLEXITIES}
            for r in comp_rows:
                key = _COMPLEXITY.get(r["cv"])
                if key:
                    by_complexity[key] += r["c"]
            out["byComplexity"] = by_complexity

            # Время выполнения (завершённые): finished_at − created_at.
            row = cur.execute(
                f"""SELECT MIN(EXTRACT(EPOCH FROM (a.finished_at - a.created_at))) AS d_min,
                           AVG(EXTRACT(EPOCH FROM (a.finished_at - a.created_at))) AS d_avg,
                           MAX(EXTRACT(EPOCH FROM (a.finished_at - a.created_at))) AS d_max
                    FROM public.application a JOIN public.status s ON s.status_id = a.status_id
                    WHERE s.name = 'completed' AND a.finished_at IS NOT NULL{where}""", params).fetchone()
            out["completionTimeSeconds"] = _stat(row, "d")

            # Время до первого назначения (журнал).
            row = cur.execute(
                f"""WITH first_assign AS (
                        SELECT h.application_id, MIN(h.changed_at) AS assigned_at
                        FROM public.application_status_history h
                        JOIN public.status s ON s.status_id = h.to_status_id
                        WHERE s.name = 'assigned' GROUP BY h.application_id)
                    SELECT MIN(EXTRACT(EPOCH FROM (fa.assigned_at - a.created_at))) AS d_min,
                           AVG(EXTRACT(EPOCH FROM (fa.assigned_at - a.created_at))) AS d_avg,
                           MAX(EXTRACT(EPOCH FROM (fa.assigned_at - a.created_at))) AS d_max
                    FROM first_assign fa JOIN public.application a ON a.application_id = fa.application_id
                    WHERE 1=1{where}""", params).fetchone()
            out["timeToAssignSeconds"] = _stat(row, "d")

            # Время «без исполнителя» = суммарное время в статусе `new` на заявку
            # (покрывает и распределение, и перераспределение — повторные циклы).
            row = cur.execute(
                f"""WITH seg AS (
                        SELECT h.application_id, s2.name AS status_name, h.changed_at,
                               LEAD(h.changed_at) OVER (PARTITION BY h.application_id
                                                        ORDER BY h.changed_at, h.id) AS next_at
                        FROM public.application_status_history h
                        JOIN public.status s2 ON s2.status_id = h.to_status_id
                        WHERE h.application_id IN (SELECT a.application_id FROM public.application a WHERE 1=1{where})),
                    per_app AS (
                        SELECT application_id, SUM(EXTRACT(EPOCH FROM (next_at - changed_at))) AS secs
                        FROM seg WHERE next_at IS NOT NULL AND status_name = 'new'
                        GROUP BY application_id)
                    SELECT MIN(secs) AS d_min, AVG(secs) AS d_avg, MAX(secs) AS d_max FROM per_app""",
                params).fetchone()
            out["timeWithoutExecutorSeconds"] = _stat(row, "d")

            # Среднее время в каждом статусе.
            out["timePerStatusSeconds"] = _time_per_status(cur, where, params)

            # Делегирования по заявкам в scope.
            drow = cur.execute(
                f"""SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE d.decision = 'confirmed') AS confirmed,
                           COUNT(*) FILTER (WHERE d.decision = 'declined')  AS declined,
                           COUNT(*) FILTER (WHERE d.decision IS NULL)       AS pending,
                           MIN(EXTRACT(EPOCH FROM (d.decided_at - d.created_at))) AS d_min,
                           AVG(EXTRACT(EPOCH FROM (d.decided_at - d.created_at))) AS d_avg,
                           MAX(EXTRACT(EPOCH FROM (d.decided_at - d.created_at))) AS d_max
                    FROM public.delegated d
                    JOIN public.application a ON a.application_id = d.application_id
                    WHERE 1=1{where}""", params).fetchone()
            out["delegations"] = {
                "total": drow["total"], "confirmed": drow["confirmed"],
                "declined": drow["declined"], "pending": drow["pending"],
                "decisionTimeSeconds": _stat(drow, "d"),
            }
    return out


# ─────────────────────────── Executors ───────────────────────────

def executors_stats(db, department_id=None, dt_from=None, dt_to=None) -> dict:
    out = _meta(department_id, dt_from, dt_to)
    dep_cond = " AND e.department_id = %s" if department_id is not None else ""
    dep_param = [int(department_id)] if department_id is not None else []

    period, pparams = [], []
    if dt_from:
        period.append("a.created_at >= %s"); pparams.append(dt_from)
    if dt_to:
        period.append("a.created_at <= %s"); pparams.append(dt_to)
    pwhere = _and(period)

    exec_role_sub = "(SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)"

    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Снимок + времена текущего цикла по заявкам, где сотрудник — исполнитель.
            base_rows = cur.execute(
                f"""
                SELECT e.employee_id, e.fio, e.department_id,
                       COUNT(*) AS assigned_count,
                       COUNT(*) FILTER (WHERE s.name = 'completed')  AS completed_count,
                       COUNT(*) FILTER (WHERE s.name = 'inProgress') AS in_progress_count,
                       COUNT(*) FILTER (WHERE p.name = 'low')      AS prio_low,
                       COUNT(*) FILTER (WHERE p.name = 'medium')   AS prio_medium,
                       COUNT(*) FILTER (WHERE p.name = 'high')     AS prio_high,
                       COUNT(*) FILTER (WHERE p.name = 'critical') AS prio_critical,
                       AVG(EXTRACT(EPOCH FROM (a.work_at - a.executor_at)))
                           FILTER (WHERE a.work_at IS NOT NULL AND a.executor_at IS NOT NULL) AS reaction_avg,
                       AVG(EXTRACT(EPOCH FROM (a.finished_at - a.work_at)))
                           FILTER (WHERE s.name = 'completed' AND a.work_at IS NOT NULL) AS handling_avg,
                       SUM(EXTRACT(EPOCH FROM (a.finished_at - a.work_at)))
                           FILTER (WHERE s.name = 'completed' AND a.work_at IS NOT NULL) AS work_total
                FROM public.employee e
                JOIN public.role r ON r.role_id = e.role_id AND r.name = 'executor'
                JOIN public.employee_to_application eta
                     ON eta.employee_id = e.employee_id AND eta.role_id = {exec_role_sub}
                JOIN public.application a ON a.application_id = eta.application_id
                JOIN public.status s ON s.status_id = a.status_id
                LEFT JOIN public.priority p ON p.priority_id = a.priority_id
                WHERE 1=1{pwhere}{dep_cond}
                GROUP BY e.employee_id, e.fio, e.department_id
                """,
                pparams + dep_param,
            ).fetchall()

            # Счётчики действий из журнала (актор = by_employee_id), период по changed_at.
            ah_period, ah_params = [], []
            if dt_from:
                ah_period.append("h.changed_at >= %s"); ah_params.append(dt_from)
            if dt_to:
                ah_period.append("h.changed_at <= %s"); ah_params.append(dt_to)
            action_rows = cur.execute(
                f"""
                SELECT h.by_employee_id AS emp, h.reason, COUNT(*) AS c
                FROM public.application_status_history h
                JOIN public.employee e ON e.employee_id = h.by_employee_id
                WHERE h.by_employee_id IS NOT NULL{_and(ah_period)}{dep_cond}
                GROUP BY h.by_employee_id, h.reason
                """,
                ah_params + dep_param,
            ).fetchall()
            actions = {}
            for r in action_rows:
                actions.setdefault(r["emp"], {})[r["reason"]] = r["c"]

    executors = []
    for r in base_rows:
        a = actions.get(r["employee_id"], {})
        executors.append({
            "employeeId": str(r["employee_id"]),
            "fullName": r["fio"] or "",
            "departmentId": str(r["department_id"]) if r["department_id"] is not None else None,
            "assignedCount": r["assigned_count"],
            "completedCount": r["completed_count"],
            "inProgressCount": r["in_progress_count"],
            "takenInWorkCount": a.get("startWork", 0),
            "rejectedCount": a.get("reject", 0),
            "delegatedCount": a.get("delegateInternal", 0) + a.get("delegateExternal", 0),
            "byPriority": {"low": r["prio_low"], "medium": r["prio_medium"],
                           "high": r["prio_high"], "critical": r["prio_critical"]},
            "avgReactionTimeSeconds": _n(r["reaction_avg"]),
            "avgHandlingTimeSeconds": _n(r["handling_avg"]),
            "totalWorkSeconds": _n(r["work_total"]),
            "idleTimeSeconds": None,      # см. docs/analytics-contract.md (планируется)
            "occupancyRatio": None,       # см. docs/analytics-contract.md (планируется)
        })
    out["executors"] = executors
    return out


# ─────────────────────────── Work types ───────────────────────────

def work_types_stats(db, department_id=None, dt_from=None, dt_to=None) -> dict:
    out = _meta(department_id, dt_from, dt_to)
    join_conds, join_params = [], []
    if dt_from:
        join_conds.append("a.created_at >= %s"); join_params.append(dt_from)
    if dt_to:
        join_conds.append("a.created_at <= %s"); join_params.append(dt_to)
    join_extra = (" AND " + " AND ".join(join_conds)) if join_conds else ""

    where, where_params = "", []
    if department_id is not None:
        where = " AND t.department_id = %s"; where_params = [int(department_id)]

    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"""
                SELECT t.type_of_works_id, t.name, t.department_id,
                       COUNT(a.application_id) AS created_count,
                       COUNT(a.application_id) FILTER (WHERE s.name = 'completed') AS completed_count,
                       COUNT(a.application_id) FILTER (WHERE a.delegated_id IS NOT NULL) AS delegated_count,
                       COUNT(a.application_id) FILTER (WHERE p.name = 'low')      AS prio_low,
                       COUNT(a.application_id) FILTER (WHERE p.name = 'medium')   AS prio_medium,
                       COUNT(a.application_id) FILTER (WHERE p.name = 'high')     AS prio_high,
                       COUNT(a.application_id) FILTER (WHERE p.name = 'critical') AS prio_critical,
                       AVG(EXTRACT(EPOCH FROM (a.finished_at - a.created_at)))
                           FILTER (WHERE s.name = 'completed' AND a.finished_at IS NOT NULL) AS completion_avg
                FROM public.types_of_works t
                LEFT JOIN public.application a
                       ON a.types_of_works = t.type_of_works_id{join_extra}
                LEFT JOIN public.status s ON s.status_id = a.status_id
                LEFT JOIN public.priority p ON p.priority_id = a.priority_id
                WHERE 1=1{where}
                GROUP BY t.type_of_works_id, t.name, t.department_id
                ORDER BY t.type_of_works_id
                """,
                join_params + where_params,
            ).fetchall()

            # Самый частый исполнитель по виду работ.
            top_rows = cur.execute(
                f"""
                WITH cnt AS (
                    SELECT a.types_of_works AS tow, eta.employee_id,
                           COUNT(*) AS c,
                           ROW_NUMBER() OVER (PARTITION BY a.types_of_works ORDER BY COUNT(*) DESC) AS rn
                    FROM public.application a
                    JOIN public.employee_to_application eta
                         ON eta.application_id = a.application_id
                        AND eta.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
                    WHERE 1=1{join_extra}
                    GROUP BY a.types_of_works, eta.employee_id)
                SELECT c.tow, c.employee_id, e.fio
                FROM cnt c JOIN public.employee e ON e.employee_id = c.employee_id
                WHERE c.rn = 1
                """,
                join_params,
            ).fetchall()
            top_by_tow = {r["tow"]: (str(r["employee_id"]), r["fio"] or "") for r in top_rows}

    out["workTypes"] = [
        {
            "workTypeId": str(r["type_of_works_id"]),
            "name": r["name"] or "",
            "departmentId": str(r["department_id"]) if r["department_id"] is not None else None,
            "createdCount": r["created_count"],
            "completedCount": r["completed_count"],
            "delegatedCount": r["delegated_count"],
            "byPriority": {"low": r["prio_low"], "medium": r["prio_medium"],
                           "high": r["prio_high"], "critical": r["prio_critical"]},
            "avgCompletionTimeSeconds": _n(r["completion_avg"]),
            "topExecutorId": top_by_tow.get(r["type_of_works_id"], (None, None))[0],
            "topExecutorName": top_by_tow.get(r["type_of_works_id"], (None, None))[1],
        }
        for r in rows
    ]
    return out


# ─────────────────────────── Departments ───────────────────────────

def departments_stats(db, department_id=None, dt_from=None, dt_to=None) -> dict:
    out = _meta(department_id, dt_from, dt_to)
    dep_cond = " AND d.department_id = %s" if department_id is not None else ""
    dep_param = [int(department_id)] if department_id is not None else []

    ap, app_params = [], []
    if dt_from:
        ap.append("a.created_at >= %s"); app_params.append(dt_from)
    if dt_to:
        ap.append("a.created_at <= %s"); app_params.append(dt_to)
    apw = _and(ap)

    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"""
                SELECT d.department_id, d.name,
                    (SELECT COUNT(*) FROM public.employee e
                      WHERE e.department_id = d.department_id AND e.is_active = true) AS employee_count,
                    (SELECT COUNT(*) FROM public.application a
                      WHERE a.department_id = d.department_id{apw}) AS application_count,
                    (SELECT COUNT(*) FROM public.application a
                      JOIN public.status s ON s.status_id = a.status_id
                      WHERE a.department_id = d.department_id AND s.name = 'completed'{apw}) AS completed_count,
                    (SELECT AVG(EXTRACT(EPOCH FROM (a.work_at - a.executor_at))) FROM public.application a
                      WHERE a.department_id = d.department_id
                        AND a.work_at IS NOT NULL AND a.executor_at IS NOT NULL{apw}) AS reaction_avg,
                    (SELECT COUNT(*) FROM public.delegated dl
                      WHERE dl.delegated_from = d.department_id::text) AS delegations_sent,
                    (SELECT COUNT(*) FROM public.delegated dl
                      WHERE dl.delegated_to = d.department_id::text) AS delegations_received
                FROM public.department d
                WHERE 1=1{dep_cond}
                ORDER BY d.department_id
                """,
                app_params + app_params + app_params + dep_param,
            ).fetchall()

    out["departments"] = [
        {
            "departmentId": str(r["department_id"]),
            "name": r["name"] or "",
            "employeeCount": r["employee_count"],
            "applicationCount": r["application_count"],
            "completedCount": r["completed_count"],
            "avgReactionTimeSeconds": _n(r["reaction_avg"]),
            "idleTimeSeconds": None,    # см. docs/analytics-contract.md (планируется)
            "occupancyRatio": None,     # см. docs/analytics-contract.md (планируется)
            "delegations": {"sent": r["delegations_sent"], "received": r["delegations_received"]},
        }
        for r in rows
    ]
    return out
