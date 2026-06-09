"""
seed.py — wipe + fill the entire database with consistent mock data.

Usage (from main.py or a management script):
    from seed import seed_database
    seed_database(DBController)

Uses the postgres superuser connection (DBController) so it never hits
privilege errors. Everything runs inside one transaction; on any error the
whole thing rolls back and the DB is left untouched.

This version matches the CURRENT schema, which means:
  • employee has NO login column — login lives in the config.json MOCK_AD
    directory and is joined in at the API layer.
  • photo stores S3 metadata (s3_key, name, content_type, size_bytes,
    application_id); seed uploads real images from tests/images_for_tests.
  • there is no priority_settings table — GET/PUT /priority-settings is
    still backed by the in-memory dict in main.py.
  • previous_executor_id / closed_by_id have only FK constraints (no UNIQUE),
    so employees may be reused freely across applications.
  • application.delegated_id ↔ delegated.application_id is a circular FK, so
    we insert the delegation with a NULL application_id, create the app, then
    back-fill delegated.application_id.

Fill order (respecting every FK):
  complexity_value → status → priority → role
  → grade → post → post_grade
  → department
  → employee
  → types_of_works → type_of_work_to_grade
  → delegated (application_id NULL)
  → application → employee_to_application
  → delegated (back-fill application_id)
  → notification
  → photo
"""

from datetime import datetime, timezone, timedelta

PROJECT_TZ = timezone.utc


