"""
Tests for the events subsystem (events_module.run_tick): deadline-approaching
notifications and overdue marking.

run_tick is called directly (the background loop ticks every 60s, so it never fires
during the fast test run). A system DB operator (postgres) is used both to drive the
tick and to inspect internal fields (is_expired / deadline_notified are not exposed
in the API). The server must be running with seeded data (same harness as the others).
"""

import os
from datetime import datetime, timezone, timedelta

import pytest
import requests

from src.application_module import PgDbOperator
from src import events_module

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")
MANAGER = ("orlova_m", "Manager!1")   # top-manager, IT (dept 1)
DEP_IT, WORK_TYPE_IT = 1, 1

_session = requests.Session()


@pytest.fixture(scope="module")
def sysdb():
    return PgDbOperator("postgres", "postgres")


def _create_app(deadline_at: str):
    body = {
        "name": "Events test application",
        "departmentId": str(DEP_IT),
        "workTypeId": str(WORK_TYPE_IT),
        "deadlineAt": deadline_at,
        "description": "Events test",
    }
    r = _session.post(f"{BASE_URL}/applications", auth=MANAGER, json=body)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


def _act(app_id, auth, **payload):
    return _session.post(f"{BASE_URL}/applications/{app_id}/actions", auth=auth, json=payload)


def _scalar(db, sql, params):
    with db.pool.connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _is_expired(db, app_id):
    return _scalar(db, "SELECT is_expired FROM public.application WHERE application_id = %s", (app_id,))


def _deadline_notified(db, app_id):
    return _scalar(db, "SELECT deadline_notified FROM public.application WHERE application_id = %s", (app_id,))


def _notif_count(db, app_id):
    return _scalar(db, "SELECT COUNT(*) FROM public.notification WHERE application_id = %s", (app_id,))


def test_overdue_marks_is_expired_and_notifies(sysdb):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    app_id = _create_app(past)

    assert _is_expired(sysdb, app_id) in (False, None)
    events_module.run_tick(sysdb)

    assert _is_expired(sysdb, app_id) is True
    assert _notif_count(sysdb, app_id) >= 1   # IT manager notified of the overdue app


def test_overdue_is_idempotent(sysdb):
    past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    app_id = _create_app(past)

    events_module.run_tick(sysdb)
    count_after_first = _notif_count(sysdb, app_id)
    events_module.run_tick(sysdb)
    count_after_second = _notif_count(sysdb, app_id)

    assert count_after_first >= 1
    assert count_after_second == count_after_first   # no duplicate notification


def test_deadline_approaching_notifies(sysdb):
    # Deadline 1h out; backdate created_at so ~10% of the window remains (<= IT's 0.8).
    deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    app_id = _create_app(deadline.isoformat())
    with sysdb.pool.connection() as conn:
        conn.execute(
            "UPDATE public.application SET created_at = %s WHERE application_id = %s",
            (datetime.now(timezone.utc) - timedelta(hours=9), app_id),
        )

    assert _deadline_notified(sysdb, app_id) in (False, None)
    events_module.run_tick(sysdb)

    assert _deadline_notified(sysdb, app_id) is True
    assert _notif_count(sysdb, app_id) >= 1


def test_deadline_approaching_notifies_executor_too(sysdb):
    # An assigned application near its deadline notifies BOTH the manager and the
    # assigned executor (new rule: recipients = managers + executor).
    deadline = datetime.now(timezone.utc) + timedelta(hours=1)
    app_id = _create_app(deadline.isoformat())
    # Assign executor ivanov (id 2), then backdate created_at so the window is nearly up.
    assert _act(app_id, MANAGER, action="assignExecutor", executorId="2").status_code == 204
    with sysdb.pool.connection() as conn:
        conn.execute(
            "UPDATE public.application SET created_at = %s WHERE application_id = %s",
            (datetime.now(timezone.utc) - timedelta(hours=9), app_id),
        )

    events_module.run_tick(sysdb)

    assert _deadline_notified(sysdb, app_id) is True
    # The assigned executor (employee_id 2) must have a notification for this app.
    got = _scalar(
        sysdb,
        "SELECT COUNT(*) FROM public.notification WHERE application_id = %s AND employee_id = 2",
        (app_id,),
    )
    assert got >= 1


def test_far_deadline_not_notified(sysdb):
    # Plenty of time left (~100% remaining) → no deadline notification yet.
    deadline = datetime.now(timezone.utc) + timedelta(days=365)
    app_id = _create_app(deadline.isoformat())

    events_module.run_tick(sysdb)

    assert _deadline_notified(sysdb, app_id) in (False, None)
    assert _notif_count(sysdb, app_id) == 0
