"""
Integration tests for management-subsystem events added in the data-contract work:
  - notifications created on assignExecutor / delegateExternal / confirm delegation;
  - external-delegation confirm moves the application to the target department (bug fix);
  - reports are department-scoped for a regular manager (bug fix).

Server must be running with freshly seeded data (same harness as test_endpoints.py).
"""

import os
import requests

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")

TOP_MANAGER  = ("orlova_m",    "Manager!1")      # top-manager, IT (dept 1), all depts
DEPT_MANAGER = ("kuznetsov_m", "Kuznetsov!7")    # plain manager, OGE (dept 2)
EXECUTOR_IT  = ("ivanov_i",    "SecretPassword!1")  # executor, IT, employee_id 2
AUTHOR_IT    = ("fedorov_a",   "Fedorov!6")      # author, IT, employee_id 7

DEP_IT, DEP_OGE = 1, 2
WORK_TYPE_IT = 1
EXECUTOR_IT_ID = "2"
OGE_MANAGER_ID = "8"   # kuznetsov_m

_session = requests.Session()


def _create_app(auth, department_id=DEP_IT, work_type_id=WORK_TYPE_IT):
    body = {
        "name": "Mgmt-event test application",
        "departmentId": str(department_id),
        "workTypeId": str(work_type_id),
        "deadlineAt": "2030-01-01T00:00:00Z",
        "description": "Mgmt-event test description",
    }
    r = _session.post(f"{BASE_URL}/applications", auth=auth, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _act(app_id, auth, **payload):
    return _session.post(f"{BASE_URL}/applications/{app_id}/actions", auth=auth, json=payload)


def _notifications(auth):
    return _session.get(f"{BASE_URL}/notifications", auth=auth).json()


def _app(app_id, auth):
    return _session.get(f"{BASE_URL}/applications/{app_id}", auth=auth).json()["application"]


def _has_notification_for_app(auth, app_id):
    items = _notifications(auth)["items"]
    return any(str(i.get("applicationId")) == str(app_id) for i in items)


def test_assign_executor_notifies_executor():
    app_id = _create_app(TOP_MANAGER, DEP_IT)
    assert _act(app_id, TOP_MANAGER, action="assignExecutor", executorId=EXECUTOR_IT_ID).status_code == 204
    assert _has_notification_for_app(EXECUTOR_IT, app_id), "executor should be notified of assignment"


def test_delegate_external_notifies_receiving_manager():
    app_id = _create_app(AUTHOR_IT, DEP_IT)
    # A manager (top-manager here) delegates the IT application to OGE.
    assert _act(app_id, TOP_MANAGER, action="delegateExternal", departmentId=str(DEP_OGE)).status_code == 204
    assert _has_notification_for_app(DEPT_MANAGER, app_id), "receiving OGE manager should be notified"


def test_confirm_external_delegation_moves_department():
    app_id = _create_app(AUTHOR_IT, DEP_IT)
    assert _act(app_id, TOP_MANAGER, action="delegateExternal", departmentId=str(DEP_OGE)).status_code == 204

    before = _app(app_id, TOP_MANAGER)
    assert str(before["departmentId"]) == str(DEP_IT)        # still original while pending

    assert _act(app_id, DEPT_MANAGER, action="confirmExternalDelegation").status_code == 204

    after = _app(app_id, TOP_MANAGER)
    assert str(after["departmentId"]) == str(DEP_OGE)        # now owned by the target dept
    assert after["status"] == "new"


def test_reassign_releases_previous_application():
    """One application per executor: assigning a busy executor to a new application
    releases their previous one back to `new` with the Unfinished flag."""
    app1 = _create_app(TOP_MANAGER, DEP_IT)
    assert _act(app1, TOP_MANAGER, action="assignExecutor", executorId=EXECUTOR_IT_ID).status_code == 204
    assert _app(app1, TOP_MANAGER)["status"] == "assigned"

    app2 = _create_app(TOP_MANAGER, DEP_IT)
    assert _act(app2, TOP_MANAGER, action="assignExecutor", executorId=EXECUTOR_IT_ID).status_code == 204

    # New application is now assigned to the executor…
    after2 = _app(app2, TOP_MANAGER)
    assert after2["status"] == "assigned"
    assert str(after2["executorId"]) == EXECUTOR_IT_ID

    # …and the previous one was released back to `new`, flagged Unfinished.
    after1 = _app(app1, TOP_MANAGER)
    assert after1["status"] == "new"
    assert after1["isUnfinished"] is True
    assert str(after1["previousExecutorId"]) == EXECUTOR_IT_ID


EXECUTOR_OGE = ("sidorova_a", "Sidorova!3")   # executor, OGE (dept 2), employee_id 4
MGR_OGE = DEPT_MANAGER                          # kuznetsov_m — manager of OGE


def test_internal_delegation_with_confirmation_notifies_manager():
    # Ensure OGE requires confirmation for internal delegation (другие тесты могли
    # переключить флаг — задаём явно, чтобы тест был самодостаточным).
    assert _session.patch(f"{BASE_URL}/departments/{DEP_OGE}/delegation-settings",
                          auth=TOP_MANAGER, json={"delegatedToSameDepartment": True}).status_code == 204
    # При включённом подтверждении внутреннее делегирование уходит в статус `delegated`,
    # и руководитель отдела должен получить уведомление о необходимости подтвердить.
    wts = _session.get(f"{BASE_URL}/work-types", auth=TOP_MANAGER,
                       params={"departmentId": str(DEP_OGE)}).json()["items"]
    assert wts, "OGE must have work types"
    app_id = _create_app(TOP_MANAGER, DEP_OGE, wts[0]["id"])
    assert _act(app_id, TOP_MANAGER, action="assignExecutor", executorId="4").status_code == 204
    # Assigned OGE executor re-addresses internally (complexity not below current).
    assert _act(app_id, EXECUTOR_OGE, action="delegateInternal", complexity="critical").status_code == 204

    assert _app(app_id, TOP_MANAGER)["status"] == "delegated"      # ушло на подтверждение
    assert _has_notification_for_app(MGR_OGE, app_id), "OGE manager should be notified to confirm"


def _patch_department(dep_id, auth, body):
    return _session.patch(f"{BASE_URL}/departments/{dep_id}", auth=auth, json=body)


def test_manager_updates_own_department_settings():
    # OGE manager updates their own department's cooldown + deadline ratio.
    assert _patch_department(DEP_OGE, DEPT_MANAGER,
                             {"employeeApplicationDelayMinutes": 90,
                              "deadlineNotificationRatio": 0.3}).status_code == 204
    deps = _session.get(f"{BASE_URL}/departments", auth=DEPT_MANAGER).json()["items"]
    oge = next(d for d in deps if str(d["id"]) == str(DEP_OGE))
    assert oge["employeeApplicationDelayMinutes"] == 90
    assert oge["deadlineNotificationRatio"] == 0.3


def test_manager_cannot_update_other_department():
    assert _patch_department(DEP_IT, DEPT_MANAGER,
                             {"employeeApplicationDelayMinutes": 5}).status_code == 403


def test_executor_cannot_update_department():
    assert _patch_department(DEP_OGE, EXECUTOR_IT,
                             {"employeeApplicationDelayMinutes": 5}).status_code == 403


def test_update_department_out_of_range_422():
    assert _patch_department(DEP_OGE, TOP_MANAGER,
                             {"deadlineNotificationRatio": 1.5}).status_code == 422


def test_reports_are_department_scoped_for_plain_manager():
    departments = _session.get(f"{BASE_URL}/departments", auth=DEPT_MANAGER).json()["items"]
    oge_name = next(d["name"] for d in departments if str(d["id"]) == str(DEP_OGE))

    rows = _session.get(f"{BASE_URL}/reports/applications", auth=DEPT_MANAGER).json()["items"]
    # Every row a plain OGE manager sees must belong to OGE.
    assert all(r.get("departmentName") == oge_name for r in rows), \
        "plain manager must only see their own department in reports"

    # A top-manager is not restricted — sees at least as many rows, incl. other depts.
    all_rows = _session.get(f"{BASE_URL}/reports/applications", auth=TOP_MANAGER).json()["items"]
    assert len(all_rows) >= len(rows)
    assert any(r.get("departmentName") != oge_name for r in all_rows)
