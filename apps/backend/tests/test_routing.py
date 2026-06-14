"""
Integration tests for the routing subsystem (routing_module.run_routing).

Сценарии готовятся через системный db-оператор (как в test_events): мы делаем нужного
исполнителя свободным/занятым и задаём приоритеты, затем вызываем run_routing напрямую и
проверяем результат через API. Imports `src` → запуск внутри контейнера.

Известные данные: ИТ-отдел (dept 1), исполнители ivanov(emp 2) и petrov(emp 3), оба грейд
«middle». Вид работ 1 (it_replace, easy → допустимы junior/middle); вид работ 3
(it_server, hard → допустимы senior/lead, middle НЕ подходит).
"""

import os
from datetime import datetime, timezone

import pytest
import requests

from src.application_module import PgDbOperator
from src import routing_module

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")
MANAGER = ("orlova_m", "Manager!1")
DEP_IT = 1
WT_IT_EASY = 1     # допускает middle
WT_IT_HARD = 3     # требует senior/lead — middle не подходит
EXEC1, EXEC2 = 2, 3   # ivanov, petrov (оба middle, ИТ)

_session = requests.Session()


@pytest.fixture(scope="module")
def sysdb():
    return PgDbOperator("postgres", "postgres")


def _create_app(dept=DEP_IT, wt=WT_IT_EASY):
    body = {"name": "Routing test", "departmentId": str(dept), "workTypeId": str(wt),
            "deadlineAt": "2030-01-01T00:00:00Z", "description": "routing test"}
    r = _session.post(f"{BASE_URL}/applications", auth=MANAGER, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _assign(app_id, emp_id):
    r = _session.post(f"{BASE_URL}/applications/{app_id}/actions", auth=MANAGER,
                      json={"action": "assignExecutor", "executorId": str(emp_id)})
    assert r.status_code == 204, r.text


def _app(app_id):
    return _session.get(f"{BASE_URL}/applications/{app_id}", auth=MANAGER).json()["application"]


def _exec_role_id(sysdb):
    with sysdb.pool.connection() as c:
        return c.execute("SELECT role_id FROM public.role WHERE name='executor'").fetchone()[0]


def _free_executor(sysdb, emp_id):
    """Снять все executor-связи сотрудника → нет активной заявки и истории завершений."""
    er = _exec_role_id(sysdb)
    with sysdb.pool.connection() as c:
        c.execute("DELETE FROM public.employee_to_application WHERE employee_id=%s AND role_id=%s",
                  (emp_id, er))


def _busy_executor(emp_id):
    app = _create_app()
    _assign(app, emp_id)
    return app


def _set_priority_score(sysdb, app_id, score):
    with sysdb.pool.connection() as c:
        c.execute("UPDATE public.application SET priority_score=%s WHERE application_id=%s",
                  (score, int(app_id)))


def _set_critical(sysdb, app_id):
    with sysdb.pool.connection() as c:
        pid = c.execute("SELECT priority_id FROM public.priority WHERE name='critical'").fetchone()[0]
        c.execute("UPDATE public.application SET priority_id=%s, priority_score=5.0 WHERE application_id=%s",
                  (pid, int(app_id)))


def _set_dept_delay(sysdb, dept_id, minutes):
    with sysdb.pool.connection() as c:
        c.execute("UPDATE public.department SET empl_appl_delay=%s WHERE department_id=%s",
                  (minutes, dept_id))


# ── Сценарии ──────────────────────────────────────────────────────────────────

def _demote_other_new_apps(sysdb, keep_app_id):
    """Снять «критичность» и обнулить score прочих new-заявок, чтобы глобальный проход
    маршрутизации обработал нашу заявку первой и ничего не вытеснил (run_routing
    обрабатывает все new-заявки по убыванию priority_score)."""
    with sysdb.pool.connection() as c:
        low = c.execute("SELECT priority_id FROM public.priority WHERE name='low'").fetchone()[0]
        c.execute(
            "UPDATE public.application a SET priority_id=%s, priority_score=0 FROM public.status s "
            "WHERE s.status_id=a.status_id AND s.name='new' AND a.application_id <> %s",
            (low, int(keep_app_id)),
        )


def test_auto_assign_to_free_executor(sysdb):
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    app = _create_app(DEP_IT, WT_IT_EASY)
    _demote_other_new_apps(sysdb, app)     # убрать конкурирующие критичные заявки
    _set_priority_score(sysdb, app, 9.0)   # обработать первой

    routing_module.run_routing(sysdb)

    a = _app(app)
    assert a["status"] == "assigned"
    assert a["executorId"] is not None
    # назначен исполнителю своего (ИТ) отдела
    assert a["executor"] and a["executor"]["departmentId"] == str(DEP_IT)


def test_no_free_executor_stays_new(sysdb):
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    try:
        _busy_executor(EXEC1)   # обе ИТ-единицы заняты
        _busy_executor(EXEC2)
        app = _create_app(DEP_IT, WT_IT_EASY)
        _demote_other_new_apps(sysdb, app)
        _set_priority_score(sysdb, app, 9.0)

        routing_module.run_routing(sysdb)

        assert _app(app)["status"] == "new"     # не критичная, свободных нет → ждёт
    finally:
        _reactivate_executors(sysdb, others)


def test_grade_matrix_filters_candidates(sysdb):
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    try:
        app = _create_app(DEP_IT, WT_IT_HARD)   # требует senior/lead, оба исполнителя middle
        _demote_other_new_apps(sysdb, app)
        _set_priority_score(sysdb, app, 9.0)

        routing_module.run_routing(sysdb)

        assert _app(app)["status"] == "new"     # нет подходящих по грейду → ждёт
    finally:
        _reactivate_executors(sysdb, others)


def test_critical_evicts_lowest_priority(sysdb):
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    try:
        app_hi = _busy_executor(EXEC1)          # займём emp2 заявкой с высоким приоритетом
        app_lo = _busy_executor(EXEC2)          # emp3 — с низким
        _set_priority_score(sysdb, app_hi, 0.7)
        _set_priority_score(sysdb, app_lo, 0.1)

        crit = _create_app(DEP_IT, WT_IT_EASY)
        _demote_other_new_apps(sysdb, crit)
        _set_critical(sysdb, crit)

        routing_module.run_routing(sysdb)

        c = _app(crit)
        assert c["status"] == "assigned"
        assert c["executorId"] == "3"           # вытеснён исполнитель с наименее приоритетной заявкой
        low = _app(app_lo)
        assert low["status"] == "new"
        assert low["isUnfinished"] is True
        assert str(low.get("previousExecutorId")) == "3"
    finally:
        _reactivate_executors(sysdb, others)


def test_critical_on_create_is_routed_immediately(sysdb):
    """Критичная при создании заявка распределяется СРАЗУ (без вызова run_routing и без
    ожидания фонового тика) — через немедленный триггер в create_application."""
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    # Дедлайн в прошлом → k_времени=1; для ИТ (k_отдела высокий) score≥0.82 → critical уже
    # при создании. Тик маршрутизации НЕ вызываем — проверяем именно немедленный триггер.
    body = {"name": "Critical on create", "departmentId": str(DEP_IT), "workTypeId": str(WT_IT_EASY),
            "deadlineAt": "2000-01-01T00:00:00Z", "description": "overdue on create"}
    r = _session.post(f"{BASE_URL}/applications", auth=MANAGER, json=body)
    assert r.status_code == 201, r.text
    app_id = r.json()["id"]

    a = _app(app_id)
    assert a["priority"] == "critical", a
    assert a["status"] == "assigned"             # распределена немедленно
    assert a["executor"] and a["executor"]["departmentId"] == str(DEP_IT)


def test_non_critical_on_create_waits_for_tick(sysdb):
    """Обычная (не критичная) заявка при создании НЕ распределяется немедленно —
    остаётся в `new` до фонового тика (немедленный триггер только для критичных)."""
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    # Далёкий дедлайн → k_времени≈0, не срочно → low, не critical.
    body = {"name": "Low on create", "departmentId": str(DEP_IT), "workTypeId": str(WT_IT_EASY),
            "deadlineAt": "2030-01-01T00:00:00Z", "description": "far deadline"}
    r = _session.post(f"{BASE_URL}/applications", auth=MANAGER, json=body)
    assert r.status_code == 201, r.text
    a = _app(r.json()["id"])
    assert a["priority"] != "critical"
    assert a["status"] == "new"                  # ждёт тик, немедленного назначения нет


def _deactivate_other_it_executors(sysdb):
    """Временно выключить «лишних» активных исполнителей ИТ (кроме EXEC1/EXEC2):
    ранние тесты (test_endpoints) добавляют в ИТ новых исполнителей из AD, и без
    этого критичной заявке нашёлся бы свободный кандидат. Возвращает их ids."""
    er = _exec_role_id(sysdb)
    with sysdb.pool.connection() as c:
        rows = c.execute(
            "SELECT employee_id FROM public.employee "
            "WHERE department_id=%s AND role_id=%s AND is_active=true "
            "AND employee_id NOT IN (%s, %s)",
            (DEP_IT, er, EXEC1, EXEC2),
        ).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            c.execute("UPDATE public.employee SET is_active=false WHERE employee_id = ANY(%s)", (ids,))
    return ids


def _reactivate_executors(sysdb, ids):
    if ids:
        with sysdb.pool.connection() as c:
            c.execute("UPDATE public.employee SET is_active=true WHERE employee_id = ANY(%s)", (ids,))


def test_critical_escalates_when_no_one_evictable(sysdb):
    """Все подходящие исполнители заняты КРИТИЧНЫМИ заявками → вытеснять некого:
    критичная заявка остаётся в `new`, руководителю уходит эскалация (однократно)."""
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)   # только EXEC1/EXEC2 в пуле ИТ
    busy1 = _busy_executor(EXEC1)
    busy2 = _busy_executor(EXEC2)
    _set_critical(sysdb, busy1)            # критичные текущие заявки не вытесняются
    _set_critical(sysdb, busy2)

    crit = _create_app(DEP_IT, WT_IT_EASY)
    _demote_other_new_apps(sysdb, crit)    # чужие new-заявки не должны вмешиваться
    _set_critical(sysdb, crit)

    def _notif_count():
        with sysdb.pool.connection() as c:
            return c.execute("SELECT COUNT(*) FROM public.notification WHERE application_id=%s",
                             (int(crit),)).fetchone()[0]

    try:
        routing_module.run_routing(sysdb)

        assert _app(crit)["status"] == "new"           # не распределена и никого не вытеснила
        assert _app(busy1)["status"] == "assigned"
        assert _app(busy2)["status"] == "assigned"
        with sysdb.pool.connection() as c:
            flag = c.execute("SELECT escalation_notified FROM public.application "
                             "WHERE application_id=%s", (int(crit),)).fetchone()[0]
        assert flag is True
        first = _notif_count()
        assert first >= 1                              # руководитель уведомлён

        routing_module.run_routing(sysdb)              # повторный проход — без дублей
        assert _notif_count() == first
    finally:
        _reactivate_executors(sysdb, others)
        # Не оставляем критичную new-заявку в очереди — она вытеснила бы чужие
        # назначения в последующих тестах/тиках.
        with sysdb.pool.connection() as c:
            low = c.execute("SELECT priority_id FROM public.priority WHERE name='low'").fetchone()[0]
            for app_id in (crit, busy1, busy2):
                c.execute("UPDATE public.application SET priority_id=%s, priority_score=0.0 "
                          "WHERE application_id=%s", (low, int(app_id)))


