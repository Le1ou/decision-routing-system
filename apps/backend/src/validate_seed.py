"""
validate_seed.py — проверка целостности засеянных данных (любой профиль сидирования).

Запуск внутри контейнера backend:
    docker compose exec -T backend python -m src.validate_seed

Скрипт НЕ меняет данные — только читает и печатает найденные логические ошибки.
Возвращает ненулевой код выхода, если есть нарушения (удобно для CI/проверок).

Проверяемые инварианты заявок (application) и связей:
  • Хронология событий: created ≤ executor ≤ work ≤ finished ≤ archived; deadline > created.
  • Минимальные длительности: заявка не может быть закрыта «мгновенно» после назначения
    (finished − executor ≥ MIN_ASSIGN_TO_CLOSE) и после старта работ
    (finished − work ≥ MIN_WORK_TO_CLOSE).
  • Согласованность статуса и таймстампов/исполнителя:
    new — без исполнителя; assigned — назначен и executor_at, без work/finished;
    inProgress — work_at и исполнитель; completed/rejected — finished_at.
  • Исполнитель из того же отдела, что и заявка; роль связи = executor.
  • Инвариант «одна активная заявка на исполнителя» (assigned/inProgress).
  • priority_score ∈ [0, 1]; is_expired согласован со статусом/дедлайном.
"""

import sys
from datetime import timezone

from psycopg.rows import dict_row

from src.application_module import PgDbOperator

# Минимальные правдоподобные длительности (в минутах).
MIN_ASSIGN_TO_CLOSE = 10   # «закрыта меньше чем за 10 минут после назначения» — это ошибка
MIN_WORK_TO_CLOSE = 5      # работа не закрывается мгновенно после старта


