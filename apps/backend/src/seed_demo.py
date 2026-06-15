"""
seed_demo.py — реалистичный профиль сидирования («чистый запуск», SEED_PROFILE=demo).

Строится ПОВЕРХ базового детерминированного сида (seed.seed_database): сначала
выполняется он — это даёт справочники и восемь сотрудников, привязанных к логинам
config.json → MOCK_AD (employee_id 1–8 должны совпадать, иначе никто не войдёт), —
а затем добавляется «жизнь»:

  • дополнительные сотрудники во всех отделах (руководитель + исполнители + авторы);
    логинов у них нет (в MOCK_AD не заведены) — они участвуют в распределении,
    истории и аналитике, но не аутентифицируются;
  • ~60 заявок за последний месяц с правдоподобным жизненным циклом
    (создана → назначена → в работе → завершена/отклонена), корректными
    таймстампами, журналом переходов (application_status_history — на нём строится
    аналитика), ссылками автор/исполнитель и соблюдением правила «одна активная
    заявка на исполнителя»;
  • несколько исторических делегирований и свежих/прочитанных уведомлений.

Генерация псевдослучайная с ФИКСИРОВАННЫМ зерном — повторный пересев даёт тот же
набор данных. Тестовый набор (pytest) рассчитан на профиль `test`: под `demo`
сид-зависимые тесты падать БУДУТ — это ожидаемо, профиль предназначен для
демонстрации/чистого запуска, а не для CI.
"""

import random
from datetime import datetime, timedelta, timezone

from psycopg.rows import dict_row

from src.seed import seed_database

PROJECT_TZ = timezone.utc
_SEED = 20260611  # фиксированное зерно — детерминированная «случайность»

# (ФИО, № отдела 1..7, (должность, грейд), роль)
_EXTRA_EMPLOYEES = (
    # ИТ (1): ещё исполнители
    ("Громов Артём Сергеевич",        1, ("Инженер", "junior"),          "executor"),
    ("Лебедева Ольга Дмитриевна",     1, ("Старший инженер", "senior"),  "executor"),
    # ОГЭ (2): старший исполнитель (для hard/critical видов работ) и автор
    ("Соколов Виктор Андреевич",      2, ("Старший инженер", "senior"),  "executor"),
    ("Морозова Дарья Игоревна",       2, ("Специалист", "junior"),       "author"),
    # Производственный (3)
    ("Волков Степан Олегович",        3, ("Руководитель", "senior"),     "manager"),
    ("Киселёв Андрей Павлович",       3, ("Инженер", "middle"),          "executor"),
    ("Тихонова Марина Викторовна",    3, ("Специалист", "junior"),       "author"),
    # ОКК (4)
    ("Белова Наталья Юрьевна",        4, ("Руководитель", "lead"),       "manager"),
    ("Гусев Павел Романович",         4, ("Специалист", "middle"),       "executor"),
    ("Зайцева Ксения Артёмовна",      4, ("Специалист", "junior"),       "executor"),
    # ОГМ (5)
    ("Крылов Олег Валентинович",      5, ("Руководитель", "senior"),     "manager"),
    ("Степанов Руслан Маратович",     5, ("Старший инженер", "senior"),  "executor"),
    ("Ефимов Денис Константинович",   5, ("Инженер", "middle"),          "executor"),
    # Складской (6)
    ("Антонова Светлана Борисовна",   6, ("Руководитель", "senior"),     "manager"),
    ("Никитин Глеб Эдуардович",       6, ("Специалист", "middle"),       "executor"),
    ("Орехова Полина Станиславовна",  6, ("Специалист", "junior"),       "executor"),
    # Снабжение (7)
    ("Романов Илья Вячеславович",     7, ("Руководитель", "lead"),       "manager"),
    ("Фомина Алина Денисовна",        7, ("Специалист", "middle"),       "executor"),
    ("Царёв Максим Леонидович",       7, ("Старший инженер", "middle"),  "executor"),
)

_APP_TOPICS = (
    "Не работает {}", "Сбой в работе: {}", "Требуется обслуживание — {}",
    "Плановые работы: {}", "Срочно: {}", "Заявка по участку №{n}: {}",
)
_APP_OBJECTS = (
    "конвейерная линия", "станок ЧПУ", "вентиляция цеха", "освещение склада",
    "пресс гидравлический", "терминал отгрузки", "сервер участка", "электрощит",
    "погрузчик", "система контроля доступа", "насосная станция", "упаковочная машина",
)
_RESULTS = (
    "Работы выполнены, оборудование проверено под нагрузкой.",
    "Заменены изношенные комплектующие, проведено тестирование.",
    "Неисправность устранена, составлен акт.",
    "Выполнена настройка и профилактика, замечаний нет.",
    "Дефект устранён, передано в эксплуатацию.",
)
_REJECT_COMMENTS = (
    "Дубликат существующей заявки.", "Работы не требуются после повторной проверки.",
    "Передано подрядной организации.",
)

