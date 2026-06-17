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

from src.application_module import configData
from src.db_helpers import dept_manager_ids
from src.seed import seed_database

PROJECT_TZ = timezone.utc
_SEED = 20260611  # фиксированное зерно — детерминированная «случайность»


# ─────────────────────────── Рабочее время (config.json → "worktime") ─────────
# Демонстрационный профиль раскладывает ВСЕ сгенерированные таймстампы по рабочему
# окну: рабочие часы [start, end] и признак, считаются ли суббота/воскресенье
# рабочими днями. Так «живой» вид на git выглядит правдоподобно — заявки создаются
# и обрабатываются в рабочее время, а не в три часа ночи в воскресенье.
def _parse_hhmm(value, default_seconds: int) -> int:
    """'HH:MM' → секунды от полуночи; на мусоре — дефолт."""
    try:
        hh, mm = str(value).split(":")
        secs = int(hh) * 3600 + int(mm) * 60
        return secs if 0 <= secs < 86400 else default_seconds
    except Exception:
        return default_seconds


_wt_cfg = configData.get("worktime", {}) or {}
_WT_START = _parse_hhmm(_wt_cfg.get("start"), 9 * 3600)
_WT_END = _parse_hhmm(_wt_cfg.get("end"), 19 * 3600)
if _WT_END <= _WT_START:                       # защита от инвертированного окна
    _WT_END = min(_WT_START + 3600, 86399)
_WT_WINDOW = _WT_END - _WT_START
_SAT_WORKING = bool(_wt_cfg.get("saturday_working", False))
_SUN_WORKING = bool(_wt_cfg.get("sunday_working", False))
# Таймстампы хранятся в UTC, а фронт рендерит их в локальной зоне пользователя
# (Intl.DateTimeFormat без timeZone). Рабочее окно [start, end] и рабочие дни
# должны выполняться именно в этой зоне отображения, поэтому окно вычисляется в
# локальном времени (UTC + offset) и затем результат переводится обратно в UTC.
try:
    _WT_OFFSET = timedelta(hours=float(_wt_cfg.get("timezone_offset_hours", 0) or 0))
except (TypeError, ValueError):
    _WT_OFFSET = timedelta(0)


def _is_working_day(d) -> bool:
    wd = d.weekday()                           # Пн=0 … Сб=5, Вс=6
    if wd == 5:
        return _SAT_WORKING
    if wd == 6:
        return _SUN_WORKING
    return True


def _next_working_day(d):
    while not _is_working_day(d):
        d += timedelta(days=1)
    return d


def _preceding_nonwork_run(td):
    """Сколько нерабочих дней идёт подряд непосредственно перед рабочим днём td."""
    cnt = 0
    p = td - timedelta(days=1)
    while not _is_working_day(p):
        cnt += 1
        p -= timedelta(days=1)
    return cnt


def to_worktime(dt: datetime) -> datetime:
    """Отобразить произвольный момент в рабочее окно/дни — СТРОГО монотонно по `dt`.

    Каждые сутки сжимаются в рабочее окно [start, end]. Нерабочие дни не
    схлопываются в одну точку, а «упаковываются» в начало следующего рабочего дня:
    окно этого дня делится на (k+1) равных под-окон, где k — число подряд идущих
    нерабочих дней перед ним; ранние нерабочие дни занимают первые под-окна, сам
    рабочий день — последнее. Внутри под-окна сутки сжимаются по времени суток.

    Так отображение СТРОГО возрастает: разные моменты дают разное время (никаких
    совпадающих таймстампов у соседних сообщений), порядок событий одной заявки
    сохраняется, и всё остаётся в рабочем окне рабочих дней. Окно считается в
    локальной зоне отображения (UTC + timezone_offset_hours), результат — в UTC."""
    local = dt + _WT_OFFSET                    # «местное» время (метка tz прежняя)
    d = local.date()
    tod = local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1e6
    tod_frac = tod / 86400.0

    target = _next_working_day(d)              # рабочий день-приёмник
    run = _preceding_nonwork_run(target)       # нерабочих дней перед ним
    total_slots = run + 1
    if _is_working_day(d):
        slot = run                             # сам рабочий день — последнее под-окно
    else:
        earliest = target - timedelta(days=run)
        slot = (d - earliest).days             # позиция нерабочего дня в серии

    width = _WT_WINDOW / total_slots
    secs = _WT_START + slot * width + tod_frac * width
    midnight = datetime(target.year, target.month, target.day, tzinfo=dt.tzinfo)
    local_res = midnight + timedelta(seconds=secs)
    return local_res - _WT_OFFSET              # обратно в UTC