# Каждый чек: (название, SQL). SQL возвращает строки-нарушители; пустой результат = ок.
CHECKS = (
    # ── Хронология ────────────────────────────────────────────────────────────
    ("executor_at < created_at",
     "SELECT application_id, created_at, executor_at FROM public.application "
     "WHERE executor_at IS NOT NULL AND executor_at < created_at"),

    ("work_at < executor_at",
     "SELECT application_id, executor_at, work_at FROM public.application "
     "WHERE work_at IS NOT NULL AND executor_at IS NOT NULL AND work_at < executor_at"),

    ("finished_at < work_at",
     "SELECT application_id, work_at, finished_at FROM public.application "
     "WHERE finished_at IS NOT NULL AND work_at IS NOT NULL AND finished_at < work_at"),

    ("finished_at < executor_at",
     "SELECT application_id, executor_at, finished_at FROM public.application "
     "WHERE finished_at IS NOT NULL AND executor_at IS NOT NULL AND finished_at < executor_at"),

    ("archived_at < finished_at",
     "SELECT application_id, finished_at, archived_at FROM public.application "
     "WHERE archived_at IS NOT NULL AND finished_at IS NOT NULL AND archived_at < finished_at"),

    ("deadline <= created_at",
     "SELECT application_id, created_at, deadline FROM public.application "
     "WHERE deadline IS NOT NULL AND deadline <= created_at"),

    # ── Минимальные длительности ──────────────────────────────────────────────
    (f"закрыта < {MIN_ASSIGN_TO_CLOSE} мин после назначения исполнителя",
     "SELECT a.application_id, a.executor_at, a.finished_at, "
     "       EXTRACT(EPOCH FROM (a.finished_at - a.executor_at))/60 AS minutes "
     "FROM public.application a JOIN public.status s ON s.status_id = a.status_id "
     "WHERE s.name = 'completed' AND a.executor_at IS NOT NULL AND a.finished_at IS NOT NULL "
     f"  AND a.finished_at - a.executor_at < INTERVAL '{MIN_ASSIGN_TO_CLOSE} minutes'"),

    (f"закрыта < {MIN_WORK_TO_CLOSE} мин после старта работ",
     "SELECT a.application_id, a.work_at, a.finished_at, "
     "       EXTRACT(EPOCH FROM (a.finished_at - a.work_at))/60 AS minutes "
     "FROM public.application a JOIN public.status s ON s.status_id = a.status_id "
     "WHERE s.name = 'completed' AND a.work_at IS NOT NULL AND a.finished_at IS NOT NULL "
     f"  AND a.finished_at - a.work_at < INTERVAL '{MIN_WORK_TO_CLOSE} minutes'"),

    # ── Согласованность статуса ↔ таймстампы ──────────────────────────────────
    ("completed без finished_at",
     "SELECT a.application_id FROM public.application a JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name='completed' AND a.finished_at IS NULL"),

    ("rejected без finished_at",
     "SELECT a.application_id FROM public.application a JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name='rejected' AND a.finished_at IS NULL"),

    ("inProgress без work_at",
     "SELECT a.application_id FROM public.application a JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name='inProgress' AND a.work_at IS NULL"),

    ("assigned с work_at или finished_at",
     "SELECT a.application_id, a.work_at, a.finished_at FROM public.application a "
     "JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name='assigned' AND (a.work_at IS NOT NULL OR a.finished_at IS NOT NULL)"),

    ("assigned/inProgress без executor_at",
     "SELECT a.application_id, s.name FROM public.application a JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name IN ('assigned','inProgress') AND a.executor_at IS NULL"),

    # ── Согласованность статуса ↔ назначенный исполнитель ─────────────────────
    ("assigned/inProgress/completed без исполнителя",
     "SELECT a.application_id, s.name FROM public.application a JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name IN ('assigned','inProgress','completed') AND NOT EXISTS ("
     "  SELECT 1 FROM public.employee_to_application eta JOIN public.role r ON r.role_id=eta.role_id "
     "  WHERE eta.application_id=a.application_id AND r.name='executor')"),

    ("new с назначенным исполнителем",
     "SELECT a.application_id FROM public.application a JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name='new' AND EXISTS ("
     "  SELECT 1 FROM public.employee_to_application eta JOIN public.role r ON r.role_id=eta.role_id "
     "  WHERE eta.application_id=a.application_id AND r.name='executor')"),

    ("заявка без автора",
     "SELECT a.application_id FROM public.application a WHERE NOT EXISTS ("
     "  SELECT 1 FROM public.employee_to_application eta JOIN public.role r ON r.role_id=eta.role_id "
     "  WHERE eta.application_id=a.application_id AND r.name='author')"),

    # ── Отдел исполнителя ↔ отдел заявки ──────────────────────────────────────
    ("исполнитель из другого отдела",
     "SELECT a.application_id, a.department_id AS app_dept, e.department_id AS exec_dept, e.employee_id "
     "FROM public.application a "
     "JOIN public.employee_to_application eta ON eta.application_id=a.application_id "
     "JOIN public.role r ON r.role_id=eta.role_id AND r.name='executor' "
     "JOIN public.employee e ON e.employee_id=eta.employee_id "
     "JOIN public.status s ON s.status_id=a.status_id "
     "WHERE s.name IN ('assigned','inProgress','completed') AND e.department_id <> a.department_id"),

    # ── Инвариант «одна активная заявка на исполнителя» ───────────────────────
    ("исполнитель с >1 активной заявкой",
     "SELECT eta.employee_id, COUNT(*) AS active FROM public.employee_to_application eta "
     "JOIN public.role r ON r.role_id=eta.role_id AND r.name='executor' "
     "JOIN public.application a ON a.application_id=eta.application_id "
     "JOIN public.status s ON s.status_id=a.status_id AND s.name IN ('assigned','inProgress') "
     "GROUP BY eta.employee_id HAVING COUNT(*) > 1"),

    # ── Прочее ────────────────────────────────────────────────────────────────
    ("priority_score вне [0,1]",
     "SELECT application_id, priority_score FROM public.application "
     "WHERE priority_score IS NOT NULL AND (priority_score < 0 OR priority_score > 1)"),

    ("is_expired у завершённой/отклонённой",
     "SELECT a.application_id, s.name FROM public.application a JOIN public.status s ON s.status_id=a.status_id "
     "WHERE a.is_expired = true AND s.name IN ('completed','rejected')"),

    # ── Согласованность матриц допуска (после дедупликации должностей) ─────────
    ("сотрудник без post_grade",
     "SELECT employee_id, fio FROM public.employee WHERE post_grade_id IS NULL AND is_active = true"),
)


def main() -> int:
    db = PgDbOperator("postgres", "postgres")
    total_violations = 0
    print("═══ Валидация засеянных данных ═══\n")
    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            for name, sql in CHECKS:
                rows = cur.execute(sql).fetchall()
                if rows:
                    total_violations += len(rows)
                    print(f"✗ {name}: {len(rows)} нарушени(й)")
                    for r in rows[:5]:
                        print(f"    {dict(r)}")
                    if len(rows) > 5:
                        print(f"    … ещё {len(rows) - 5}")
                else:
                    print(f"✓ {name}")
    print()
    if total_violations:
        print(f"ИТОГО: {total_violations} нарушени(й) — данные требуют исправления.")
        return 1
    print("ИТОГО: нарушений не найдено — данные согласованы.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