def test_unassignable_regular_app_notifies_manager_once(sysdb):
    """Обычная (не критичная) заявка, которой не нашлось свободного подходящего
    исполнителя, остаётся в `new`, а руководитель отдела получает уведомление —
    однократно (дедуп через escalation_notified)."""
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    try:
        _busy_executor(EXEC1)
        _busy_executor(EXEC2)
        app = _create_app(DEP_IT, WT_IT_EASY)
        _demote_other_new_apps(sysdb, app)
        _set_priority_score(sysdb, app, 9.0)

        def _mgr_notifs():
            with sysdb.pool.connection() as c:
                return c.execute(
                    "SELECT COUNT(*) FROM public.notification n "
                    "JOIN public.employee e ON e.employee_id = n.employee_id "
                    "JOIN public.role r ON r.role_id = e.role_id "
                    "WHERE n.application_id = %s AND r.name IN ('manager', 'top-manager')",
                    (int(app),),
                ).fetchone()[0]

        routing_module.run_routing(sysdb)
        assert _app(app)["status"] == "new"     # свободных нет, заявка ждёт
        first = _mgr_notifs()
        assert first >= 1                       # руководитель отдела уведомлён

        routing_module.run_routing(sysdb)
        assert _mgr_notifs() == first           # повторный проход — без дублей

        _free_executor(sysdb, EXEC1)            # исполнитель освободился
        routing_module.run_routing(sysdb)
        assert _app(app)["status"] == "assigned"  # заявка распределена (флаг сброшен)
    finally:
        _reactivate_executors(sysdb, others)


