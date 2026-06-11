"""
events_module.py — подсистема событий и исполнения решений.

На каждом тике `run_tick(db)`:
  • пересчитывает `application.priority_score` открытых заявок (k_времени растёт
    со временем) — см. priority_module;
  • помечает просроченные заявки (`is_expired`) и уведомляет руководителя отдела
    и назначенного исполнителя;
  • шлёт уведомление о приближении дедлайна по всем открытым статусам (когда доля
    ОСТАВШЕГОСЯ времени <= department.deadline_notification) — руководителю отдела
    и назначенному исполнителю.

run_tick идемпотентен: повторные вызовы не шлют дубли (дедуп через
application.is_expired и application.deadline_notified). Работает под системным
соединением (DBController) — на привилегии пользователя не завязан.
Фоновый цикл, вызывающий run_tick по таймеру, поднимается в lifespan FastAPI
(main.py); следом за событиями тот же цикл запускает маршрутизацию
(routing_module.run_routing).
"""

from datetime import datetime, timezone

from psycopg.rows import dict_row

from src.db_helpers import dept_manager_ids, notify

DEFAULT_DEADLINE_NOTIFICATION = 0.25  # доля оставшегося времени, если у отдела не задано


def _assigned_executor_id(cur, app_id):
    """employee_id назначенного исполнителя заявки, или None."""
    row = cur.execute(
        "SELECT eta.employee_id FROM public.employee_to_application eta "
        "JOIN public.role r ON r.role_id = eta.role_id "
        "WHERE eta.application_id = %s AND r.name = 'executor' LIMIT 1",
        (int(app_id),),
    ).fetchone()
    return row["employee_id"] if row else None


def _recipients(cur, dept_id, app_id) -> set:
    """Получатели уведомления по заявке: руководители отдела + назначенный исполнитель."""
    ids = set(dept_manager_ids(cur, dept_id))
    exec_id = _assigned_executor_id(cur, app_id)
    if exec_id is not None:
        ids.add(exec_id)
    return ids


def run_tick(db, now=None) -> dict:
    """Один проход подсистемы событий. Возвращает счётчики выполненных действий."""
    now = now or datetime.now(timezone.utc)
    expired_count = 0
    deadline_notified_count = 0

    # 0. Пересчёт приоритета открытых заявок (k_времени растёт со временем).
    from src import priority_module
    priority_recomputed = priority_module.recompute_open(db, now)

    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # ── 1. Просроченные заявки ───────────────────────────────────────
            overdue = cur.execute(
                """
                SELECT a.application_id, a.name, a.department_id
                FROM public.application a
                JOIN public.status s ON s.status_id = a.status_id
                WHERE a.deadline IS NOT NULL
                  AND a.deadline < %s
                  AND s.name NOT IN ('completed', 'rejected')
                  AND COALESCE(a.is_expired, false) = false
                """,
                (now,),
            ).fetchall()
            for r in overdue:
                cur.execute(
                    "UPDATE public.application SET is_expired = true, updated_at = %s WHERE application_id = %s",
                    (now, r["application_id"]),
                )
                for rid in _recipients(cur, r["department_id"], r["application_id"]):
                    notify(cur, f"Заявка «{r['name']}» просрочена.",
                           rid, r["application_id"], now)
                expired_count += 1

            # ── 2. Приближение дедлайна (все открытые статусы, ещё не просрочена) ─
            # Уведомляем и руководителя отдела, и назначенного исполнителя (если есть).
            approaching = cur.execute(
                """
                SELECT a.application_id, a.name, a.department_id,
                       a.created_at, a.deadline, d.deadline_notification
                FROM public.application a
                JOIN public.status s ON s.status_id = a.status_id
                JOIN public.department d ON d.department_id = a.department_id
                WHERE s.name IN ('new', 'assigned', 'inProgress', 'delegated')
                  AND a.deadline IS NOT NULL AND a.created_at IS NOT NULL
                  AND a.deadline > a.created_at
                  AND a.deadline > %s
                  AND COALESCE(a.deadline_notified, false) = false
                """,
                (now,),
            ).fetchall()
            for r in approaching:
                total = (r["deadline"] - r["created_at"]).total_seconds()
                remaining = (r["deadline"] - now).total_seconds()
                ratio = remaining / total if total > 0 else 0.0
                threshold = (r["deadline_notification"]
                             if r["deadline_notification"] is not None
                             else DEFAULT_DEADLINE_NOTIFICATION)
                if ratio <= threshold:
                    pct = max(0, int(round(ratio * 100)))
                    for rid in _recipients(cur, r["department_id"], r["application_id"]):
                        notify(cur,
                               f"По заявке «{r['name']}» истекает срок (осталось ~{pct}% времени).",
                               rid, r["application_id"], now)
                    cur.execute(
                        "UPDATE public.application SET deadline_notified = true, updated_at = %s "
                        "WHERE application_id = %s",
                        (now, r["application_id"]),
                    )
                    deadline_notified_count += 1

    return {"expired": expired_count, "deadlineNotifications": deadline_notified_count,
            "priorityRecomputed": priority_recomputed}
