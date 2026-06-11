"""
routing_module.py — подсистема принятия решений и маршрутизации (см. backend-functions §3).

`run_routing(db)` делает один проход распределения заявок в статусе `new`, по убыванию
приоритета (`priority_score`). Правила:

  • Обычная (не критичная) заявка: назначается свободному подходящему исполнителю
    («минимально способному»); если свободных нет — остаётся в `new` (не вытесняет никого
    и автоматически не перераспределяется), а руководителю отдела уходит уведомление
    «нет свободных подходящих исполнителей» (однократно на заявку).
  • Критичная заявка: назначается свободному; если свободных нет — вытесняет у подходящего
    исполнителя НЕ критичную заявку с наименьшим приоритетом (та возвращается в `new` с
    признаком «Незавершённая»); если вытеснять некого — остаётся в `new` и руководителю
    уходит уведомление-эскалация (однократно).

  Дедуп уведомлений «не удалось распределить» — флаг application.escalation_notified,
  сбрасывается при назначении (вручную флаг не сбрасывается — повторное уведомление
  придёт только после нового цикла назначение → возврат в `new`).

«Свободный подходящий исполнитель»: активен, роль executor, его отдел = отделу заявки,
грейд входит в матрицу вида работ, нет активной заявки (assigned/inProgress) и нет
внутреннего делегирования, ждущего подтверждения руководителя (при отклонении такая
заявка вернётся исполнителю), и прошёл кулдаун `empl_appl_delay` после последней
завершённой заявки (кулдаун соблюдают все, в т.ч. критичные). «Минимально способный» —
наименьший грейд; при равенстве — дольше всех простаивавший.

Запускается фоновым циклом (lifespan) ОТДЕЛЬНО от events.run_tick. Работает под системным
соединением (DBController). Всё (назначение/вытеснение/журнал/уведомления) — в одной
транзакции прохода.

Помимо периодического полного прохода, `run_routing(db, only_application_id=...)` вызывается
из create_application сразу при создании КРИТИЧНОЙ заявки — немедленное распределение одной
заявки, без ожидания тика (см. параметр only_application_id).
"""

from datetime import datetime, timezone, timedelta

from psycopg.rows import dict_row

from src.db_helpers import dept_manager_ids, notify, record_status_change

# Произвольный, но фиксированный ключ для transaction-level advisory lock,
# сериализующего проходы run_routing между собой (фон + немедленный триггер).
_ROUTING_LOCK_KEY = 727274


def _role_id(cur, name):
    r = cur.execute("SELECT role_id FROM public.role WHERE name = %s LIMIT 1", (name,)).fetchone()
    return r["role_id"] if r else None


def _status_id(cur, name):
    r = cur.execute("SELECT status_id FROM public.status WHERE name = %s LIMIT 1", (name,)).fetchone()
    return r["status_id"] if r else None


def _allowed_grade_ids(cur, work_type_id):
    if work_type_id is None:
        return []
    rows = cur.execute(
        "SELECT grade_id FROM public.type_of_work_to_grade WHERE type_of_works_id = %s",
        (work_type_id,),
    ).fetchall()
    return [r["grade_id"] for r in rows]


def _dept_delay_minutes(cur, dept_id):
    r = cur.execute(
        "SELECT empl_appl_delay FROM public.department WHERE department_id = %s", (dept_id,)
    ).fetchone()
    return (r["empl_appl_delay"] if r and r["empl_appl_delay"] is not None else 0)


def _journal(cur, application_id, from_sid, to_sid, reason, at):
    # Маршрутизация действует от имени системы → by_employee_id = NULL.
    record_status_change(cur, application_id, from_sid, to_sid, None, reason, at)


