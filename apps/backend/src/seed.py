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
  • photo has only value + application_id (no name / type / url yet).
  • there is no priority_settings table — GET/PUT /priority-settings is
    still backed by the in-memory dict in main.py.
  • application has UNIQUE constraints on previous_executor_id and
    closed_by_id, so every value used in those two columns is kept DISTINCT
    here to avoid a unique-violation. (Those constraints are a bug — see notes.)
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

    with db_operator.pool.connection() as conn:

        # ── 0. Wipe everything ────────────────────────────────────────────────
        print("[seed] Wiping all tables …")
        conn.execute("""
            DO $$ DECLARE r RECORD;
            BEGIN
                FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public')
                LOOP
                    EXECUTE 'TRUNCATE TABLE public.'
                        || quote_ident(r.tablename)
                        || ' RESTART IDENTITY CASCADE;';
                END LOOP;
            END $$;
        """)
        print("[seed] All tables cleared.")

        # ── 1. complexity_value (identity starts at 0 per schema) ────────────
        # Index maps directly to ComplexityValues in main.py:
        #   0=easy 1=medium 2=hard 3=critical
        complexity_ids = {}
        for name in ("easy", "medium", "hard", "critical"):
            row = conn.execute(
                "INSERT INTO public.complexity_value (name) VALUES (%s) RETURNING complexity_value_id",
                (name,)
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
        dep_ids = {}
        for dep_key, name, group, value, same_dep, delay, notif in (
            ("it",  "ИТ-отдел",           "Основной",         0.9,  False, 30,  0.8),
            ("oge", "ОГЭ",                "Основной",         0.7,  True,  60,  0.7),
            ("sec", "Отдел безопасности", "Основной",         0.85, False, 45,  0.75),
            ("hr",  "Отдел кадров",       "Административный", 0.5,  True,  120, 0.6),
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
            ("executor4", "Козлов Дмитрий Александрович",  "sec", "senior_senior",  "executor",    True),
            ("author1",   "Новикова Елена Владимировна",   "hr",  "spec_junior",    "author",      True),
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

        # ── 10. types_of_works ────────────────────────────────────────────────
        tow_ids = {}
        work_types = (
            # key,               name,                            dep,   complexity
            ("it_pc_repair",     "Ремонт ПК",                    "it",  "easy"),
            ("it_net_setup",     "Настройка сети",               "it",  "medium"),
            ("it_server_setup",  "Развёртывание сервера",        "it",  "hard"),
            ("it_security_audit","Аудит безопасности",           "it",  "critical"),
            ("oge_inspection",   "Технический осмотр",           "oge", "medium"),
            ("oge_maintenance",  "Плановое обслуживание",        "oge", "easy"),
            ("sec_access",       "Выдача доступа",               "sec", "easy"),
            ("sec_incident",     "Реагирование на инцидент",     "sec", "critical"),
            ("hr_onboarding",    "Оформление нового сотрудника", "hr",  "medium"),
        )
        for tow_key, name, dep_key, complexity in work_types:
            row = conn.execute(
                """
                INSERT INTO public.types_of_works (name, complexity_value, department_id)
                VALUES (%s, %s, %s)
                RETURNING type_of_works_id
                """,
                (name, complexity_ids[complexity], dep_ids[dep_key])
            ).fetchone()
            tow_ids[tow_key] = row[0]
        print(f"[seed] types_of_works → {tow_ids}")

        # ── 11. type_of_work_to_grade ─────────────────────────────────────────
        # New contract: a work type allows a set of GRADES (not post_grades).
        # Relation: вид работы -> сложность -> грейды.
        tow_grade_links = (
            ("it_pc_repair",      ["junior", "middle"]),
            ("it_net_setup",      ["middle", "senior"]),
            ("it_server_setup",   ["senior", "lead"]),
            ("it_security_audit", ["senior", "lead"]),
            ("oge_inspection",    ["middle", "senior"]),
            ("oge_maintenance",   ["junior", "middle"]),
            ("sec_access",        ["junior", "middle"]),
            ("sec_incident",      ["senior", "lead"]),
            ("hr_onboarding",     ["junior"]),
        )
        for tow_key, grade_keys in tow_grade_links:
            for grade_key in grade_keys:
                conn.execute(
                    """
                    INSERT INTO public.type_of_work_to_grade
                        (type_of_works_id, grade_id)
                    VALUES (%s, %s)
                    """,
                    (tow_ids[tow_key], grade_ids[grade_key])
                )
        print("[seed] type_of_work_to_grade → done")

        # ── 12. delegated (application_id NULL for now) ───────────────────────
        # delegated_by / delegated_from / delegated_to are still text columns,
        # so department ids are passed as strings.
        deleg_row = conn.execute(
            """
            INSERT INTO public.delegated
                (delegated_by, delegated_by_employee, delegated_from, delegated_to,
                 comment, created_at, decision, decided_at, application_id)
            VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, NULL)
            RETURNING delegated_id
            """,
            (
                str(dep_ids["it"]),
                emp_ids["manager"],
                str(dep_ids["it"]),
                str(dep_ids["oge"]),
                "Работы относятся к компетенции ОГЭ.",
                now - timedelta(hours=1),
            )
        ).fetchone()
        delegated_id = deleg_row[0]
        print(f"[seed] delegated → delegated_id={delegated_id}")

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
            comp_val = complexity_ids.get(assigned_complexity) if assigned_complexity else None
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

        # ── status: new ───────────────────────────────────────────────────────
        insert_app(
            "new_simple", "Не работает принтер в 302 кабинете", "low", "new",
            "Принтер Canon LBP6030 не печатает. Горит красный индикатор.",
            "it", "it_pc_repair", "author1",
            created_offset_days=1,
        )
        insert_app(
            "new_unfinished", "Повторная настройка VPN для удалённых сотрудников", "medium", "new",
            "Предыдущий исполнитель не завершил работу. Требуется повторная настройка.",
            "it", "it_net_setup", "author2",
            is_unfinished=True,
            previous_executor_key="executor1",   # UNIQUE col — only used here
            manager_comment="Возвращено на доработку — предыдущий исполнитель не справился.",
            created_offset_days=3,
        )
        insert_app(
            "new_high", "Аудит учётных записей после увольнения сотрудника", "high", "new",
            "Необходимо проверить и отозвать все доступы уволившегося сотрудника.",
            "sec", "sec_access", "author1",
            created_offset_days=0,
        )

        # ── status: assigned ──────────────────────────────────────────────────
        insert_app(
            "assigned_it", "Установить рабочую станцию в бухгалтерии", "medium", "assigned",
            "Новый ПК для сотрудника Смирновой Т.В. Требуется установка ОС и 1С.",
            "it", "it_pc_repair", "author2", "executor1",
            executor_at=now - timedelta(hours=2),
            assigned_complexity="easy",
            manager_comment="Назначено вручную. Приоритет — до конца дня.",
            created_offset_days=2,
        )
        insert_app(
            "assigned_oge", "Плановый осмотр серверной комнаты", "high", "assigned",
            "Ежеквартальный технический осмотр оборудования в серверной.",
            "oge", "oge_inspection", "author1", "executor3",
            executor_at=now - timedelta(hours=5),
            assigned_complexity="medium",
            created_offset_days=1,
        )

        # ── status: inProgress ────────────────────────────────────────────────
        insert_app(
            "in_progress_net", "Настройка VLAN для нового офисного сегмента", "high", "inProgress",
            "Требуется создание и настройка VLAN 30 на коммутаторах Cisco.",
            "it", "it_net_setup", "author2", "executor2",
            executor_at=now - timedelta(days=1),
            work_at=now - timedelta(hours=3),
            assigned_complexity="medium",
            created_offset_days=4,
        )
        insert_app(
            "in_progress_sec", "Реагирование на подозрительную активность в сети", "critical", "inProgress",
            "Зафиксированы попытки несанкционированного доступа к серверу БД.",
            "sec", "sec_incident", "author1", "executor4",
            executor_at=now - timedelta(hours=6),
            work_at=now - timedelta(hours=4),
            assigned_complexity="critical",
            created_offset_days=0,
            deadline_offset_days=1,
        )

        # ── status: completed (each closed_by is DISTINCT — UNIQUE col) ───────
        insert_app(
            "completed_1", "Замена жёсткого диска на SSD в ПК директора", "high", "completed",
            "Диск Western Digital 1TB заменён на SSD Samsung 870 EVO 500GB.",
            "it", "it_pc_repair", "author2", "executor1",
            executor_at=now - timedelta(days=5),
            work_at=now - timedelta(days=4),
            finished_at=now - timedelta(days=3),
            result_text="SSD установлен, система перенесена, проверена работоспособность. Скорость загрузки ОС выросла с 90 до 12 секунд.",
            assigned_complexity="easy",
            created_offset_days=7,
            deadline_offset_days=3,
            closed_by_key="executor1",
        )
        insert_app(
            "completed_2", "Развёртывание GitLab на внутреннем сервере", "critical", "completed",
            "Установить и настроить GitLab CE для отдела разработки.",
            "it", "it_server_setup", "author1", "executor2",
            executor_at=now - timedelta(days=10),
            work_at=now - timedelta(days=9),
            finished_at=now - timedelta(days=7),
            result_text="GitLab CE 16.x развёрнут, настроен LDAP, созданы группы и проекты. Документация передана команде.",
            assigned_complexity="hard",
            created_offset_days=14,
            deadline_offset_days=5,
            closed_by_key="executor2",
        )
        insert_app(
            "completed_3", "Оформление нового сотрудника — Титов К.Р.", "low", "completed",
            "Подготовить пропуск, учётную запись и рабочее место.",
            "hr", "hr_onboarding", "author1", "executor3",
            executor_at=now - timedelta(days=3),
            work_at=now - timedelta(days=2),
            finished_at=now - timedelta(days=1),
            result_text="Учётная запись создана, пропуск выдан, рабочее место подготовлено.",
            assigned_complexity="medium",
            created_offset_days=5,
            deadline_offset_days=2,
            closed_by_key="executor3",
            archived_at=now - timedelta(hours=12),   # demo: completed + archived (hidden from main list)
        )

        # ── status: rejected (closed_by = manager, still distinct) ────────────
        insert_app(
            "rejected_1", "Установить игровую мышь на рабочий ПК", "low", "rejected",
            "Сотрудник просит установить игровую мышь Razer для работы.",
            "it", "it_pc_repair", "author2",
            finished_at=now - timedelta(days=1),
            manager_comment="Отклонено: не является производственной необходимостью.",
            closed_by_key="manager",
            created_offset_days=3,
        )

        # ── status: delegated (references the delegation created earlier) ─────
        insert_app(
            "delegated_1", "Техническое обслуживание ИБП в серверной", "medium", "delegated",
            "Требуется проверка и замена аккумуляторов в ИБП APC 3000VA.",
            "it", "oge_maintenance", "author1",
            created_offset_days=2,
            delegated_id=delegated_id,
        )

        # Back-fill the circular FK now that the application row exists.
        conn.execute(
            "UPDATE public.delegated SET application_id = %s WHERE delegated_id = %s",
            (app_ids["delegated_1"], delegated_id)
        )
        print(f"[seed] application + employee_to_application → {app_ids}")
        print(f"[seed] delegated back-filled → app_id={app_ids['delegated_1']}")

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
             "Вам назначена заявка: «Реагирование на подозрительную активность в сети».",
             "in_progress_sec",  False),
            ("executor2",
             "Вам назначена заявка: «Настройка VLAN для нового офисного сегмента».",
             "in_progress_net",  True),
            ("author1",
             "Заявка «Техническое обслуживание ИБП в серверной» передана в ОГЭ.",
             "delegated_1",      False),
            # second notification for executor1 — proves the dropped UNIQUE works
            ("executor1",
             "Заявка «Замена жёсткого диска на SSD» отмечена выполненной.",
             "completed_1",      True),
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

        # ── 15. photo ─────────────────────────────────────────────────────────
        # Photos are now stored in S3. Seed inserts placeholder rows only when
        # S3_BUCKET_NAME is configured so the bucket actually exists.
        import os, uuid, base64, boto3
        bucket = os.environ.get("S3_BUCKET_NAME", "")
        if bucket:
            TINY_PNG = base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
            )
            s3 = boto3.client(
                "s3",
                endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY"),
                region_name=os.environ.get("S3_REGION", "auto"),
            )
            for app_key in ("completed_1", "completed_2", "in_progress_sec"):
                app_id = app_ids[app_key]
                s3_key = f"applications/{app_id}/{uuid.uuid4()}-seed.png"
                s3.put_object(Bucket=bucket, Key=s3_key, Body=TINY_PNG, ContentType="image/png")
                conn.execute(
                    """INSERT INTO public.photo (s3_key, name, content_type, size_bytes, application_id)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (s3_key, "seed.png", "image/png", len(TINY_PNG), app_id)
                )
            print("[seed] photo → done")
        else:
            print("[seed] photo → skipped (S3_BUCKET_NAME not set)")

    # psycopg auto-commits on clean context-manager exit
    print("[seed] ✅ Database seeded successfully.")
    _print_summary(app_ids, emp_ids, dep_ids, tow_ids, pg_ids)


def _print_summary(app_ids, emp_ids, dep_ids, tow_ids, pg_ids):
    print("\n─── Seed summary ────────────────────────────────────────────")
    print(f"  Employees   : {len(emp_ids)}  → {list(emp_ids.values())}")
    print(f"  Departments : {len(dep_ids)}  → {list(dep_ids.values())}")
    print(f"  Work types  : {len(tow_ids)}  → {list(tow_ids.values())}")
    print(f"  Positions   : {len(pg_ids)}   → {list(pg_ids.values())}")
    print(f"  Applications: {len(app_ids)} → {list(app_ids.values())}")
    print("─────────────────────────────────────────────────────────────\n")