EXEC1_AUTH = ("ivanov_i", "SecretPassword!1")   # executor ivanov (emp 2)


def _set_internal_confirmation(required: bool):
    r = _session.patch(f"{BASE_URL}/departments/{DEP_IT}/delegation-settings", auth=MANAGER,
                       json={"delegatedToSameDepartment": required})
    assert r.status_code == 204, r.text


def test_pending_internal_delegation_blocks_auto_assignment(sysdb):
    """Исполнитель с внутренним делегированием на подтверждении считается ЗАНЯТЫМ:
    при отклонении руководителем заявка вернётся ему, и авто-назначение второй заявки
    нарушило бы правило «одна заявка на исполнителя»."""
    _set_internal_confirmation(True)
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    try:
        pending = _create_app()
        _assign(pending, EXEC1)
        r = _session.post(f"{BASE_URL}/applications/{pending}/actions", auth=EXEC1_AUTH,
                          json={"action": "delegateInternal", "complexity": "hard"})
        assert r.status_code == 204, r.text
        assert _app(pending)["status"] == "delegated"    # ждёт подтверждения
        _busy_executor(EXEC2)                            # второй исполнитель занят

        app = _create_app(DEP_IT, WT_IT_EASY)
        _demote_other_new_apps(sysdb, app)
        _set_priority_score(sysdb, app, 9.0)
        routing_module.run_routing(sysdb)

        assert _app(app)["status"] == "new"              # EXEC1 «занят» делегированием

        # Руководитель отклонил → заявка вернулась исполнителю, активная заявка одна.
        r = _session.post(f"{BASE_URL}/applications/{pending}/actions", auth=MANAGER,
                          json={"action": "declineExternalDelegation"})
        assert r.status_code == 204, r.text
        restored = _app(pending)
        assert restored["status"] == "assigned"
        assert restored["executorId"] == str(EXEC1)
    finally:
        _set_internal_confirmation(False)
        _reactivate_executors(sysdb, others)


