"""
priority_module.py — расчёт приоритета заявки (см. docs/...backend-functions §3.2).

Архитектура рассчитана на лёгкую замену/расширение формулы:
  • `compute_priority_score(...)` — ЧИСТАЯ функция формулы (без БД и побочных эффектов).
    Чтобы поменять формулу или добавить вариант — замените эту функцию либо добавьте
    рядом аналогичную и переключите её вызов в одном месте — `recompute_and_store`.
  • `score_to_level(score)` — отображение непрерывного score в дискретный уровень.
  • `recompute_and_store(...)` / `recompute_open(...)` — «обвязка»: собирают входные данные
    из БД и настроек, вызывают формулу и сохраняют результат. Сбор данных отделён от самой
    формулы, поэтому правки формулы не затрагивают обвязку и наоборот.

Текущая формула:  П = k_отдела · k_времени + k_руководителя + k_срочности , clamp[0,1].
Тяжёлые зависимости (config, БД-настройки) импортируются лениво внутри функций обвязки,
чтобы чистую формулу можно было импортировать и тестировать без остального backend.
"""

from datetime import datetime, timezone

# Пороги отображения score -> уровень (по убыванию). Вынесены отдельно, чтобы их можно
# было настроить или расширить, не трогая формулу.
LEVEL_THRESHOLDS = (("critical", 0.82), ("high", 0.62), ("medium", 0.38))
DEFAULT_LEVEL = "low"

# Значения по умолчанию для коэффициентов/срочности, если не заданы в настройках/конфиге.
DEFAULT_COEFF = 0.2
DEFAULT_URGENT_THRESHOLD_HOURS = 24


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def score_to_level(score: float) -> str:
    """Непрерывный score [0..1] -> уровень приоритета (low/medium/high/critical)."""
    for name, threshold in LEVEL_THRESHOLDS:
        if score >= threshold:
            return name
    return DEFAULT_LEVEL


def _time_factor(created_at, deadline, now) -> float:
    """k_времени ∈ [0,1] — плавная доля прошедшего времени от создания до дедлайна
    (0 — только создана, 1 — дедлайн наступил/прошёл)."""
    if not created_at or not deadline:
        return 0.0
    total = (deadline - created_at).total_seconds()
    if total <= 0:
        return 1.0
    return _clamp01((now - created_at).total_seconds() / total)


def _is_urgent(created_at, deadline, threshold_hours) -> bool:
    if not created_at or not deadline or threshold_hours is None:
        return False
    return (deadline - created_at).total_seconds() <= float(threshold_hours) * 3600.0


def compute_priority_score(*, department_coeff, deadline_weight, created_at, deadline, now,
                           manager_author_coeff=0.0, is_manager_author=False,
                           urgent_threshold_hours=None, urgent_bonus=0.0) -> float:
    """ЧИСТАЯ формула приоритета (единственная точка, которую меняют при смене формулы):

        П = k_отдела · k_времени + k_руководителя + k_срочности ,  зажато в [0,1]

    Все входные данные передаются явно — функция не обращается к БД/конфигу и легко
    тестируется и подменяется.
    """
    k_time = _time_factor(created_at, deadline, now) * float(deadline_weight or 0.0)
    k_manager = float(manager_author_coeff) if is_manager_author else 0.0
    bonus = float(urgent_bonus) if _is_urgent(created_at, deadline, urgent_threshold_hours) else 0.0
    return _clamp01(float(department_coeff or 0.0) * k_time + k_manager + bonus)


# ─────────────────────── обвязка: сбор данных + сохранение ───────────────────────

def _load_settings(db) -> dict:
    """Коэффициенты приоритета из persistent-настроек (department/managerAuthor/deadline)."""
    from src import priority_settings_store as ps_store
    return ps_store.load_effective(db)