# Хелперы для дат уже-в-рабочем-окне (когда базовый перенос _remap_to_worktime уже
# сделан): нужны, чтобы досоздавать строки «устаканивания» сразу в рабочем окне, не
# гоняя их повторно через to_worktime (она не идемпотентна).
def _window_end_utc(local_date, tzinfo) -> datetime:
    """Конец рабочего окна указанной МЕСТНОЙ даты, в UTC."""
    return (datetime(local_date.year, local_date.month, local_date.day, tzinfo=tzinfo)
            + timedelta(seconds=_WT_END) - _WT_OFFSET)


def _bump_in_window(base: datetime, gap: timedelta, ceiling: datetime) -> datetime:
    """Момент через ~gap после `base` (который уже в рабочем окне), но не позже конца
    рабочего окна того же дня и не позже `ceiling` (последний момент окна ≤ now). Так
    событие остаётся валидным (в окне рабочего дня) и в прошлом."""
    if base > ceiling:
        base = ceiling
    local = base + _WT_OFFSET
    cap = min(_window_end_utc(local.date(), local.tzinfo), ceiling)
    target = base + gap
    if target > cap:
        target = cap
    if target < base:
        target = base
    return target


def _recent_in_window(now: datetime) -> datetime:
    """Ближайший момент рабочего окна НЕ позже `now` (для дат «недавно»)."""
    local = now + _WT_OFFSET
    d = local.date()
    sod = local.hour * 3600 + local.minute * 60 + local.second
    if _is_working_day(d) and _WT_START <= sod <= _WT_END:
        return now
    if _is_working_day(d) and sod > _WT_END:
        return _window_end_utc(d, local.tzinfo)
    pd = d - timedelta(days=1)
    while not _is_working_day(pd):
        pd -= timedelta(days=1)
    return _window_end_utc(pd, local.tzinfo)


# Колонки-таймстампы, которые нужно перенести в рабочее окно post-фактум (после
# базового сида + слоя demo). pk → имя первичного ключа, cols → колонки времени.
_TIMESTAMP_COLUMNS = (
    ("application", "application_id",
     ("created_at", "updated_at", "executor_at", "work_at",
      "finished_at", "archived_at", "deadline")),
    ("application_message", "message_id", ("created_at",)),
    ("application_status_history", "id", ("changed_at",)),
    ("notification", "notification_id", ("created_at",)),
    ("delegated", "delegated_id", ("created_at", "decided_at")),
)


def _remap_to_worktime(cur, ceiling) -> None:
    """Прогнать все таймстампы демо-данных через to_worktime (in-place UPDATE).

    Применяется ОДИН раз после генерации. to_worktime монотонна, поэтому относительный
    порядок колонок внутри строки сохраняется. Все колонки КРОМЕ `deadline` —
    исторические (события прошлого), поэтому они зажимаются сверху `ceiling` (последний
    момент рабочего окна ≤ now): иначе при сидировании ВНЕ рабочих часов «почти-сейчас»
    метки уехали бы в будущее (напр. на 09:00 текущего дня). `deadline` может быть в
    будущем — его не зажимаем."""
    for table, pk, cols in _TIMESTAMP_COLUMNS:
        rows = cur.execute(
            f"SELECT {pk}, {', '.join(cols)} FROM public.{table}").fetchall()
        for r in rows:
            updates = {}
            for c in cols:
                if r[c] is None:
                    continue
                mapped = to_worktime(r[c])
                if c != "deadline" and mapped > ceiling:
                    mapped = ceiling
                updates[c] = mapped
            if not updates:
                continue
            assignment = ", ".join(f"{c} = %s" for c in updates)
            cur.execute(
                f"UPDATE public.{table} SET {assignment} WHERE {pk} = %s",
                (*updates.values(), r[pk]))