def test_min_capable_executor_preferred(sysdb):
    """«Минимально способный»: при двух свободных подходящих исполнителях заявка уходит
    исполнителю с МЕНЬШИМ грейдом (junior раньше middle)."""
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    with sysdb.pool.connection() as c:
        orig_pg = c.execute("SELECT post_grade_id FROM public.employee WHERE employee_id=%s",
                            (EXEC2,)).fetchone()[0]
        jr = c.execute("SELECT post_grade_id FROM public.post_grade WHERE grade_grade_id=0 LIMIT 1").fetchone()
        if jr is None:
            post = c.execute("SELECT post_id FROM public.post LIMIT 1").fetchone()[0]
            jr = c.execute("INSERT INTO public.post_grade (post_post_id, grade_grade_id) "
                           "VALUES (%s, 0) RETURNING post_grade_id", (post,)).fetchone()
        c.execute("UPDATE public.employee SET post_grade_id=%s WHERE employee_id=%s",
                  (jr[0], EXEC2))   # petrov теперь junior; ivanov остаётся middle
    try:
        app = _create_app(DEP_IT, WT_IT_EASY)     # easy → допустимы junior и middle
        _demote_other_new_apps(sysdb, app)
        _set_priority_score(sysdb, app, 9.0)

        routing_module.run_routing(sysdb)

        a = _app(app)
        assert a["status"] == "assigned"
        assert a["executorId"] == str(EXEC2), "junior должен быть предпочтён middle"
    finally:
        with sysdb.pool.connection() as c:
            c.execute("UPDATE public.employee SET post_grade_id=%s WHERE employee_id=%s",
                      (orig_pg, EXEC2))
        _reactivate_executors(sysdb, others)