def _free_candidates(cur, exec_role_id, dept_id, allowed_grades, delay_minutes, now):
    """Свободные подходящие исполнители, отсортированные «минимально способный» вперёд."""
    if not allowed_grades:
        return []
    rows = cur.execute(
        """
        SELECT e.employee_id, g.grade_id,
               (SELECT MAX(a3.finished_at)
                FROM public.employee_to_application eta3
                JOIN public.application a3 ON a3.application_id = eta3.application_id
                JOIN public.status s3 ON s3.status_id = a3.status_id
                WHERE eta3.employee_id = e.employee_id AND eta3.role_id = %(exec)s
                  AND s3.name = 'completed') AS last_finish
        FROM public.employee e
        JOIN public.post_grade pg ON pg.post_grade_id = e.post_grade_id
        JOIN public.grade g ON g.grade_id = pg.grade_grade_id
        WHERE e.is_active = true
          AND e.department_id = %(dept)s
          AND e.role_id = %(exec)s
          AND g.grade_id = ANY(%(allowed)s)
          AND NOT EXISTS (
              SELECT 1 FROM public.employee_to_application eta2
              JOIN public.application a2 ON a2.application_id = eta2.application_id
              JOIN public.status s2 ON s2.status_id = a2.status_id
              LEFT JOIN public.delegated d2 ON d2.delegated_id = a2.delegated_id
              WHERE eta2.employee_id = e.employee_id AND eta2.role_id = %(exec)s
                AND (s2.name IN ('assigned', 'inProgress')
                     -- Внутреннее делегирование, ждущее подтверждения руководителя:
                     -- при отклонении заявка ВЕРНЁТСЯ этому исполнителю (assigned/
                     -- inProgress), поэтому до решения он считается занятым — иначе
                     -- авто-назначение могло бы дать ему вторую активную заявку.
                     OR (s2.name = 'delegated'
                         AND d2.delegated_from = d2.delegated_to)))
        """,
        {"exec": exec_role_id, "dept": dept_id, "allowed": list(allowed_grades)},
    ).fetchall()

    cutoff = now - timedelta(minutes=delay_minutes or 0)
    _min_dt = datetime.min.replace(tzinfo=timezone.utc)
    free = [r for r in rows if r["last_finish"] is None or r["last_finish"] <= cutoff]
    # Минимально способный: меньший грейд вперёд; при равенстве — дольше простаивавший
    # (last_finish раньше; никогда не назначавшиеся — в начало).
    free.sort(key=lambda r: (r["grade_id"], r["last_finish"] or _min_dt))
    return free


def _evictable(cur, exec_role_id, dept_id, allowed_grades):
    """Подходящий исполнитель с активной НЕ критичной заявкой наименьшего приоритета."""
    if not allowed_grades:
        return None
    return cur.execute(
        """
        SELECT e.employee_id, a.application_id AS victim_app, a.status_id AS victim_status_id,
               a.name AS victim_name, a.priority_score
        FROM public.employee e
        JOIN public.post_grade pg ON pg.post_grade_id = e.post_grade_id
        JOIN public.grade g ON g.grade_id = pg.grade_grade_id
        JOIN public.employee_to_application eta ON eta.employee_id = e.employee_id AND eta.role_id = %(exec)s
        JOIN public.application a ON a.application_id = eta.application_id
        JOIN public.status s ON s.status_id = a.status_id AND s.name IN ('assigned', 'inProgress')
        JOIN public.priority p ON p.priority_id = a.priority_id
        WHERE e.is_active = true AND e.department_id = %(dept)s AND e.role_id = %(exec)s
          AND g.grade_id = ANY(%(allowed)s) AND p.name <> 'critical'
        ORDER BY a.priority_score ASC NULLS FIRST
        LIMIT 1
        """,
        {"exec": exec_role_id, "dept": dept_id, "allowed": list(allowed_grades)},
    ).fetchone()


def _assign(cur, exec_role_id, application_id, from_sid, assigned_sid, employee_id, now, reason):
    cur.execute(
        "UPDATE public.application SET status_id = %s, executor_at = %s, updated_at = %s, "
        "is_unfinished = false, escalation_notified = false WHERE application_id = %s",
        (assigned_sid, now, now, int(application_id)),
    )
    cur.execute(
        "DELETE FROM public.employee_to_application WHERE application_id = %s AND role_id = %s",
        (int(application_id), exec_role_id),
    )
    cur.execute(
        "INSERT INTO public.employee_to_application (role_id, application_id, employee_id) VALUES (%s, %s, %s)",
        (exec_role_id, int(application_id), int(employee_id)),
    )
    _journal(cur, application_id, from_sid, assigned_sid, reason, now)