# Веса жизненного цикла сгенерированных заявок.
_STATUS_WEIGHTS = (("completed", 50), ("assigned", 15), ("inProgress", 15),
                   ("new", 12), ("rejected", 8))
_PRIORITY_WEIGHTS = (("low", 50), ("medium", 30), ("high", 15), ("critical", 5))
_PRIORITY_SCORE = {"low": 0.18, "medium": 0.48, "high": 0.7, "critical": 0.88}


def _pick(rnd, weighted):
    return rnd.choices([v for v, _ in weighted], weights=[w for _, w in weighted])[0]


def seed_database_demo(db_operator) -> None:
    """Базовый сид + реалистичный слой. Вызывается вместо seed_database при
    SEED_PROFILE=demo (см. core.py)."""
    seed_database(db_operator)
    rnd = random.Random(_SEED)
    now = datetime.now(PROJECT_TZ)

    with db_operator.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # ── Справочники из БД (ids детерминированы базовым сидом) ──────────
            roles = {r["name"]: r["role_id"] for r in
                     cur.execute("SELECT role_id, name FROM public.role").fetchall()}
            statuses = {r["name"]: r["status_id"] for r in
                        cur.execute("SELECT status_id, name FROM public.status").fetchall()}
            priorities = {r["name"]: r["priority_id"] for r in
                          cur.execute("SELECT priority_id, name FROM public.priority").fetchall()}
            post_grades = {
                (r["post"], r["grade"]): r["post_grade_id"] for r in cur.execute(
                    "SELECT pg.post_grade_id, p.name AS post, g.name AS grade "
                    "FROM public.post_grade pg "
                    "JOIN public.post p ON p.post_id = pg.post_post_id "
                    "JOIN public.grade g ON g.grade_id = pg.grade_grade_id").fetchall()
            }
            work_types = cur.execute(
                "SELECT type_of_works_id, department_id FROM public.types_of_works").fetchall()
            wts_by_dept = {}
            for wt in work_types:
                wts_by_dept.setdefault(wt["department_id"], []).append(wt["type_of_works_id"])

            # ── Дополнительные сотрудники (без логинов) ────────────────────────
            for fio, dept, pg_key, role in _EXTRA_EMPLOYEES:
                cur.execute(
                    "INSERT INTO public.employee (department_id, post_grade_id, role_id, fio, "
                    "created_at, updated_at, is_active) VALUES (%s, %s, %s, %s, %s, %s, true)",
                    (dept, post_grades[pg_key], roles[role], fio,
                     now - timedelta(days=120), now - timedelta(days=120)),
                )

            employees = cur.execute(
                "SELECT e.employee_id, e.department_id, r.name AS role FROM public.employee e "
                "JOIN public.role r ON r.role_id = e.role_id WHERE e.is_active = true").fetchall()
            execs_by_dept, authors_by_dept = {}, {}
            for e in employees:
                if e["role"] == "executor":
                    execs_by_dept.setdefault(e["department_id"], []).append(e["employee_id"])
                authors_by_dept.setdefault(e["department_id"], []).append(e["employee_id"])

            def journal(app_id, from_st, to_st, by_emp, reason, at):
                cur.execute(
                    "INSERT INTO public.application_status_history (application_id, "
                    "from_status_id, to_status_id, changed_at, by_employee_id, reason) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (app_id, from_st, to_st, at, by_emp, reason))

            def notify(text, emp, app_id, at, is_read):
                cur.execute(
                    "INSERT INTO public.notification (text, created_at, employee_id, is_read, "
                    "application_id) VALUES (%s, %s, %s, %s, %s)",
                    (text, at, emp, is_read, app_id))

            def dept_manager(dept):
                mgrs = [e["employee_id"] for e in employees
                        if e["department_id"] == dept and e["role"] in ("manager", "top-manager")]
                return mgrs[0] if mgrs else None

            # ── ~60 заявок за последний месяц ──────────────────────────────────
            busy = set()          # исполнители с активной заявкой (инвариант «одна заявка»)
            counts = {}
            completed_app_ids = []
            for i in range(60):
                dept = rnd.choices(range(1, 8), weights=(22, 18, 14, 12, 14, 12, 8))[0]
                wt = rnd.choice(wts_by_dept[dept])
                author = rnd.choice(authors_by_dept[dept])
                status = _pick(rnd, _STATUS_WEIGHTS)
                # Активным заявкам нужен СВОБОДНЫЙ исполнитель отдела.
                executor = None
                if status in ("assigned", "inProgress", "completed", "rejected"):
                    pool = execs_by_dept.get(dept, [])
                    if status in ("assigned", "inProgress"):
                        pool = [x for x in pool if x not in busy]
                    if not pool:
                        status = "new"
                    else:
                        executor = rnd.choice(pool)
                        if status in ("assigned", "inProgress"):
                            busy.add(executor)
                if status == "rejected" and rnd.random() < 0.5:
                    executor = None   # отменена автором ещё из «Нового»

                created = now - timedelta(days=rnd.uniform(0.3, 30), hours=rnd.uniform(0, 8))
                deadline = created + timedelta(days=rnd.uniform(1.5, 14))
                if status == "new":
                    # свежие необработанные; пара — с горящим/прошедшим сроком
                    created = now - timedelta(hours=rnd.uniform(1, 40))
                    deadline = created + timedelta(days=rnd.uniform(0.05, 7))

                executor_at = created + timedelta(minutes=rnd.uniform(8, 600)) if executor else None
                work_at = (executor_at + timedelta(minutes=rnd.uniform(5, 360))
                           if executor and status in ("inProgress", "completed") else None)
                finished = None
                if status == "completed":
                    finished = work_at + timedelta(hours=rnd.uniform(0.5, 60))
                elif status == "rejected":
                    finished = created + timedelta(hours=rnd.uniform(1, 48))
                updated = finished or work_at or executor_at or created

                prio = _pick(rnd, _PRIORITY_WEIGHTS)
                score = min(1.0, max(0.0, _PRIORITY_SCORE[prio] + rnd.uniform(-0.06, 0.06)))
                topic = rnd.choice(_APP_TOPICS).format(rnd.choice(_APP_OBJECTS), n=rnd.randint(1, 9))

                app_id = cur.execute(
                    """INSERT INTO public.application
                        (name, priority_id, status_id, description, department_id, types_of_works,
                         is_unfinished, is_expired, deadline, created_at, updated_at,
                         executor_at, work_at, finished_at, result_text, manager_comment,
                         priority_score)
                       VALUES (%s,%s,%s,%s,%s,%s,false,false,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING application_id""",
                    (topic, priorities[prio], statuses[status],
                     f"{topic}. Требуется выезд специалиста и диагностика.",
                     dept, wt, deadline, created, updated, executor_at, work_at, finished,
                     rnd.choice(_RESULTS) if status == "completed" else None,
                     rnd.choice(_REJECT_COMMENTS) if status == "rejected" else None,
                     score),
                ).fetchone()["application_id"]

                cur.execute("INSERT INTO public.employee_to_application (role_id, application_id, "
                            "employee_id) VALUES (%s, %s, %s)", (roles["author"], app_id, author))
                if executor:
                    cur.execute("INSERT INTO public.employee_to_application (role_id, application_id, "
                                "employee_id) VALUES (%s, %s, %s)",
                                (roles["executor"], app_id, executor))

                # Журнал переходов — основа аналитики.
                mgr = dept_manager(dept)
                journal(app_id, None, statuses["new"], author, "create", created)
                if executor_at:
                    journal(app_id, statuses["new"], statuses["assigned"], mgr,
                            "assignExecutor", executor_at)
                if work_at:
                    journal(app_id, statuses["assigned"], statuses["inProgress"], executor,
                            "startWork", work_at)
                if status == "completed":
                    journal(app_id, statuses["inProgress"], statuses["completed"], executor,
                            "complete", finished)
                    completed_app_ids.append((app_id, topic, author, finished))
                if status == "rejected":
                    journal(app_id, statuses["assigned"] if executor else statuses["new"],
                            statuses["rejected"], mgr if executor else author,
                            "reject" if executor else "cancel", finished)

                # Уведомления: свежие назначения (непрочитанные) и завершения.
                if status in ("assigned", "inProgress") and (now - executor_at).days < 3:
                    notify(f"Вам назначена заявка: «{topic}».", executor, app_id,
                           executor_at, False)
                if status == "completed" and (now - finished).days < 7:
                    notify(f"Заявка «{topic}» выполнена.", author, app_id, finished,
                           rnd.random() < 0.6)
                counts[status] = counts.get(status, 0) + 1

            # ── Несколько исторических делегирований (по завершённым заявкам) ──
            for app_id, topic, author, finished in completed_app_ids[:4]:
                row = cur.execute("SELECT department_id, created_at FROM public.application "
                                  "WHERE application_id = %s", (app_id,)).fetchone()
                other = rnd.choice([d for d in range(1, 8) if d != row["department_id"]])
                decided = row["created_at"] + timedelta(hours=rnd.uniform(1, 12))
                cur.execute(
                    "INSERT INTO public.delegated (delegated_by, delegated_by_employee, "
                    "delegated_from, delegated_to, comment, created_at, application_id, "
                    "decision, decided_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (str(other), dept_manager(other), str(other), str(row["department_id"]),
                     "Работы относятся к вашему отделу.", row["created_at"], app_id,
                     "confirmed", decided))

    print(f"[seed:demo] extra employees → {len(_EXTRA_EMPLOYEES)}, "
          f"generated applications → {counts}")