# ─────────────────── «Устаканивание» мира под фоновый цикл ─────────────────────
# Демо-сид заранее доводит данные до неподвижной точки подсистем событий и
# маршрутизации и проставляет дедуп-флаги, чтобы ПЕРВЫЙ тик фонового цикла НЕ
# присылал лавину уведомлений «сейчас» по старым заявкам. Уведомления, которые этот
# тик создал бы (просрочка, приближение дедлайна, неуспех распределения), сид
# создаёт сам — датируя их ПРОШЛЫМ (как и положено по логике заявки).
def _insert_notif(cur, text, employee_id, application_id, at, is_read) -> None:
    if employee_id is None:
        return
    cur.execute(
        "INSERT INTO public.notification (text, created_at, employee_id, is_read, application_id) "
        "VALUES (%s, %s, %s, %s, %s)",
        (text, at, int(employee_id), bool(is_read), int(application_id)))


def _assigned_executor_id(cur, app_id):
    row = cur.execute(
        "SELECT eta.employee_id FROM public.employee_to_application eta "
        "JOIN public.role r ON r.role_id = eta.role_id "
        "WHERE eta.application_id = %s AND r.name = 'executor' LIMIT 1",
        (int(app_id),)).fetchone()
    return row["employee_id"] if row else None


def _recipients(cur, dept_id, app_id) -> set:
    """Получатели уведомления по заявке: руководители отдела + назначенный исполнитель."""
    ids = set(dept_manager_ids(cur, dept_id))
    ex = _assigned_executor_id(cur, app_id)
    if ex is not None:
        ids.add(ex)
    return ids


def _past_is_read(now, at, rnd) -> bool:
    """Старое уведомление скорее прочитано, свежее — скорее нет (правдоподобный микс)."""
    age_h = (now - at).total_seconds() / 3600.0
    if age_h >= 48:
        return rnd.random() < 0.9
    if age_h >= 6:
        return rnd.random() < 0.5
    return rnd.random() < 0.15


def _backdate_routing(cur, rnd, now, ceiling, before_notif, before_hist, new_before) -> None:
    """Сдвинуть в прошлое (в рабочее окно) то, что создал разовый проход run_routing
    (он метит «сейчас»). Данные уже перенесены в окно, поэтому используем _bump_in_window."""
    assigned_at = {}
    if new_before:
        for r in cur.execute(
                "SELECT a.application_id, a.created_at, s.name AS st "
                "FROM public.application a JOIN public.status s ON s.status_id = a.status_id "
                "WHERE a.application_id = ANY(%s)", (list(new_before),)).fetchall():
            if r["st"] in ("assigned", "inProgress") and r["created_at"] is not None:
                at = _bump_in_window(r["created_at"], timedelta(minutes=rnd.uniform(15, 360)), ceiling)
                assigned_at[r["application_id"]] = at
                cur.execute(
                    "UPDATE public.application SET executor_at = %s, updated_at = %s "
                    "WHERE application_id = %s", (at, at, r["application_id"]))
    # Уведомления маршрутизации (id > before_notif): назначение — к моменту назначения;
    # эскалация (заявка осталась new) — вскоре после её создания.
    for n in cur.execute(
            "SELECT notification_id, application_id FROM public.notification "
            "WHERE notification_id > %s", (before_notif,)).fetchall():
        app = n["application_id"]
        if app in assigned_at:
            at = assigned_at[app]
        else:
            cr = cur.execute("SELECT created_at FROM public.application WHERE application_id = %s",
                             (app,)).fetchone()
            base = cr["created_at"] if cr and cr["created_at"] else ceiling
            at = _bump_in_window(base, timedelta(minutes=rnd.uniform(30, 240)), ceiling)
        cur.execute("UPDATE public.notification SET created_at = %s, is_read = %s "
                    "WHERE notification_id = %s",
                    (at, _past_is_read(now, at, rnd), n["notification_id"]))
    # Журнал маршрутизации (id > before_hist) — синхронно с назначением.
    for h in cur.execute(
            "SELECT id, application_id FROM public.application_status_history WHERE id > %s",
            (before_hist,)).fetchall():
        if h["application_id"] in assigned_at:
            cur.execute("UPDATE public.application_status_history SET changed_at = %s WHERE id = %s",
                        (assigned_at[h["application_id"]], h["id"]))