def seed_database(db_operator) -> None:
    """
    Wipe all public tables (RESTART IDENTITY CASCADE) and insert a full
    set of mock data. Raises on any DB error.

    NOTE on employee_id mapping: TRUNCATE … RESTART IDENTITY resets the
    employee identity sequence to 1, and employees are inserted in the order
    below, so the IDs are deterministic:
        1=manager 2=executor1 3=executor2 4=executor3
        5=executor4 6=author1 7=author2 8=manager_oge
    each onboarded entry in config.json MOCK_AD must point its employee_id at
    the matching id.
    """
    now = datetime.now(PROJECT_TZ)

    # Complexity is stored 1-based in types_of_works.complexity_value and
    # application.empl_assigned_complexity (matches main.py: complexity_int_to_str
    # reads value-1, create/update store index+1). 1=easy 2=medium 3=hard 4=critical.
    complexity_to_int = {"easy": 1, "medium": 2, "hard": 3, "critical": 4}

    with db_operator.pool.connection() as conn:

        # ── 0. Wipe everything ────────────────────────────────────────────────
        print("[seed] Wiping all tables …")
        # priority_settings is configuration (set by a top-manager via the API),
        # not demo data — keep it across reseeds so it survives a backend restart.
        conn.execute("""
            DO $$ DECLARE r RECORD;
            BEGIN
                FOR r IN (SELECT tablename FROM pg_tables
                          WHERE schemaname = 'public'
                            AND tablename <> 'priority_settings')
                LOOP
                    EXECUTE 'TRUNCATE TABLE public.'
                        || quote_ident(r.tablename)
                        || ' RESTART IDENTITY CASCADE;';
                END LOOP;
            END $$;
        """)
        print("[seed] All tables cleared.")

        # ── 1. complexity_value ──────────────────────────────────────────────
        # 1-based to match main.py (complexity_int_to_str reads value-1; work-type
        # create/update store index+1). types_of_works.complexity_value is an FK to
        # this table, so the ids here must cover 1..4. We override the identity to
        # pin the ids: 1=easy 2=medium 3=hard 4=critical.
        complexity_ids = {}
        for cid, name in ((1, "easy"), (2, "medium"), (3, "hard"), (4, "critical")):
            row = conn.execute(
                "INSERT INTO public.complexity_value (complexity_value_id, name) "
                "OVERRIDING SYSTEM VALUE VALUES (%s, %s) RETURNING complexity_value_id",
                (cid, name)
            ).fetchone()
            complexity_ids[name] = row[0]
        print(f"[seed] complexity_value → {complexity_ids}")

        # ── 2. status ─────────────────────────────────────────────────────────
        status_ids = {}
        for name in ("new", "assigned", "delegated", "inProgress", "rejected", "completed"):
            row = conn.execute(
                "INSERT INTO public.status (name) VALUES (%s) RETURNING status_id",
                (name,)
            ).fetchone()
            status_ids[name] = row[0]
        print(f"[seed] status → {status_ids}")

        # ── 3. priority ───────────────────────────────────────────────────────
        priority_ids = {}
        for name, value in (("low", 0.25), ("medium", 0.5), ("high", 0.75), ("critical", 1.0)):
            row = conn.execute(
                "INSERT INTO public.priority (name, value) VALUES (%s, %s) RETURNING priority_id",
                (name, value)
            ).fetchone()
            priority_ids[name] = row[0]
        print(f"[seed] priority → {priority_ids}")

        # ── 4. role ───────────────────────────────────────────────────────────
        role_ids = {}
        for name in ("author", "executor", "manager", "top-manager"):
            row = conn.execute(
                "INSERT INTO public.role (name) VALUES (%s) RETURNING role_id",
                (name,)
            ).fetchone()
            role_ids[name] = row[0]
        print(f"[seed] role → {role_ids}")

        # ── 5. grade (identity starts at 0 per schema) ───────────────────────
        grade_ids = {}
        for name in ("junior", "middle", "senior", "lead"):
            row = conn.execute(
                "INSERT INTO public.grade (name) VALUES (%s) RETURNING grade_id",
                (name,)
            ).fetchone()
            grade_ids[name] = row[0]
        print(f"[seed] grade → {grade_ids}")

        # ── 6. post ───────────────────────────────────────────────────────────
        post_ids = {}
        for name, is_top in (
            ("Инженер",         False),
            ("Старший инженер", False),
            ("Руководитель",    True),
            ("Специалист",      False),
        ):
            row = conn.execute(
                "INSERT INTO public.post (name, is_top) VALUES (%s, %s) RETURNING post_id",
                (name, is_top)
            ).fetchone()
            post_ids[name] = row[0]
        print(f"[seed] post → {post_ids}")

        # ── 7. post_grade ─────────────────────────────────────────────────────
        pg_ids = {}
        for pg_key, post_name, grade_name in (
            ("engineer_junior",  "Инженер",         "junior"),
            ("engineer_middle",  "Инженер",         "middle"),
            ("senior_middle",    "Старший инженер", "middle"),
            ("senior_senior",    "Старший инженер", "senior"),
            ("lead_senior",      "Руководитель",    "senior"),
            ("lead_lead",        "Руководитель",    "lead"),
            ("spec_junior",      "Специалист",      "junior"),
            ("spec_middle",      "Специалист",      "middle"),
        ):
            row = conn.execute(
                "INSERT INTO public.post_grade (post_post_id, grade_grade_id) VALUES (%s, %s) RETURNING post_grade_id",
                (post_ids[post_name], grade_ids[grade_name])
            ).fetchone()
            pg_ids[pg_key] = row[0]
        print(f"[seed] post_grade → {pg_ids}")

        # ── 8. department ─────────────────────────────────────────────────────
        # empl_appl_delay is integer (minutes) per the fixed schema.
        # Отделы и виды работ — по docs/requirements-and-type-of-work.md.
        dep_ids = {}
        for dep_key, name, group, value, same_dep, delay, notif in (
            ("it",        "ИТ-отдел",                        "Основной",        0.9,  False, 30,  0.8),
            ("oge",       "Отдел главного энергетика (ОГЭ)",  "Основной",        0.7,  True,  60,  0.7),
            ("prod",      "Производственный отдел",           "Основной",        0.95, False, 30,  0.75),
            ("okk",       "Отдел контроля качества (ОКК)",    "Основной",        0.8,  True,  45,  0.7),
            ("ogm",       "Отдел главного механика (ОГМ)",    "Основной",        0.85, True,  60,  0.7),
            ("warehouse", "Складской отдел",                  "Вспомогательный", 0.5,  False, 120, 0.6),
            ("supply",    "Отдел снабжения",                  "Вспомогательный", 0.6,  False, 120, 0.6),
        ):
            row = conn.execute(
                """
                INSERT INTO public.department
                    ("group", value, name, delegated_to_same_dep,
                     empl_appl_delay, deadline_notification)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING department_id
                """,
                (group, value, name, same_dep, delay, notif)
            ).fetchone()
            dep_ids[dep_key] = row[0]
        print(f"[seed] department → {dep_ids}")

        # ── 9. employee ───────────────────────────────────────────────────────
        # role_key is the employee's system role in the directory (single role
        # per the new contract's User.role). The current user's full set of roles
        # for /auth/me is derived from the role stored in config.json MOCK_AD.
        emp_ids = {}
        employees = (
            # key,        fio,                             dep,   pg_key            role,          is_active
            ("manager",   "Орлова Мария Викторовна",       "it",  "lead_lead",      "top-manager", True),
            ("executor1", "Иванов Иван Иванович",          "it",  "engineer_middle","executor",    True),
            ("executor2", "Петров Пётр Петрович",          "it",  "senior_middle",  "executor",    True),
            ("executor3", "Сидорова Анна Сергеевна",       "oge", "spec_middle",    "executor",    True),
            ("executor4", "Козлов Дмитрий Александрович",  "prod", "senior_senior", "executor",    True),
            ("author1",   "Новикова Елена Владимировна",   "okk",  "spec_junior",   "author",      True),
            ("author2",   "Фёдоров Алексей Николаевич",    "it",  "engineer_junior","author",      True),
            ("manager_oge","Кузнецов Михаил Сергеевич",    "oge", "lead_senior",    "manager",     True),
        )
        for emp_key, fio, dep_key, pg_key, role_key, is_active in employees:
            row = conn.execute(
                """
                INSERT INTO public.employee
                    (department_id, post_grade_id, role_id, fio, created_at, updated_at, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING employee_id
                """,
                (dep_ids[dep_key], pg_ids[pg_key], role_ids[role_key], fio, now, now, is_active)
            ).fetchone()
            emp_ids[emp_key] = row[0]
        print(f"[seed] employee → {emp_ids}")

        # ── 10. types_of_works + 11. type_of_work_to_grade ───────────────────
        # Виды работ по отделам (docs/requirements-and-type-of-work.md). Допустимые
        # грейды («позиции») выводятся из сложности вида работ.
        grades_by_complexity = {
            "easy":     ["junior", "middle"],
            "medium":   ["middle", "senior"],
            "hard":     ["senior", "lead"],
            "critical": ["senior", "lead"],
        }
        tow_ids = {}
        work_types = (
            # key,            name,                                          dep,         complexity
            ("it_replace",    "Замена оборудования",                         "it",        "easy"),
            ("it_fix",        "Починка оборудования",                        "it",        "medium"),
            ("it_server",     "Настройка сервера",                           "it",        "hard"),
            ("it_sw_install", "Установка ПО",                                "it",        "easy"),
            ("it_sw_setup",   "Настройка ПО",                                "it",        "medium"),
            ("oge_motor",     "Ремонт электродвигателей",                    "oge",       "hard"),
            ("oge_short",     "Устранение замыканий",                        "oge",       "critical"),
            ("oge_wiring",    "Ремонт проводки",                             "oge",       "medium"),
            ("oge_pipe",      "Ремонт трубопроводов",                        "oge",       "medium"),
            ("oge_power",     "Устранение обесточивания",                    "oge",       "critical"),
            ("prod_repair",   "Заявка на ремонт оборудования",               "prod",      "medium"),
            ("prod_replace",  "Заявка на замену оборудования",               "prod",      "hard"),
            ("okk_check_in",  "Проверка/приёмка покупных деталей",           "okk",       "easy"),
            ("okk_check_out", "Проверка/приёмка готовой продукции",          "okk",       "medium"),
            ("okk_defect",    "Фиксация брака",                              "okk",       "easy"),
            ("okk_fix",       "Устранение брака",                            "okk",       "hard"),
            ("ogm_repair",    "Ремонтные работы",                            "ogm",       "medium"),
            ("ogm_parts",     "Замена комплектующих",                        "ogm",       "medium"),
            ("ogm_oil",       "Замена масла",                                "ogm",       "easy"),
            ("ogm_emergency", "Аварийные работы",                            "ogm",       "critical"),
            ("wh_invoice",    "Оформление товарных накладных",               "warehouse", "easy"),
            ("wh_ship",       "Отгрузка товара",                             "warehouse", "easy"),
            ("wh_inventory",  "Инвентаризация",                              "warehouse", "medium"),
            ("wh_receive",    "Приёмка товара",                              "warehouse", "easy"),
            ("wh_writeoff",   "Списание и утилизация",                       "warehouse", "medium"),
            ("wh_internal",   "Приёмка от производства (внутри организации)", "warehouse", "easy"),
            ("sup_parts",     "Предоставление комплектующих/деталей",        "supply",    "medium"),
            ("sup_order",     "Оформление заказа поставщику",                "supply",    "medium"),
            ("sup_tender",    "Создание заявок на тендер",                   "supply",    "hard"),
            ("sup_contract",  "Подготовка договоров",                        "supply",    "medium"),
            ("sup_replace",   "Замена позиции в поставке",                   "supply",    "easy"),
        )
        for tow_key, name, dep_key, complexity in work_types:
            row = conn.execute(
                """
                INSERT INTO public.types_of_works (name, complexity_value, department_id)
                VALUES (%s, %s, %s)
                RETURNING type_of_works_id
                """,
                (name, complexity_to_int[complexity], dep_ids[dep_key])
            ).fetchone()
            tow_ids[tow_key] = row[0]
            for grade_key in grades_by_complexity[complexity]:
                conn.execute(
                    "INSERT INTO public.type_of_work_to_grade (type_of_works_id, grade_id) VALUES (%s, %s)",
                    (tow_ids[tow_key], grade_ids[grade_key])
                )
        print(f"[seed] types_of_works → {tow_ids}")
        print("[seed] type_of_work_to_grade → done")

        # ── 12. delegated (application_id NULL for now; back-filled after apps) ─
        # delegated_by / delegated_from / delegated_to are text columns, so
        # department ids are passed as strings.
        def insert_delegation(by_emp_key, from_dep, to_dep, comment, hours_ago):
            return conn.execute(
                """
                INSERT INTO public.delegated
                    (delegated_by, delegated_by_employee, delegated_from, delegated_to,
                     comment, created_at, decision, decided_at, application_id)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, NULL)
                RETURNING delegated_id
                """,
                (str(dep_ids[from_dep]), emp_ids[by_emp_key], str(dep_ids[from_dep]),
                 str(dep_ids[to_dep]), comment, now - timedelta(hours=hours_ago))
            ).fetchone()[0]

        delegated_id_1 = insert_delegation("manager", "it", "oge",
            "Работы относятся к компетенции ОГЭ.", 1)
        delegated_id_2 = insert_delegation("executor4", "prod", "ogm",
            "Требуется бригада ОГМ для замены комплектующих.", 2)
        print(f"[seed] delegated → {delegated_id_1}, {delegated_id_2}")

        # ── 13. application + employee_to_application ─────────────────────────
        # IMPORTANT: previous_executor_id and closed_by_id each carry a UNIQUE
        # constraint in the current schema, so the values supplied for those two
        # columns are kept DISTINCT across all rows below. (See notes — those
        # constraints should be dropped.)
        app_ids = {}

        def insert_app(
            key, name, priority, status, description,
            dep_key, tow_key,
            author_key, executor_key=None,
            is_unfinished=False, is_expired=False,
            created_offset_days=0,
            deadline_offset_days=7,
            executor_at=None, work_at=None, finished_at=None,
            result_text=None, assigned_complexity=None,
            delegated_id=None,
            executor_comment=None, manager_comment=None,
            previous_executor_key=None, closed_by_key=None,
            archived_at=None,
        ):
            created  = now - timedelta(days=created_offset_days)
            deadline = created + timedelta(days=deadline_offset_days)
            comp_val = complexity_to_int.get(assigned_complexity) if assigned_complexity else None
            prev_exc = emp_ids.get(previous_executor_key) if previous_executor_key else None
            closed_by = emp_ids.get(closed_by_key) if closed_by_key else None

            row = conn.execute(
                """
                INSERT INTO public.application (
                    name, priority_id, status_id, description,
                    department_id, types_of_works,
                    is_unfinished, is_expired,
                    empl_assigned_complexity,
                    delegated_id,
                    deadline, created_at, updated_at,
                    executor_at, work_at, finished_at, archived_at, result_text,
                    executor_comment, manager_comment,
                    previous_executor_id, closed_by_id
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s,
                    %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s
                ) RETURNING application_id
                """,
                (
                    name,
                    priority_ids[priority],
                    status_ids[status],
                    description,
                    dep_ids[dep_key],
                    tow_ids[tow_key],
                    is_unfinished, is_expired,
                    comp_val,
                    delegated_id,
                    deadline, created, created,
                    executor_at, work_at, finished_at, archived_at, result_text,
                    executor_comment, manager_comment,
                    prev_exc, closed_by,
                )
            ).fetchone()
            app_id = row[0]
            app_ids[key] = app_id

            # Link author
            conn.execute(
                """
                INSERT INTO public.employee_to_application
                    (role_id, application_id, employee_id)
                VALUES (%s, %s, %s)
                """,
                (role_ids["author"], app_id, emp_ids[author_key])
            )

            # Link executor (if assigned)
            if executor_key:
                conn.execute(
                    """
                    INSERT INTO public.employee_to_application
                        (role_id, application_id, employee_id)
                    VALUES (%s, %s, %s)
                    """,
                    (role_ids["executor"], app_id, emp_ids[executor_key])
                )

            return app_id

        # NOTE: порядок вставки задаёт application_id (1..N). Первые 12 заявок и их
        # статусы зафиксированы (на них опираются интеграционные тесты).

        # ── status: new ───────────────────────────────────────────────────────
        insert_app(
            "new_simple", "Не работает принтер в 302 кабинете", "low", "new",
            "Принтер Canon LBP6030 не печатает. Горит красный индикатор.",
            "it", "it_replace", "author1",
            created_offset_days=1,
        )
        insert_app(
            "new_unfinished", "Повторная установка ПО на рабочих станциях", "medium", "new",
            "Предыдущий исполнитель не завершил работу. Требуется повторная установка.",
            "it", "it_fix", "author2",
            is_unfinished=True,
            previous_executor_key="executor1",
            manager_comment="Возвращено на доработку — предыдущий исполнитель не справился.",
            created_offset_days=3,
        )
        insert_app(
            "new_high", "Внеплановая проверка партии готовой продукции", "high", "new",
            "После замены комплектующих требуется приёмка партии перед отгрузкой.",
            "okk", "okk_check_out", "author1",
            created_offset_days=0,
        )

        # ── status: assigned ──────────────────────────────────────────────────
        insert_app(
            "assigned_it", "Установить рабочую станцию в бухгалтерии", "medium", "assigned",
            "Новый ПК для сотрудника Смирновой Т.В. Требуется установка ОС и 1С.",
            "it", "it_fix", "author2", "executor1",
            executor_at=now - timedelta(hours=2),
            assigned_complexity="easy",
            manager_comment="Назначено вручную. Приоритет — до конца дня.",
            created_offset_days=2,
        )
        insert_app(
            "assigned_oge", "Ремонт проводки в цехе №2", "high", "assigned",
            "Повреждена проводка на участке сборки, требуется ремонт силами ОГЭ.",
            "oge", "oge_wiring", "author1", "executor3",
            executor_at=now - timedelta(hours=5),
            assigned_complexity="medium",
            created_offset_days=1,
        )

        # ── status: inProgress ────────────────────────────────────────────────
        insert_app(
            "in_progress_net", "Настройка сервера для отдела разработки", "high", "inProgress",
            "Требуется развернуть и настроить новый сервер приложений.",
            "it", "it_server", "author2", "executor2",
            executor_at=now - timedelta(days=1),
            work_at=now - timedelta(hours=3),
            assigned_complexity="hard",
            created_offset_days=4,
        )
        insert_app(
            "in_progress_prod", "Аварийный ремонт гидравлического пресса", "critical", "inProgress",
            "Пресс остановлен, течь масла в гидросистеме. Требуется срочный ремонт.",
            "prod", "prod_repair", "author1", "executor4",
            executor_at=now - timedelta(hours=6),
            work_at=now - timedelta(hours=4),
            assigned_complexity="critical",
            created_offset_days=0,
            deadline_offset_days=1,
        )

        # ── status: completed ─────────────────────────────────────────────────
        insert_app(
            "completed_1", "Замена жёсткого диска на SSD в ПК директора", "high", "completed",
            "Диск Western Digital 1TB заменён на SSD Samsung 870 EVO 500GB.",
            "it", "it_replace", "author2", "executor1",
            executor_at=now - timedelta(days=5),
            work_at=now - timedelta(days=4),
            finished_at=now - timedelta(days=3),
            result_text="SSD установлен, система перенесена, проверена работоспособность.",
            assigned_complexity="easy",
            created_offset_days=7,
            deadline_offset_days=3,
            closed_by_key="executor1",
        )
        insert_app(
            "completed_2", "Развёртывание сервера непрерывной интеграции", "critical", "completed",
            "Установить и настроить сервер сборки для отдела разработки.",
            "it", "it_server", "author1", "executor2",
            executor_at=now - timedelta(days=10),
            work_at=now - timedelta(days=9),
            finished_at=now - timedelta(days=7),
            result_text="Сервер развёрнут и настроен, документация передана команде.",
            assigned_complexity="hard",
            created_offset_days=14,
            deadline_offset_days=5,
            closed_by_key="executor2",
        )
        insert_app(
            "completed_3", "Фиксация брака в партии №451", "low", "completed",
            "Зафиксировать и описать брак, выявленный при приёмке партии.",
            "okk", "okk_defect", "author1", "executor3",
            executor_at=now - timedelta(days=3),
            work_at=now - timedelta(days=2),
            finished_at=now - timedelta(days=1),
            result_text="Брак зафиксирован, составлен акт, партия отправлена на доработку.",
            assigned_complexity="easy",
            created_offset_days=5,
            deadline_offset_days=2,
            closed_by_key="executor3",
            archived_at=now - timedelta(hours=12),   # demo: completed + archived (скрыта из списка)
        )

        # ── status: rejected ──────────────────────────────────────────────────
        insert_app(
            "rejected_1", "Установить игровую мышь на рабочий ПК", "low", "rejected",
            "Сотрудник просит установить игровую мышь Razer для работы.",
            "it", "it_replace", "author2",
            finished_at=now - timedelta(days=1),
            manager_comment="Отклонено: не является производственной необходимостью.",
            closed_by_key="manager",
            created_offset_days=3,
        )

        # ── status: delegated ─────────────────────────────────────────────────
        insert_app(
            "delegated_1", "Устранение обесточивания склада", "medium", "delegated",
            "На складе пропало электропитание, требуется бригада ОГЭ.",
            "it", "oge_power", "author1",
            created_offset_days=2,
            delegated_id=delegated_id_1,
        )
        insert_app(
            "delegated_2", "Замена комплектующих конвейера", "high", "delegated",
            "Изношены ролики конвейера, требуется замена силами ОГМ.",
            "prod", "ogm_parts", "author2",
            created_offset_days=1,
            delegated_id=delegated_id_2,
        )

        # ── дополнительные заявки: больше разнообразия + заявка, созданная Орловой ─
        insert_app(
            "orlova_new", "Закупка и установка лицензий ПО", "medium", "new",
            "Необходимо приобрести и установить лицензии офисного ПО на 15 рабочих мест.",
            "it", "it_sw_install", "manager",          # автор — Орлова (top-manager)
            created_offset_days=0,
        )
        insert_app(
            "assigned_okk", "Приёмка покупных деталей от поставщика", "medium", "assigned",
            "Поступила партия комплектующих, требуется входной контроль качества.",
            "okk", "okk_check_in", "author1", "executor3",
            executor_at=now - timedelta(hours=8),
            assigned_complexity="easy",
            created_offset_days=1,
        )
        insert_app(
            "completed_warehouse", "Инвентаризация склада №3", "low", "completed",
            "Плановая инвентаризация остатков на складе №3.",
            "warehouse", "wh_inventory", "author1", "executor1",
            executor_at=now - timedelta(days=4),
            work_at=now - timedelta(days=3),
            finished_at=now - timedelta(days=2),
            result_text="Инвентаризация проведена, расхождений не выявлено.",
            assigned_complexity="medium",
            created_offset_days=6,
            deadline_offset_days=4,
            closed_by_key="executor1",
        )
        insert_app(
            "new_supply", "Оформить заказ поставщику на подшипники", "medium", "new",
            "Закончились подшипники 6204, требуется заказ у поставщика.",
            "supply", "sup_order", "author2",
            created_offset_days=0,
        )
        insert_app(
            "new_ogm", "Аварийные работы на участке упаковки", "high", "new",
            "Вышел из строя упаковочный автомат, требуются аварийные работы ОГМ.",
            "ogm", "ogm_emergency", "author1",
            created_offset_days=0,
        )

        # Back-fill the circular FK now that the application rows exist.
        conn.execute("UPDATE public.delegated SET application_id = %s WHERE delegated_id = %s",
                     (app_ids["delegated_1"], delegated_id_1))
        conn.execute("UPDATE public.delegated SET application_id = %s WHERE delegated_id = %s",
                     (app_ids["delegated_2"], delegated_id_2))
        print(f"[seed] application + employee_to_application → {app_ids}")
        print(f"[seed] delegated back-filled → {app_ids['delegated_1']}, {app_ids['delegated_2']}")

        # ── 14. notification (multiple per employee now allowed) ──────────────
        notifications = (
            # employee_key,  text,                                                          app_key,          is_read
            ("executor1",
             "Вам назначена новая заявка: «Установить рабочую станцию в бухгалтерии».",
             "assigned_it",      False),
            ("executor3",
             "Вам назначена новая заявка: «Плановый осмотр серверной комнаты».",
             "assigned_oge",     False),
            ("manager",
             "Заявка «Развёртывание GitLab на внутреннем сервере» выполнена.",
             "completed_2",      True),
            ("author2",
             "Заявка «Установить игровую мышь на рабочий ПК» отклонена.",
             "rejected_1",       True),
            ("executor4",
             "Вам назначена заявка: «Аварийный ремонт гидравлического пресса».",
             "in_progress_prod", False),
            ("executor2",
             "Вам назначена заявка: «Настройка сервера для отдела разработки».",
             "in_progress_net",  True),
            ("author1",
             "Заявка «Устранение обесточивания склада» передана в ОГЭ.",
             "delegated_1",      False),
            # second notification for executor1 — несколько уведомлений на сотрудника
            ("executor1",
             "Заявка «Замена жёсткого диска на SSD» отмечена выполненной.",
             "completed_1",      True),
            ("manager",
             "Ваша заявка «Закупка и установка лицензий ПО» создана и ожидает распределения.",
             "orlova_new",       False),
            ("author2",
             "Заявка «Замена комплектующих конвейера» передана в ОГМ.",
             "delegated_2",      False),
        )
        for emp_key, text, app_key, is_read in notifications:
            conn.execute(
                """
                INSERT INTO public.notification
                    (text, created_at, employee_id, is_read, application_id)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (text, now - timedelta(minutes=5), emp_ids[emp_key],
                 is_read, app_ids[app_key])
            )
        print("[seed] notification → done")

        # ── 15. photo (вложения в S3) ─────────────────────────────────────────
        # Реальные изображения берём из apps/backend/tests/images_for_tests и
        # раскладываем по нескольким заявкам. Загрузка best-effort: если бакет не
        # настроен или недоступен — заявки засеяны, фото просто пропускаются.
        import os, uuid, mimetypes, boto3
        from pathlib import Path
        from botocore.config import Config as _BotoConfig
        bucket = os.environ.get("S3_BUCKET_NAME", "")
        # Заявки, к которым прикрепляем вложения (изображения распределяются по кругу).
        photo_targets = [
            "completed_1", "completed_2", "in_progress_prod",
            "orlova_new", "completed_warehouse", "delegated_2",
        ]
        if bucket:
            try:
                images_dir = Path(__file__).resolve().parent.parent / "tests" / "images_for_tests"
                image_files = sorted(
                    f for f in images_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")
                ) if images_dir.exists() else []
                if not image_files:
                    print(f"[seed] photo → skipped (no images in {images_dir})")
                else:
                    _path_style = os.environ.get("S3_FORCE_PATH_STYLE", "").strip().lower() in ("1", "true", "yes", "on")
                    s3 = boto3.client(
                        "s3",
                        endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
                        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
                        region_name=os.environ.get("S3_REGION", "auto"),
                        config=_BotoConfig(s3={"addressing_style": "path"}) if _path_style else None,
                    )
                    count = 0
                    for i, app_key in enumerate(photo_targets):
                        img = image_files[i % len(image_files)]
                        data = img.read_bytes()
                        content_type = mimetypes.guess_type(img.name)[0] or "application/octet-stream"
                        app_id = app_ids[app_key]
                        s3_key = f"applications/{app_id}/{uuid.uuid4()}-{img.name}"
                        s3.put_object(Bucket=bucket, Key=s3_key, Body=data, ContentType=content_type)
                        conn.execute(
                            """INSERT INTO public.photo (s3_key, name, content_type, size_bytes, application_id)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (s3_key, img.name, content_type, len(data), app_id)
                        )
                        count += 1
                    print(f"[seed] photo → done ({count} attachments from {len(image_files)} image(s))")
            except Exception as e:
                print(f"[seed] photo → skipped (S3 upload failed: {e})")
        else:
            print("[seed] photo → skipped (S3_BUCKET_NAME not set)")

    # psycopg auto-commits on clean context-manager exit
    print("[seed] ✅ Database seeded successfully.")
    _print_summary(app_ids, emp_ids, dep_ids, tow_ids, pg_ids)


