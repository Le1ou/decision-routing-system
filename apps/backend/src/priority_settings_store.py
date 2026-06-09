"""
priority_settings_store.py — персистентное хранилище коэффициентов расчёта приоритета.

Заменяет прежний in-memory dict в main.py (терялся при рестарте). Контракт ответа
API не меняется: {"department": {dep_id: коэф}, "managerAuthor": {dep_id: коэф},
"deadline": число}. Данные лежат в одной строке таблицы public.priority_settings (id=1).

Запись выполняется только в PUT /priority-settings (top-manager). Чтение НЕ пишет в БД:
если строки ещё нет или для отдела не задан коэффициент — подставляется значение по
умолчанию 0.2 (как в прежней in-memory реализации), но в БД это не сохраняется.
"""

from psycopg.types.json import Json

DEFAULT_COEFF = 0.2
# Вес фактора срока по умолчанию = 1.0: время до дедлайна считается «в полную силу»
# (k_времени = deadlinePressure · deadline, deadlinePressure ∈ [0,1]).
DEFAULT_DEADLINE = 1.0


def _load_row(db):
    """Прочитать строку настроек (id=1) или вернуть None, если её ещё нет."""
    rows = db.getRowFromTable("priority_settings", "id", 1)
    return rows[0] if rows else None


def load_effective(db) -> dict:
    """Эффективные настройки: строка из БД (если есть), дополненная значениями по
    умолчанию для каждого известного отдела. В БД ничего не пишет."""
    row = _load_row(db)
    department = dict(row["department"]) if row and row.get("department") else {}
    manager_author = dict(row["manager_author"]) if row and row.get("manager_author") else {}
    deadline = float(row["deadline"]) if row and row.get("deadline") is not None else DEFAULT_DEADLINE

    deps = db.getAllRowsFromTable("department") or []
    for d in deps:
        dep_id = str(d["department_id"])
        # k_отдела по умолчанию = важность отдела (department.value); managerAuthor — 0.2.
        dep_default = float(d["value"]) if d.get("value") is not None else DEFAULT_COEFF
        department.setdefault(dep_id, dep_default)
        manager_author.setdefault(dep_id, DEFAULT_COEFF)

    return {"department": department, "managerAuthor": manager_author, "deadline": deadline}


def save(db, department: dict, manager_author: dict, deadline: float) -> None:
    """UPSERT строки настроек (id=1). Вызывается только top-manager через PUT."""
    department = {str(k): float(v) for k, v in (department or {}).items()}
    manager_author = {str(k): float(v) for k, v in (manager_author or {}).items()}
    with db.pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO public.priority_settings (id, department, manager_author, deadline)
            VALUES (1, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
                SET department = EXCLUDED.department,
                    manager_author = EXCLUDED.manager_author,
                    deadline = EXCLUDED.deadline
            """,
            (Json(department), Json(manager_author), float(deadline)),
        )