def _settle_expiry(cur, rnd, now, ceiling) -> int:
    """Пометить уже просроченные открытые заявки + уведомления, датированные прошлым."""
    overdue = cur.execute(
        "SELECT a.application_id, a.name, a.department_id, a.deadline "
        "FROM public.application a JOIN public.status s ON s.status_id = a.status_id "
        "WHERE a.deadline IS NOT NULL AND a.deadline < %s "
        "AND s.name NOT IN ('completed', 'rejected') "
        "AND COALESCE(a.is_expired, false) = false", (now,)).fetchall()
    for r in overdue:
        cur.execute("UPDATE public.application SET is_expired = true WHERE application_id = %s",
                    (r["application_id"],))
        # Дедлайн уже в рабочем окне (данные перенесены) — уведомление чуть позже него.
        at = _bump_in_window(r["deadline"], timedelta(hours=rnd.uniform(0.5, 6)), ceiling)
        for rid in _recipients(cur, r["department_id"], r["application_id"]):
            _insert_notif(cur, f"Заявка «{r['name']}» просрочена.", rid,
                          r["application_id"], at, _past_is_read(now, at, rnd))
    return len(overdue)


def _settle_deadline(cur, rnd, now, ceiling) -> int:
    """Пометить заявки с приближающимся дедлайном + уведомления, датированные прошлым."""
    rows = cur.execute(
        "SELECT a.application_id, a.name, a.department_id, a.created_at, a.deadline, "
        "       d.deadline_notification "
        "FROM public.application a JOIN public.status s ON s.status_id = a.status_id "
        "JOIN public.department d ON d.department_id = a.department_id "
        "WHERE s.name IN ('new', 'assigned', 'inProgress', 'delegated') "
        "AND a.deadline IS NOT NULL AND a.created_at IS NOT NULL "
        "AND a.deadline > a.created_at AND a.deadline > %s "
        "AND COALESCE(a.deadline_notified, false) = false", (now,)).fetchall()
    count = 0
    for r in rows:
        total = (r["deadline"] - r["created_at"]).total_seconds()
        remaining = (r["deadline"] - now).total_seconds()
        ratio = remaining / total if total > 0 else 0.0
        threshold = (r["deadline_notification"]
                     if r["deadline_notification"] is not None else 0.25)
        if ratio <= threshold:
            cur.execute("UPDATE public.application SET deadline_notified = true "
                        "WHERE application_id = %s", (r["application_id"],))
            # Приближение замечено «недавно» — датируем ближайшим прошлым в рабочем окне.
            at = ceiling
            pct = max(0, int(round(ratio * 100)))
            for rid in _recipients(cur, r["department_id"], r["application_id"]):
                _insert_notif(cur, f"По заявке «{r['name']}» истекает срок (осталось ~{pct}% времени).",
                              rid, r["application_id"], at, _past_is_read(now, at, rnd))
            count += 1
    return count


