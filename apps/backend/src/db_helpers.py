"""
db_helpers.py — маленькие общие SQL-хелперы, нужные сразу нескольким подсистемам
(управление заявками, события, маршрутизация): уведомления, руководители отдела,
журнал переходов статусов. Раньше каждая подсистема держала свою копию.

Зависимостей от остального backend нет (только psycopg) — модуль можно импортировать
из events_module/routing_module, которые тестируются отдельно от приложения.
"""

from psycopg.rows import dict_row


def notify(cur, text, employee_id, application_id, at) -> None:
    """Вставить одно уведомление через переданный курсор (в текущей транзакции)."""
    if employee_id is None:
        return
    cur.execute(
        "INSERT INTO public.notification (text, created_at, employee_id, is_read, application_id) "
        "VALUES (%s, %s, %s, false, %s)",
        (text, at, int(employee_id), int(application_id)),
    )


def create_notification(db, text, employee_id, application_id, at) -> None:
    """Вставить одно уведомление через пул `db` (отдельная транзакция).

    Используется ПОСЛЕ коммита действия (best-effort из подсистемы управления) под
    системным соединением, чтобы не зависеть от табличных привилегий пользователя.
    """
    if employee_id is None:
        return
    with db.pool.connection() as conn:
        conn.execute(
            "INSERT INTO public.notification (text, created_at, employee_id, is_read, application_id) "
            "VALUES (%s, %s, %s, false, %s)",
            (text, at, int(employee_id), int(application_id)),
        )


def dept_manager_ids(cur, dept_id) -> list:
    """Активные руководители/топ-менеджеры отдела — получатели уведомлений."""
    if dept_id is None:
        return []
    rows = cur.execute(
        "SELECT e.employee_id FROM public.employee e "
        "JOIN public.role r ON r.role_id = e.role_id "
        "WHERE e.department_id = %s AND r.name IN ('manager', 'top-manager') "
        "AND e.is_active = true",
        (int(dept_id),),
    ).fetchall()
    return [r["employee_id"] for r in rows]


def department_manager_ids(db, dept_id) -> list:
    """То же, что dept_manager_ids, но через пул `db` (для пост-коммитных уведомлений)."""
    if dept_id is None:
        return []
    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return dept_manager_ids(cur, dept_id)


def record_status_change(cur, application_id, from_status_id, to_status_id,
                         by_employee_id, reason, at) -> None:
    """Append a row to public.application_status_history.

    Called inside the same transaction/cursor as the status change so the journal
    stays consistent with application state. `from_status_id` is None on creation;
    `by_employee_id` is None when the system (routing) changed the status.
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