def seed_demo_notifications(db_operator) -> None:
    """Создать несколько ИТ-заявок, чьи дедлайны заставят подсистему событий прислать
    уведомления (просрочка / приближение срока) руководителю ИТ-отдела (orlova_m) в
    первые ~30–120 секунд после старта. Вызывается из main.py только в mock-режиме,
    ПОСЛЕ seed_database (иначе будут стёрты). Заявки исчезнут при следующем пересеве.

    Заявки создаются прямым INSERT (как остальной seed), без обращения к API.
    """
    now = datetime.now(PROJECT_TZ)
    with db_operator.pool.connection() as conn:
        status = conn.execute("SELECT status_id FROM public.status WHERE name = 'new' LIMIT 1").fetchone()
        prio = conn.execute("SELECT priority_id FROM public.priority WHERE name = 'low' LIMIT 1").fetchone()
        # Получатель уведомлений — руководитель ИТ-отдела (orlova_m, employee_id=1).
        author = conn.execute("SELECT employee_id, department_id FROM public.employee WHERE employee_id = 1").fetchone()
        author_role = conn.execute("SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1").fetchone()
        if not (status and prio and author and author_role):
            print("[demo] notification demo skipped (missing seed references)")
            return
        status_id, prio_id = status[0], prio[0]
        author_id, dep_id = author[0], author[1]
        wt = conn.execute(
            "SELECT type_of_works_id FROM public.types_of_works WHERE department_id = %s LIMIT 1",
            (dep_id,),
        ).fetchone()
        if not wt:
            print("[demo] notification demo skipped (no work type in IT department)")
            return
        wt_id = wt[0]

        demos = (
            ("ДЕМО: заявка уже просрочена",
             "Дедлайн в прошлом — уведомление о просрочке придёт на ближайшем тике (~до 30с).",
             now - timedelta(minutes=10)),
            ("ДЕМО: приближается срок исполнения",
             "Уведомление о близости дедлайна придёт, когда останется мало времени (~60с).",
             now + timedelta(seconds=300)),
            ("ДЕМО: просрочится через ~2 минуты",
             "Дедлайн через ~115с — уведомление о просрочке придёт около отметки ~120с.",
             now + timedelta(seconds=115)),
        )
        for name, desc, deadline in demos:
            app_id = conn.execute(
                """
                INSERT INTO public.application
                    (name, priority_id, status_id, description, department_id, types_of_works,
                     is_unfinished, is_expired, deadline, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, false, false, %s, %s, %s)
                RETURNING application_id
                """,
                (name, prio_id, status_id, desc, dep_id, wt_id, deadline, now, now),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO public.employee_to_application (role_id, application_id, employee_id) VALUES (%s, %s, %s)",
                (author_role[0], app_id, author_id),
            )
    print("[demo] notification demo applications created (mock mode)")


def _print_summary(app_ids, emp_ids, dep_ids, tow_ids, pg_ids):
    print("\n─── Seed summary ────────────────────────────────────────────")
    print(f"  Employees   : {len(emp_ids)}  → {list(emp_ids.values())}")
    print(f"  Departments : {len(dep_ids)}  → {list(dep_ids.values())}")
    print(f"  Work types  : {len(tow_ids)}  → {list(tow_ids.values())}")
    print(f"  Positions   : {len(pg_ids)}   → {list(pg_ids.values())}")
    print(f"  Applications: {len(app_ids)} → {list(app_ids.values())}")
    print("─────────────────────────────────────────────────────────────\n")
