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
    """Снять «критичность» с прочих new-заявок, чтобы глобальный проход маршрутизации
    не вытеснил нашу назначенную заявку (run_routing обрабатывает все new-заявки)."""
    with sysdb.pool.connection() as c:
        low = c.execute("SELECT priority_id FROM public.priority WHERE name='low'").fetchone()[0]
        c.execute(
            "UPDATE public.application a SET priority_id=%s FROM public.status s "
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
    _busy_executor(EXEC1)   # обе ИТ-единицы заняты
    _busy_executor(EXEC2)
    app = _create_app(DEP_IT, WT_IT_EASY)
    _set_priority_score(sysdb, app, 9.0)

    routing_module.run_routing(sysdb)

    assert _app(app)["status"] == "new"     # не критичная, свободных нет → ждёт


def test_grade_matrix_filters_candidates(sysdb):
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    app = _create_app(DEP_IT, WT_IT_HARD)   # требует senior/lead, оба исполнителя middle
    _set_priority_score(sysdb, app, 9.0)

    routing_module.run_routing(sysdb)

    assert _app(app)["status"] == "new"     # нет подходящих по грейду → ждёт


def test_critical_evicts_lowest_priority(sysdb):
    _free_executor(sysdb, EXEC1)
    _free_executor(sysdb, EXEC2)
    app_hi = _busy_executor(EXEC1)          # займём emp2 заявкой с высоким приоритетом
    app_lo = _busy_executor(EXEC2)          # emp3 — с низким
    _set_priority_score(sysdb, app_hi, 0.7)
    _set_priority_score(sysdb, app_lo, 0.1)

    crit = _create_app(DEP_IT, WT_IT_EASY)
    _set_critical(sysdb, crit)

    routing_module.run_routing(sysdb)

    c = _app(crit)
    assert c["status"] == "assigned"
    assert c["executorId"] == "3"           # вытеснён исполнитель с наименее приоритетной заявкой
    low = _app(app_lo)
    assert low["status"] == "new"
    assert low["isUnfinished"] is True
    assert str(low.get("previousExecutorId")) == "3"


def test_cooldown_blocks_recently_finished(sysdb):
    _free_executor(sysdb, EXEC1)
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
        _set_priority_score(sysdb, app, 9.0)
        routing_module.run_routing(sysdb)
        assert _app(app)["status"] == "new"  # emp2 на кулдауне, emp3 занят → ждёт
    finally:
        _set_dept_delay(sysdb, DEP_IT, 30)   # вернуть как в seed