def test_position_axis_filters_candidates(sysdb):
    """Допуск по двум осям: должность И грейд. ivanov (emp 2) — Инженер-middle,
    petrov (emp 3) — Старший инженер-middle: грейд у обоих подходит, но вид работ,
    ограниченный должностью «Старший инженер», должен достаться только petrov.
    Пустая матрица должностей (старые виды работ) ограничения не накладывает."""
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    wt_id = None
    try:
        with sysdb.pool.connection() as c:
            senior_post = c.execute(
                "SELECT post_id FROM public.post WHERE name = 'Старший инженер'").fetchone()[0]
            wt_id = c.execute(
                "INSERT INTO public.types_of_works (name, complexity_value, department_id) "
                "VALUES ('Только для старших', 2, %s) RETURNING type_of_works_id",
                (DEP_IT,)).fetchone()[0]
            for g in (0, 1):   # junior, middle — грейды обоих исполнителей подходят
                c.execute("INSERT INTO public.type_of_work_to_grade (type_of_works_id, grade_id) "
                          "VALUES (%s, %s)", (wt_id, g))
            c.execute("INSERT INTO public.type_of_work_to_post (type_of_works_id, post_id) "
                      "VALUES (%s, %s)", (wt_id, senior_post))

        app = _create_app(DEP_IT, wt_id)
        _demote_other_new_apps(sysdb, app)
        _set_priority_score(sysdb, app, 9.0)

        routing_module.run_routing(sysdb)

        a = _app(app)
        assert a["status"] == "assigned"
        assert a["executorId"] == str(EXEC2), \
            "ivanov (Инженер) не подходит по должности — заявка должна уйти petrov (Старший инженер)"
    finally:
        if wt_id is not None:
            with sysdb.pool.connection() as c:
                c.execute("DELETE FROM public.type_of_work_to_post WHERE type_of_works_id = %s", (wt_id,))
                c.execute("DELETE FROM public.type_of_work_to_grade WHERE type_of_works_id = %s", (wt_id,))
        _reactivate_executors(sysdb, others)


def test_position_axis_no_match_waits(sysdb):
    """Если по должности не подходит никто (а по грейду подходят) — заявка ждёт в new
    и руководителю уходит эскалация."""
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    others = _deactivate_other_it_executors(sysdb)
    wt_id = None
    try:
        with sysdb.pool.connection() as c:
            spec_post = c.execute(
                "SELECT post_id FROM public.post WHERE name = 'Специалист'").fetchone()[0]
            wt_id = c.execute(
                "INSERT INTO public.types_of_works (name, complexity_value, department_id) "
                "VALUES ('Только для специалистов', 1, %s) RETURNING type_of_works_id",
                (DEP_IT,)).fetchone()[0]
            for g in (0, 1):
                c.execute("INSERT INTO public.type_of_work_to_grade (type_of_works_id, grade_id) "
                          "VALUES (%s, %s)", (wt_id, g))
            c.execute("INSERT INTO public.type_of_work_to_post (type_of_works_id, post_id) "
                      "VALUES (%s, %s)", (wt_id, spec_post))   # в ИТ специалистов нет

        app = _create_app(DEP_IT, wt_id)
        _demote_other_new_apps(sysdb, app)
        _set_priority_score(sysdb, app, 9.0)

        routing_module.run_routing(sysdb)

        assert _app(app)["status"] == "new"   # по должности никто не подходит → ждёт
    finally:
        if wt_id is not None:
            with sysdb.pool.connection() as c:
                c.execute("DELETE FROM public.type_of_work_to_post WHERE type_of_works_id = %s", (wt_id,))
                c.execute("DELETE FROM public.type_of_work_to_grade WHERE type_of_works_id = %s", (wt_id,))
        _reactivate_executors(sysdb, others)


def test_cooldown_blocks_recently_finished(sysdb):
    _free_executor(sysdb, EXEC1)
    others = _deactivate_other_it_executors(sysdb)
    try:
        _busy_executor(EXEC2)                    # emp3 занят
        done = _create_app()                     # emp2 только что завершил заявку
        _assign(done, EXEC1)
        with sysdb.pool.connection() as c:
            comp = c.execute("SELECT status_id FROM public.status WHERE name='completed'").fetchone()[0]
            c.execute("UPDATE public.application SET status_id=%s, finished_at=now() WHERE application_id=%s",
                      (comp, int(done)))
        _set_dept_delay(sysdb, DEP_IT, 99999)    # большой кулдаун
        try:
            app = _create_app(DEP_IT, WT_IT_EASY)
            _demote_other_new_apps(sysdb, app)
            _set_priority_score(sysdb, app, 9.0)
            routing_module.run_routing(sysdb)
            assert _app(app)["status"] == "new"  # emp2 на кулдауне, emp3 занят → ждёт
        finally:
            _set_dept_delay(sysdb, DEP_IT, 30)   # вернуть как в seed
    finally:
        _reactivate_executors(sysdb, others)