def _settle_demo(db, rnd, now, ceiling, before_notif, before_hist, new_before) -> dict:
    """Довести мир до неподвижной точки подсистем (после коммита генерации И переноса
    в рабочее окно — _remap_to_worktime уже выполнен на ВСЕХ сид-данных).

    Маршрутизация прогоняется на ФИНАЛЬНЫХ (уже перенесённых) данных — ровно тех, что
    увидит фоновый цикл, поэтому его первый тик ничего не переназначит. Результат
    маршрутизации и пометки просрочки/дедлайна датируются прошлым (в рабочем окне).
    Повторный перенос НЕ нужен — все новые метки считаются сразу в окне."""
    from src import routing_module
    routing_module.run_routing(db, now=now)
    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            _backdate_routing(cur, rnd, now, ceiling, before_notif, before_hist, new_before)
            expired = _settle_expiry(cur, rnd, now, ceiling)
            approaching = _settle_deadline(cur, rnd, now, ceiling)
    return {"expired": expired, "deadlineNotifications": approaching}


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

# Небольшие диалоги автор ↔ исполнитель для «живого» вида карточек. Каждый кортеж —
# (кто: "author"|"executor", текст). Реплики идут по очереди; таймстампы
# проставляются логично (после назначения исполнителя, по возрастанию) при вставке.
_CHAT_SCRIPTS = (
    (("author",   "Добрый день! Подскажите, когда сможете приступить?"),
     ("executor", "Здравствуйте! Могу подойти в течение часа."),
     ("author",   "Отлично, доступ на участок открыт."),
     ("executor", "Принял, выезжаю.")),
    (("executor", "Уточните, пожалуйста, модель оборудования."),
     ("author",   "Это станок с участка №3, паспорт оставил на месте."),
     ("executor", "Нашёл, спасибо. Приступаю к диагностике.")),
    (("author",   "Это срочно — линия простаивает."),
     ("executor", "Понял, поднимаю приоритет, буду через час."),
     ("author",   "Отлично, ждём.")),
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

            def message(app_id, author_emp, text, at):
                cur.execute(
                    "INSERT INTO public.application_message (application_id, "
                    "author_employee_id, text, created_at) VALUES (%s, %s, %s, %s)",
                    (app_id, author_emp, text, at))

            # ── ~60 заявок за последний месяц ──────────────────────────────────
            busy = set()          # исполнители с активной заявкой (инвариант «одна заявка»)
            counts = {}
            completed_app_ids = []
            chat_candidates = []  # (app_id, author, executor, executor_at, end_anchor)
            overdue_quota = 4     # «здоровая доска»: лимит просроченных активных заявок

            # Исполнители, уже занятые БАЗОВЫМ сидом (active-заявки 1..18): иначе demo-цикл
            # мог бы выдать им вторую активную заявку (нарушив «одна заявка/исполнитель»).
            for r in cur.execute(
                    "SELECT DISTINCT eta.employee_id AS e FROM public.employee_to_application eta "
                    "JOIN public.application a ON a.application_id = eta.application_id "
                    "JOIN public.status s ON s.status_id = a.status_id "
                    "JOIN public.role rr ON rr.role_id = eta.role_id "
                    "WHERE rr.name = 'executor' AND s.name IN ('assigned', 'inProgress')").fetchall():
                busy.add(r["e"])

            # ── Сценарные заявки для наглядности презентации (ДО основного цикла, чтобы
            # гарантированно достались свободные исполнители) ──────────────────
            # Полностью связанная заявка (ссылки автор/исполнитель + журнал). Срок —
            # «срочное окно» (≤ urgent_threshold_hours) даёт бонус срочности в формуле
            # приоритета, поэтому такие заявки видны как высокий/критичный приоритет, при
            # этом дедлайн ещё В БУДУЩЕМ (не просрочены). Отделы 1 (ИТ) и 2 (ОГЭ) имеют
            # высокие коэффициенты в priority_settings — там приоритет нагляднее.
            def mk_app(name, desc, dept, prio, status, author, executor,
                       created, deadline, executor_at=None, work_at=None, score=None):
                wt = rnd.choice(wts_by_dept[dept])
                updated = work_at or executor_at or created
                aid = cur.execute(
                    """INSERT INTO public.application
                        (name, priority_id, status_id, description, department_id, types_of_works,
                         is_unfinished, is_expired, deadline, created_at, updated_at,
                         executor_at, work_at, finished_at, result_text, priority_score)
                       VALUES (%s,%s,%s,%s,%s,%s,false,false,%s,%s,%s,%s,%s,NULL,NULL,%s)
                       RETURNING application_id""",
                    (name, priorities[prio], statuses[status], desc, dept, wt, deadline,
                     created, updated, executor_at, work_at,
                     score if score is not None else _PRIORITY_SCORE[prio])).fetchone()["application_id"]
                cur.execute("INSERT INTO public.employee_to_application (role_id, application_id, "
                            "employee_id) VALUES (%s,%s,%s)", (roles["author"], aid, author))
                if executor:
                    cur.execute("INSERT INTO public.employee_to_application (role_id, application_id, "
                                "employee_id) VALUES (%s,%s,%s)", (roles["executor"], aid, executor))
                journal(aid, None, statuses["new"], author, "create", created)
                if executor_at:
                    journal(aid, statuses["new"], statuses["assigned"], dept_manager(dept),
                            "assignExecutor", executor_at)
                if work_at:
                    journal(aid, statuses["assigned"], statuses["inProgress"], executor,
                            "startWork", work_at)
                counts[status] = counts.get(status, 0) + 1
                return aid

            _URGENT = ("Срочно: аварийная остановка линии", "Срочно: отказ системы вентиляции",
                       "Срочно: течь в гидросистеме пресса", "Срочно: сбой терминала отгрузки",
                       "Срочно: обесточен участок сборки")
            it_mgr = dept_manager(1)
            it_author = next((a for a in authors_by_dept.get(1, []) if a != it_mgr), 7)

            # (2) Срочные В СРОК: занимаем свободных исполнителей ИТ заявками «в работе».
            # Первая — критичная (автор-руководитель поднимает приоритет), но НЕ просрочена.
            for idx, emp in enumerate([e for e in execs_by_dept.get(1, []) if e not in busy]):
                busy.add(emp)
                c = now - timedelta(hours=rnd.uniform(6, 10))
                d = c + timedelta(hours=24)                       # срочное окно → бонус срочности
                ea = c + timedelta(minutes=rnd.uniform(20, 70))
                wa = ea + timedelta(minutes=rnd.uniform(30, 120))
                if idx == 0:
                    aid = mk_app("Срочно: отказ основного сервера 1С",
                                 "Высокий приоритет, работа идёт по графику.", 1, "critical",
                                 "inProgress", it_mgr or it_author, emp, c, d, ea, wa, score=0.9)
                    a_for_chat = it_mgr or it_author
                else:
                    aid = mk_app(rnd.choice(_URGENT), "Срочная заявка, исполнитель уже в работе.",
                                 1, "high", "inProgress", it_author, emp, c, d, ea, wa, score=0.72)
                    a_for_chat = it_author
                if emp != a_for_chat:
                    chat_candidates.append((aid, a_for_chat, emp, ea, wa))

            # (3) Застрявшие в очереди: все исполнители ИТ теперь заняты → две срочные НОВЫЕ
            # заявки ИТ остаются в «Новом» и эскалируются руководителю (это сделает
            # run_routing в _settle_demo). Автор — не руководитель → приоритет high (не
            # critical), вытеснения не будет.
            for _ in range(2):
                c = now - timedelta(hours=rnd.uniform(14, 20))
                d = c + timedelta(hours=24)                       # срочно, но срок ещё не вышел
                mk_app("Срочно: " + rnd.choice(("сбой почтового сервера", "отказ VPN-шлюза",
                        "вирусная атака на АРМ")), "Все исполнители ИТ заняты — ожидает освобождения.",
                       1, "high", "new", it_author, None, c, d, score=0.66)

            # Ещё одна срочная В СРОК в ОГЭ (отдел 2) на свободном исполнителе.
            free2 = [e for e in execs_by_dept.get(2, []) if e not in busy]
            if free2:
                emp = free2[0]; busy.add(emp)
                a2 = next((a for a in authors_by_dept.get(2, []) if a != dept_manager(2)),
                          dept_manager(2))
                c = now - timedelta(hours=rnd.uniform(6, 12)); d = c + timedelta(hours=24)
                ea = c + timedelta(minutes=rnd.uniform(20, 80))
                wa = ea + timedelta(minutes=rnd.uniform(30, 120))
                aid = mk_app(rnd.choice(_URGENT), "Срочная заявка ОГЭ, работа идёт.", 2, "high",
                             "inProgress", a2, emp, c, d, ea, wa, score=0.62)
                if emp != a2:
                    chat_candidates.append((aid, a2, emp, ea, wa))

            # (4) Свежие делегирования между отделами (в т.ч. на рассмотрении).
            _DELEGS = ((1, 5, None,        "Требуется бригада ОГМ — вне компетенции ИТ."),
                       (3, 2, "confirmed", "Электротехнические работы — передано в ОГЭ."),
                       (6, 7, None,        "Закупка у поставщика — на рассмотрении снабжения."))
            for frm, to, decision, comment in _DELEGS:
                c = now - timedelta(days=rnd.uniform(0.4, 3))
                author = rnd.choice(authors_by_dept.get(frm, [dept_manager(frm)]))
                decided_at = c + timedelta(hours=rnd.uniform(2, 20)) if decision else None
                drow = cur.execute(
                    "INSERT INTO public.delegated (delegated_by, delegated_by_employee, "
                    "delegated_from, delegated_to, comment, created_at, decision, decided_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING delegated_id",
                    (str(frm), dept_manager(frm), str(frm), str(to), comment, c, decision, decided_at)
                ).fetchone()["delegated_id"]
                app_dept = to if decision == "confirmed" else frm
                aid = mk_app("Делегирование: " + rnd.choice(_APP_OBJECTS), comment, app_dept,
                             "medium", "delegated", author, None, c,
                             c + timedelta(days=rnd.uniform(3, 9)))
                journal(aid, statuses["new"], statuses["delegated"], dept_manager(frm),
                        "delegate", c + timedelta(minutes=rnd.uniform(10, 120)))
                cur.execute("UPDATE public.application SET delegated_id=%s WHERE application_id=%s",
                            (drow, aid))
                cur.execute("UPDATE public.delegated SET application_id=%s WHERE delegated_id=%s",
                            (aid, drow))

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
                    # Необработанные заявки в очереди: срок комфортно в БУДУЩЕМ. Иначе
                    # периодический пересчёт приоритета поднял бы их до «критичных», и
                    # фоновый цикл начал бы вытеснять/переназначать прямо на старте —
                    # демо должно открываться стабильным, без скачков статусов.
                    created = now - timedelta(hours=rnd.uniform(1, 36))
                    deadline = now + timedelta(days=rnd.uniform(2.5, 12))
                elif status in ("assigned", "inProgress"):
                    # «Здоровая доска»: активная работа в ОСНОВНОМ идёт в срок; лишь
                    # несколько заявок (квота) просрочены — чтобы просрочка читалась как
                    # заметное исключение, а не как норма.
                    if overdue_quota > 0 and rnd.random() < 0.4:
                        deadline = now - timedelta(days=rnd.uniform(0.3, 3))
                        created = deadline - timedelta(days=rnd.uniform(1.5, 6))
                        overdue_quota -= 1
                    else:
                        created = now - timedelta(days=rnd.uniform(0.2, 6), hours=rnd.uniform(0, 12))
                        deadline = now + timedelta(days=rnd.uniform(1.5, 12))

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
                # Критичная заявка не «висит» в Новом — её распределяют немедленно
                # (а в сиде это иначе приводило бы к вытеснению на первом тике цикла).
                if status == "new" and prio == "critical":
                    prio = "high"
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
                # Кандидаты на чат: есть назначенный исполнитель и автор ≠ исполнитель.
                if executor_at and executor and executor != author:
                    end_anchor = finished or work_at or now
                    chat_candidates.append((app_id, author, executor, executor_at, end_anchor))
                counts[status] = counts.get(status, 0) + 1

            # ── Немного чатов на заявках (не слишком много) ────────────────────
            # Берём несколько заявок с назначенным исполнителем и раскладываем по ним
            # короткие диалоги ПЛОТНЫМ кластером вскоре после назначения (реплики в
            # пределах ~30 минут). Так переписка читается связно, а перенос в рабочее
            # окно (_remap_to_worktime в конце) не разбрасывает её по разным дням.
            rnd.shuffle(chat_candidates)
            for app_id, author, executor, executor_at, end_anchor in chat_candidates[:7]:
                script = rnd.choice(_CHAT_SCRIPTS)
                # Первая реплика — через несколько минут после назначения; далее с
                # небольшим шагом. Весь диалог укладывается в ~30 минут и не выходит
                # за конец жизненного цикла (finish/«сейчас»).
                start = executor_at + timedelta(minutes=rnd.uniform(4, 18))
                if start >= end_anchor:
                    start = executor_at
                gap = timedelta(minutes=rnd.uniform(3, 8))
                total = gap * (len(script) - 1)
                # Удержать весь диалог в пределах ОДНОГО местного дня: иначе перенос в
                # рабочее окно разнёс бы реплики по соседним рабочим дням.
                start_local = start + _WT_OFFSET
                day_end_local = (datetime(start_local.year, start_local.month, start_local.day,
                                          tzinfo=start_local.tzinfo) + timedelta(days=1))
                if start_local + total >= day_end_local:
                    start = max(executor_at, day_end_local - total - timedelta(minutes=1) - _WT_OFFSET)
                at = start
                for who, text in script:
                    emp = author if who == "author" else executor
                    message(app_id, emp, text, min(at, end_anchor))
                    at += gap
                counts["chat"] = counts.get("chat", 0) + 1

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

            # ── Перенос ВСЕХ таймстампов демо-данных в рабочее окно ────────────
            # Рабочие часы [start, end] и рабочие дни из config.json → "worktime".
            # Монотонно, поэтому порядок событий внутри заявки сохраняется. Делаем ДО
            # «устаканивания», чтобы маршрутизация ниже шла на тех же данных, что увидит
            # фоновый цикл (иначе его первый тик переназначал бы из-за сдвига кулдаунов).
            # ceiling = последний момент рабочего окна ≤ now: потолок для исторических
            # меток, чтобы при сидировании вне рабочих часов они не уехали в будущее.
            ceiling = _recent_in_window(now)
            _remap_to_worktime(cur, ceiling)

            # Снимок «до маршрутизации»: позволит найти и сдвинуть в прошлое строки,
            # которые создаст разовый проход run_routing в _settle_demo (ниже).
            before_notif = cur.execute(
                "SELECT COALESCE(MAX(notification_id), 0) AS m FROM public.notification"
            ).fetchone()["m"]
            before_hist = cur.execute(
                "SELECT COALESCE(MAX(id), 0) AS m FROM public.application_status_history"
            ).fetchone()["m"]
            new_before = {r["application_id"] for r in cur.execute(
                "SELECT a.application_id FROM public.application a "
                "JOIN public.status s ON s.status_id = a.status_id WHERE s.name = 'new'"
            ).fetchall()}

    # Генерация закоммичена. Доводим мир до неподвижной точки подсистем (маршрутизация
    # + просрочка/дедлайн) с уведомлениями из ПРОШЛОГО и проставляем дедуп-флаги, чтобы
    # первый тик фонового цикла НЕ присылал лавину «сейчас». Перенос в рабочее окно —
    # последним шагом внутри _settle_demo.
    settled = _settle_demo(db_operator, rnd, now, ceiling, before_notif, before_hist, new_before)
    counts["preExpired"] = settled["expired"]
    counts["preDeadline"] = settled["deadlineNotifications"]

    print(f"[seed:demo] extra employees → {len(_EXTRA_EMPLOYEES)}, "
          f"generated applications → {counts}, worktime "
          f"{_WT_START // 3600:02d}:00–{_WT_END // 3600:02d}:00")