def _load_urgent_cfg() -> dict:
    """Порог срочности из config.json -> блок "priority"."""
    from src.application_module import configData
    cfg = (configData.get("priority") or {})
    return {
        "threshold_hours": cfg.get("urgent_deadline_threshold_hours", DEFAULT_URGENT_THRESHOLD_HOURS),
    }


def recompute_and_store(db, cur, application_id, now=None, settings=None, urgent_cfg=None):
    """Собрать входные данные одной заявки (через переданный курсор `cur`, чтобы видеть
    в т.ч. незакоммиченные изменения в текущей транзакции), посчитать score по формуле,
    сохранить `priority_score` и производный `priority_id`. Возвращает (score, level) или None.

    `settings`/`urgent_cfg` можно передать заранее (для пакетного пересчёта), иначе
    загружаются здесь.
    """
    now = now or datetime.now(timezone.utc)
    if settings is None:
        settings = _load_settings(db)
    if urgent_cfg is None:
        urgent_cfg = _load_urgent_cfg()

    app = cur.execute(
        """
        SELECT a.created_at, a.deadline, a.department_id,
               author_link.employee_id AS author_id
        FROM public.application a
        LEFT JOIN public.employee_to_application author_link
               ON author_link.application_id = a.application_id
              AND author_link.role_id = (SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1)
        WHERE a.application_id = %s
        """,
        (int(application_id),),
    ).fetchone()
    if not app:
        return None

    # Коэффициенты берутся по отделу АВТОРА (fallback — отдел заявки).
    author_dept = app["department_id"]
    is_manager_author = False
    if app["author_id"] is not None:
        arow = cur.execute(
            "SELECT e.department_id, r.name AS role FROM public.employee e "
            "LEFT JOIN public.role r ON r.role_id = e.role_id WHERE e.employee_id = %s",
            (app["author_id"],),
        ).fetchone()
        if arow:
            if arow["department_id"] is not None:
                author_dept = arow["department_id"]
            is_manager_author = arow["role"] in ("manager", "top-manager")

    dept_key = str(author_dept) if author_dept is not None else None
    dept_coeff = settings["department"].get(dept_key, DEFAULT_COEFF) if dept_key else DEFAULT_COEFF
    mgr_coeff = settings["managerAuthor"].get(dept_key, DEFAULT_COEFF) if dept_key else DEFAULT_COEFF

    score = compute_priority_score(
        department_coeff=dept_coeff,
        deadline_weight=settings["deadline"],
        created_at=app["created_at"],
        deadline=app["deadline"],
        now=now,
        manager_author_coeff=mgr_coeff,
        is_manager_author=is_manager_author,
        urgent_threshold_hours=urgent_cfg["threshold_hours"],
        urgent_bonus=settings["urgentBonus"],
    )
    level = score_to_level(score)

    pid_row = cur.execute(
        "SELECT priority_id FROM public.priority WHERE name = %s LIMIT 1", (level,)
    ).fetchone()
    cur.execute(
        "UPDATE public.application SET priority_score = %s, priority_id = %s WHERE application_id = %s",
        (score, pid_row["priority_id"] if pid_row else None, int(application_id)),
    )
    return score, level


def recompute_open(db, now=None) -> int:
    """Пересчитать приоритет всех открытых заявок (new/assigned/inProgress/delegated).
    Вызывается периодически подсистемой событий — k_времени меняется со временем.
    Возвращает число обработанных заявок."""
    from psycopg.rows import dict_row
    now = now or datetime.now(timezone.utc)
    settings = _load_settings(db)
    urgent_cfg = _load_urgent_cfg()
    count = 0
    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                "SELECT a.application_id FROM public.application a "
                "JOIN public.status s ON s.status_id = a.status_id "
                "WHERE s.name IN ('new', 'assigned', 'inProgress', 'delegated')"
            ).fetchall()
            for r in rows:
                recompute_and_store(db, cur, r["application_id"], now,
                                    settings=settings, urgent_cfg=urgent_cfg)
                count += 1
    return count