def run_routing(db, now=None, only_application_id=None) -> dict:
    """Один проход распределения `new`-заявок по убыванию приоритета.

    `only_application_id` (необязательно) — обработать ТОЛЬКО одну заявку (если она в
    статусе `new`), не трогая остальную очередь. Используется для немедленного
    распределения критичной заявки сразу при создании (без ожидания фонового тика);
    переиспользует ту же логику назначения/вытеснения/эскалации, что и полный проход.
    """
    now = now or datetime.now(timezone.utc)
    assigned = evicted = escalated = 0

    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Сериализуем проходы маршрутизации (фоновый цикл + немедленный триггер
            # критичной заявки могут идти параллельно на разных соединениях) — иначе два
            # прохода могут назначить одного свободного исполнителя на две заявки и нарушить
            # инвариант «одна заявка/исполнитель». Lock держится до конца транзакции прохода.
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (_ROUTING_LOCK_KEY,))
            exec_role = _role_id(cur, "executor")
            new_sid = _status_id(cur, "new")
            assigned_sid = _status_id(cur, "assigned")
            if exec_role is None or new_sid is None or assigned_sid is None:
                return {"assigned": 0, "evicted": 0, "escalated": 0}

            one_filter = " AND a.application_id = %s" if only_application_id is not None else ""
            apps = cur.execute(
                f"""
                SELECT a.application_id, a.name, a.department_id, a.types_of_works,
                       a.priority_score, a.escalation_notified, p.name AS priority_name
                FROM public.application a
                JOIN public.status s ON s.status_id = a.status_id AND s.name = 'new'
                LEFT JOIN public.priority p ON p.priority_id = a.priority_id
                WHERE 1=1{one_filter}
                ORDER BY a.priority_score DESC NULLS LAST
                """,
                (int(only_application_id),) if only_application_id is not None else None,
            ).fetchall()

            for app in apps:
                app_id = app["application_id"]
                dept = app["department_id"]
                wt = app["types_of_works"]
                if dept is None or wt is None:
                    continue
                allowed = _allowed_grade_ids(cur, wt)
                is_critical = (app["priority_name"] == "critical")

                free = _free_candidates(cur, exec_role, dept, allowed,
                                        _dept_delay_minutes(cur, dept), now) if allowed else []
                if free:
                    emp = free[0]["employee_id"]
                    _assign(cur, exec_role, app_id, new_sid, assigned_sid, emp, now, "auto_assign")
                    notify(cur, f"Вам назначена заявка: «{app['name']}».", emp, app_id, now)
                    assigned += 1
                    continue

                if is_critical and allowed:
                    # Критичная: вытеснение наименее приоритетной НЕ критичной заявки.
                    victim = _evictable(cur, exec_role, dept, allowed)
                    if victim:
                        emp = victim["employee_id"]
                        cur.execute(
                            "UPDATE public.application SET status_id = %s, is_unfinished = true, "
                            "previous_executor_id = %s, updated_at = %s WHERE application_id = %s",
                            (new_sid, emp, now, victim["victim_app"]),
                        )
                        _journal(cur, victim["victim_app"], victim["victim_status_id"], new_sid,
                                 "critical_evict", now)
                        _assign(cur, exec_role, app_id, new_sid, assigned_sid, emp, now, "auto_assign")
                        notify(cur, f"Вам назначена критичная заявка: «{app['name']}».", emp, app_id, now)
                        notify(cur, f"Заявка «{victim['victim_name']}» снята с вас под критичную и "
                                    f"возвращена в «Новый».", emp, victim["victim_app"], now)
                        for mid in dept_manager_ids(cur, dept):
                            notify(cur, f"Критичная заявка «{app['name']}» вытеснила заявку "
                                        f"«{victim['victim_name']}».", mid, app_id, now)
                        evicted += 1
                        assigned += 1
                        continue

                # Заявку некому отдать: свободных подходящих исполнителей нет, а вытеснение
                # невозможно (не критичная / все заняты критичными / нет подходящих по
                # грейду). Уведомляем руководителя отдела — однократно на заявку
                # (дедуп через escalation_notified; сбрасывается при назначении).
                if not app["escalation_notified"]:
                    if is_critical:
                        text = (f"Критичную заявку «{app['name']}» не удалось распределить "
                                f"автоматически — требуется ручное назначение.")
                    else:
                        text = (f"Заявку «{app['name']}» не удалось распределить автоматически: "
                                f"нет свободных подходящих исполнителей.")
                    for mid in dept_manager_ids(cur, dept):
                        notify(cur, text, mid, app_id, now)
                    cur.execute(
                        "UPDATE public.application SET escalation_notified = true WHERE application_id = %s",
                        (app_id,),
                    )
                    escalated += 1

    return {"assigned": assigned, "evicted": evicted, "escalated": escalated}
