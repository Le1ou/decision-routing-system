"""
Integration tests for all API endpoints.

Requirements:
    pip install requests pytest

Usage:
    Start the server first, then run:
        pytest apps/backend/tests/test_endpoints.py -v

Assumptions:
    - Server is running at BASE_URL with freshly seeded data.
    - Seeded application IDs are deterministic (RESTART IDENTITY):
        1=new  2=new(unfinished)  3=new(high)
        4=assigned  5=assigned  6=inProgress  7=inProgress
        8=completed  9=completed  10=completed
        11=rejected  12=delegated
    - Department IDs: 1=IT  2=OGE  3=SEC  4=HR
    - Work type ID 1 = it_pc_repair (IT dept, easy complexity)
    - Notification IDs are fetched dynamically to avoid fragility.
"""

import pytest
import requests

BASE_URL = "http://localhost:8000"

# ── Auth credentials (username, password) ────────────────────────────────────

MANAGER  = ("orlova_m",  "Manager!1")        # all permissions
EXECUTOR = ("ivanov_i",  "SecretPassword!1") # canExecuteApplications only
AUTHOR   = ("fedorov_a", "Fedorov!6")        # canCreateApplications only
BAD_AUTH = ("nobody",    "wrongpass")

# ── Known seeded IDs ──────────────────────────────────────────────────────────

APP_NEW        = 1
APP_NEW_2      = 2
APP_ASSIGNED   = 4   # executor = ivanov_i (employee_id=2)
APP_IN_PROGRESS = 6
APP_COMPLETED  = 8
APP_DELEGATED  = 12

DEP_IT  = 1
DEP_OGE = 2

WORK_TYPE_IT_REPAIR = 1   # referenced by seeded applications — safe for 409 tests
POSITION_ID = 1

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _req(method, path, auth=None, **kwargs):
    return requests.request(method, f"{BASE_URL}{path}", auth=auth, **kwargs)

def get(path, auth=None, **kw):    return _req("GET",    path, auth, **kw)
def post(path, auth=None, **kw):   return _req("POST",   path, auth, **kw)
def patch(path, auth=None, **kw):  return _req("PATCH",  path, auth, **kw)
def put(path, auth=None, **kw):    return _req("PUT",    path, auth, **kw)
def delete(path, auth=None, **kw): return _req("DELETE", path, auth, **kw)

VALID_APP_BODY = {
    "name": "Test application",
    "departmentId": str(DEP_IT),
    "workTypeId": str(WORK_TYPE_IT_REPAIR),
    "deadlineAt": "2030-01-01T00:00:00Z",
    "description": "Test description",
}

VALID_PRIORITY_BODY = {
    "department": 0.2,
    "position":   0.2,
    "workType":   0.2,
    "deadline":   0.2,
    "managerAuthor": 0.2,
}

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def managed_app_id():
    """New application used for stateful action tests (assign, reject, etc.)."""
    r = post("/applications", MANAGER, json=VALID_APP_BODY)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


@pytest.fixture(scope="module")
def deletable_work_type_id():
    """Work type with no applications attached — safe to delete."""
    r = post("/work-types", MANAGER,
             json={"name": "Temporary WType", "departmentId": str(DEP_IT), "complexity": "easy"})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


# ─────────────────────────────────────────────────────────────────────────────
# /auth/me
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthMe:
    def test_manager_returns_200_with_all_permissions(self):
        r = get("/auth/me", MANAGER)
        assert r.status_code == 200
        body = r.json()
        assert "user" in body and "permissions" in body
        assert "manager" in body["user"]["roles"]
        perms = body["permissions"]
        assert perms["canManageEmployees"] is True
        assert perms["canManageWorkTypes"] is True
        assert perms["canManagePrioritySettings"] is True
        assert perms["canViewReports"] is True
        assert perms["canCreateApplications"] is True
        assert perms["canExecuteApplications"] is True
        assert perms["canManageDepartment"] is True

    def test_executor_returns_200_with_limited_permissions(self):
        r = get("/auth/me", EXECUTOR)
        assert r.status_code == 200
        perms = r.json()["permissions"]
        assert perms["canManageEmployees"] is False
        assert perms["canViewReports"] is False

    def test_author_returns_200(self):
        assert get("/auth/me", AUTHOR).status_code == 200

    def test_no_auth_returns_401(self):
        assert get("/auth/me").status_code == 401

    def test_wrong_password_returns_401(self):
        assert get("/auth/me", ("orlova_m", "WrongPassword")).status_code == 401

    def test_unknown_user_returns_401(self):
        assert get("/auth/me", BAD_AUTH).status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# GET /applications
# ─────────────────────────────────────────────────────────────────────────────

class TestListApplications:
    def test_manager_200(self):
        r = get("/applications", MANAGER)
        assert r.status_code == 200
        assert "items" in r.json() and "pagination" in r.json()

    def test_executor_200(self):
        assert get("/applications", EXECUTOR).status_code == 200

    def test_author_200(self):
        assert get("/applications", AUTHOR).status_code == 200

    def test_no_auth_401(self):
        assert get("/applications").status_code == 401

    def test_filter_by_status_returns_only_matching(self):
        r = get("/applications", MANAGER, params={"status": "new"})
        assert r.status_code == 200
        for item in r.json()["items"]:
            assert item["status"] == "new"

    def test_assigned_to_me_200(self):
        assert get("/applications", EXECUTOR, params={"assignedToMe": True}).status_code == 200

    def test_created_by_me_200(self):
        assert get("/applications", AUTHOR, params={"createdByMe": True}).status_code == 200

    def test_page_zero_422(self):
        assert get("/applications", MANAGER, params={"page": 0}).status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# POST /applications
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateApplication:
    def test_author_201(self):
        r = post("/applications", AUTHOR, json=VALID_APP_BODY)
        assert r.status_code == 201
        assert "id" in r.json()

    def test_manager_201(self):
        assert post("/applications", MANAGER, json=VALID_APP_BODY).status_code == 201

    def test_executor_201(self):
        assert post("/applications", EXECUTOR, json=VALID_APP_BODY).status_code == 201

    def test_no_auth_401(self):
        assert post("/applications", json=VALID_APP_BODY).status_code == 401

    def test_invalid_date_422(self):
        body = {**VALID_APP_BODY, "deadlineAt": "not-a-date"}
        assert post("/applications", AUTHOR, json=body).status_code == 422

    def test_invalid_department_id_400(self):
        body = {**VALID_APP_BODY, "departmentId": "9999"}
        assert post("/applications", AUTHOR, json=body).status_code == 400

    def test_invalid_work_type_id_400(self):
        body = {**VALID_APP_BODY, "workTypeId": "9999"}
        assert post("/applications", AUTHOR, json=body).status_code == 400

    def test_missing_name_422(self):
        body = {k: v for k, v in VALID_APP_BODY.items() if k != "name"}
        assert post("/applications", AUTHOR, json=body).status_code == 422

    def test_missing_description_422(self):
        body = {k: v for k, v in VALID_APP_BODY.items() if k != "description"}
        assert post("/applications", AUTHOR, json=body).status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# GET /applications/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestGetApplication:
    def test_existing_200(self):
        r = get(f"/applications/{APP_NEW_2}", MANAGER)
        assert r.status_code == 200
        assert "application" in r.json()

    def test_executor_200(self):
        assert get(f"/applications/{APP_NEW_2}", EXECUTOR).status_code == 200

    def test_not_found_404(self):
        assert get("/applications/99999", MANAGER).status_code == 404

    def test_non_integer_id_422(self):
        assert get("/applications/abc", MANAGER).status_code == 422

    def test_no_auth_401(self):
        assert get(f"/applications/{APP_NEW_2}").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# POST /applications/{id}/actions
# ─────────────────────────────────────────────────────────────────────────────

class TestApplicationActions:
    def test_assign_executor_on_new_app_204(self, managed_app_id):
        r = post(f"/applications/{managed_app_id}/actions", MANAGER,
                 json={"action": "assignExecutor", "executorId": "2"})
        assert r.status_code == 204

    def test_assign_executor_cancels_delegation_204(self):
        r = post(f"/applications/{APP_DELEGATED}/actions", MANAGER,
                 json={"action": "assignExecutor", "executorId": "2"})
        assert r.status_code == 204
        detail = get(f"/applications/{APP_DELEGATED}", MANAGER).json()["application"]
        assert detail.get("delegationId") is None

    def test_executor_start_work_on_assigned_204(self):
        r = post(f"/applications/{APP_ASSIGNED}/actions", EXECUTOR,
                 json={"action": "startWork"})
        assert r.status_code == 204

    def test_complete_without_result_text_400(self):
        # APP_IN_PROGRESS is inProgress — executor can complete but resultText is required
        r = post(f"/applications/{APP_IN_PROGRESS}/actions", EXECUTOR,
                 json={"action": "complete"})
        assert r.status_code == 400

    def test_executor_cannot_assign_executor_403(self, managed_app_id):
        r = post(f"/applications/{managed_app_id}/actions", EXECUTOR,
                 json={"action": "assignExecutor", "executorId": "2"})
        assert r.status_code == 403

    def test_author_cannot_assign_executor_403(self, managed_app_id):
        r = post(f"/applications/{managed_app_id}/actions", AUTHOR,
                 json={"action": "assignExecutor", "executorId": "2"})
        assert r.status_code == 403

    def test_assign_without_executor_id_400(self, managed_app_id):
        r = post(f"/applications/{managed_app_id}/actions", MANAGER,
                 json={"action": "assignExecutor"})
        assert r.status_code == 400

    def test_unknown_action_400(self, managed_app_id):
        r = post(f"/applications/{managed_app_id}/actions", MANAGER,
                 json={"action": "doSomethingUnknown"})
        assert r.status_code == 400

    def test_not_found_404(self):
        r = post("/applications/99999/actions", MANAGER,
                 json={"action": "assignExecutor", "executorId": "2"})
        assert r.status_code == 404

    def test_non_integer_id_422(self):
        r = post("/applications/abc/actions", MANAGER,
                 json={"action": "assignExecutor", "executorId": "2"})
        assert r.status_code == 422

    def test_no_auth_401(self):
        r = post(f"/applications/{APP_NEW}/actions",
                 json={"action": "assignExecutor", "executorId": "2"})
        assert r.status_code == 401

    def test_manager_can_reject_completed_app_is_forbidden_403(self):
        r = post(f"/applications/{APP_COMPLETED}/actions", MANAGER,
                 json={"action": "reject"})
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# /departments
# ─────────────────────────────────────────────────────────────────────────────

class TestDepartments:
    def test_manager_200(self):
        r = get("/departments", MANAGER)
        assert r.status_code == 200
        assert len(r.json()["items"]) >= 4

    def test_executor_200(self):
        assert get("/departments", EXECUTOR).status_code == 200

    def test_author_200(self):
        assert get("/departments", AUTHOR).status_code == 200

    def test_no_auth_401(self):
        assert get("/departments").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# GET /employees
# ─────────────────────────────────────────────────────────────────────────────

class TestGetEmployees:
    def test_manager_200(self):
        r = get("/employees", MANAGER)
        assert r.status_code == 200
        assert len(r.json()["items"]) > 0

    def test_executor_200(self):
        assert get("/employees", EXECUTOR).status_code == 200

    def test_filter_by_role_executor(self):
        r = get("/employees", MANAGER, params={"role": "executor"})
        assert r.status_code == 200
        for emp in r.json()["items"]:
            assert "executor" in emp["roles"]

    def test_filter_by_active_200(self):
        assert get("/employees", MANAGER, params={"isActive": True}).status_code == 200

    def test_filter_by_department_200(self):
        r = get("/employees", MANAGER, params={"departmentId": str(DEP_IT)})
        assert r.status_code == 200

    def test_no_auth_401(self):
        assert get("/employees").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# POST /employees
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateEmployee:
    def test_manager_201(self):
        r = post("/employees", MANAGER,
                 json={"adUserId": "1001", "positionId": str(POSITION_ID), "isActive": True})
        assert r.status_code == 201
        assert "id" in r.json()

    def test_executor_403(self):
        r = post("/employees", EXECUTOR,
                 json={"adUserId": "1002", "positionId": str(POSITION_ID), "isActive": True})
        assert r.status_code == 403

    def test_author_403(self):
        r = post("/employees", AUTHOR,
                 json={"adUserId": "1003", "positionId": str(POSITION_ID), "isActive": True})
        assert r.status_code == 403

    def test_no_auth_401(self):
        r = post("/employees",
                 json={"adUserId": "1001", "positionId": str(POSITION_ID), "isActive": True})
        assert r.status_code == 401

    def test_invalid_position_400(self):
        r = post("/employees", MANAGER,
                 json={"adUserId": "1001", "positionId": "9999", "isActive": True})
        assert r.status_code == 400

    def test_missing_fields_422(self):
        assert post("/employees", MANAGER, json={"adUserId": "1001"}).status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /employees/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateEmployee:
    def test_manager_set_active_204(self):
        assert patch("/employees/1", MANAGER, json={"isActive": True}).status_code == 204

    def test_manager_deactivate_reactivate_204(self):
        assert patch("/employees/2", MANAGER, json={"isActive": False}).status_code == 204
        assert patch("/employees/2", MANAGER, json={"isActive": True}).status_code == 204

    def test_manager_update_position_204(self):
        assert patch("/employees/3", MANAGER, json={"positionId": str(POSITION_ID)}).status_code == 204

    def test_executor_403(self):
        assert patch("/employees/1", EXECUTOR, json={"isActive": True}).status_code == 403

    def test_author_403(self):
        assert patch("/employees/1", AUTHOR, json={"isActive": True}).status_code == 403

    def test_not_found_404(self):
        assert patch("/employees/99999", MANAGER, json={"isActive": True}).status_code == 404

    def test_non_integer_id_422(self):
        assert patch("/employees/abc", MANAGER, json={"isActive": True}).status_code == 422

    def test_empty_body_422(self):
        assert patch("/employees/1", MANAGER, json={}).status_code == 422

    def test_no_auth_401(self):
        assert patch("/employees/1", json={"isActive": True}).status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# /positions
# ─────────────────────────────────────────────────────────────────────────────

class TestPositions:
    def test_manager_200(self):
        r = get("/positions", MANAGER)
        assert r.status_code == 200
        assert len(r.json()["items"]) > 0

    def test_executor_200(self):
        assert get("/positions", EXECUTOR).status_code == 200

    def test_author_200(self):
        assert get("/positions", AUTHOR).status_code == 200

    def test_no_auth_401(self):
        assert get("/positions").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# /ad/users
# ─────────────────────────────────────────────────────────────────────────────

class TestAdUsers:
    def test_manager_200(self):
        r = get("/ad/users", MANAGER)
        assert r.status_code == 200
        assert "items" in r.json()

    def test_executor_200(self):
        assert get("/ad/users", EXECUTOR).status_code == 200

    def test_filter_by_query_200(self):
        assert get("/ad/users", MANAGER, params={"query": "Смирнова"}).status_code == 200

    def test_no_auth_401(self):
        assert get("/ad/users").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# GET /work-types
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWorkTypes:
    def test_manager_200(self):
        r = get("/work-types", MANAGER)
        assert r.status_code == 200
        assert len(r.json()["items"]) > 0

    def test_executor_200(self):
        assert get("/work-types", EXECUTOR).status_code == 200

    def test_filter_by_department_200(self):
        r = get("/work-types", MANAGER, params={"departmentId": str(DEP_IT)})
        assert r.status_code == 200

    def test_nonexistent_department_returns_empty_list(self):
        r = get("/work-types", MANAGER, params={"departmentId": "9999"})
        assert r.status_code == 200
        assert r.json()["items"] == []

    def test_no_auth_401(self):
        assert get("/work-types").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# POST /work-types
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateWorkType:
    def test_manager_201(self):
        r = post("/work-types", MANAGER,
                 json={"name": "New WType", "departmentId": str(DEP_IT), "complexity": "easy"})
        assert r.status_code == 201
        assert "id" in r.json()

    def test_executor_403(self):
        r = post("/work-types", EXECUTOR,
                 json={"name": "X", "departmentId": str(DEP_IT), "complexity": "easy"})
        assert r.status_code == 403

    def test_author_403(self):
        r = post("/work-types", AUTHOR,
                 json={"name": "X", "departmentId": str(DEP_IT), "complexity": "easy"})
        assert r.status_code == 403

    def test_no_auth_401(self):
        r = post("/work-types",
                 json={"name": "X", "departmentId": str(DEP_IT), "complexity": "easy"})
        assert r.status_code == 401

    def test_invalid_complexity_422(self):
        r = post("/work-types", MANAGER,
                 json={"name": "X", "departmentId": str(DEP_IT), "complexity": "extreme"})
        assert r.status_code == 422

    def test_invalid_department_400(self):
        r = post("/work-types", MANAGER,
                 json={"name": "X", "departmentId": "9999", "complexity": "easy"})
        assert r.status_code == 400

    def test_missing_fields_422(self):
        assert post("/work-types", MANAGER, json={"name": "X"}).status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /work-types/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteWorkType:
    def test_manager_204(self, deletable_work_type_id):
        assert delete(f"/work-types/{deletable_work_type_id}", MANAGER).status_code == 204

    def test_executor_403(self):
        assert delete(f"/work-types/{WORK_TYPE_IT_REPAIR}", EXECUTOR).status_code == 403

    def test_author_403(self):
        assert delete(f"/work-types/{WORK_TYPE_IT_REPAIR}", AUTHOR).status_code == 403

    def test_in_use_409(self):
        assert delete(f"/work-types/{WORK_TYPE_IT_REPAIR}", MANAGER).status_code == 409

    def test_not_found_404(self):
        assert delete("/work-types/99999", MANAGER).status_code == 404

    def test_non_integer_id_422(self):
        assert delete("/work-types/abc", MANAGER).status_code == 422

    def test_no_auth_401(self):
        assert delete(f"/work-types/{WORK_TYPE_IT_REPAIR}").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# /priority-settings
# ─────────────────────────────────────────────────────────────────────────────

class TestPrioritySettings:
    def test_get_manager_200(self):
        r = get("/priority-settings", MANAGER)
        assert r.status_code == 200
        assert set(r.json().keys()) == {"department", "position", "workType", "deadline", "managerAuthor"}

    def test_get_executor_403(self):
        assert get("/priority-settings", EXECUTOR).status_code == 403

    def test_get_author_403(self):
        assert get("/priority-settings", AUTHOR).status_code == 403

    def test_get_no_auth_401(self):
        assert get("/priority-settings").status_code == 401

    def test_put_manager_200(self):
        r = put("/priority-settings", MANAGER, json=VALID_PRIORITY_BODY)
        assert r.status_code == 200
        assert r.json() == VALID_PRIORITY_BODY

    def test_put_executor_403(self):
        assert put("/priority-settings", EXECUTOR, json=VALID_PRIORITY_BODY).status_code == 403

    def test_put_author_403(self):
        assert put("/priority-settings", AUTHOR, json=VALID_PRIORITY_BODY).status_code == 403

    def test_put_no_auth_401(self):
        assert put("/priority-settings", json=VALID_PRIORITY_BODY).status_code == 401

    def test_put_value_out_of_range_422(self):
        body = {**VALID_PRIORITY_BODY, "department": 1.5}
        assert put("/priority-settings", MANAGER, json=body).status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# /notifications
# ─────────────────────────────────────────────────────────────────────────────

class TestNotifications:
    def test_get_manager_200(self):
        r = get("/notifications", MANAGER)
        assert r.status_code == 200
        assert "items" in r.json() and "unreadCount" in r.json()

    def test_get_executor_200(self):
        assert get("/notifications", EXECUTOR).status_code == 200

    def test_get_unread_only_200(self):
        assert get("/notifications", EXECUTOR, params={"unreadOnly": True}).status_code == 200

    def test_get_no_auth_401(self):
        assert get("/notifications").status_code == 401

    def test_mark_all_read_manager_204(self):
        assert post("/notifications/read-all", MANAGER).status_code == 204

    def test_mark_all_read_no_auth_401(self):
        assert post("/notifications/read-all").status_code == 401

    def test_mark_single_not_found_404(self):
        assert post("/notifications/99999/read", MANAGER).status_code == 404

    def test_mark_single_non_integer_422(self):
        assert post("/notifications/abc/read", MANAGER).status_code == 422

    def test_mark_single_no_auth_401(self):
        assert post("/notifications/1/read").status_code == 401

    def test_mark_single_existing_204(self):
        r = get("/notifications", EXECUTOR)
        items = r.json().get("items", [])
        if items:
            notif_id = items[0]["id"]
            assert post(f"/notifications/{notif_id}/read", EXECUTOR).status_code == 204
        else:
            pytest.skip("No notifications available for executor")


# ─────────────────────────────────────────────────────────────────────────────
# /reports
# ─────────────────────────────────────────────────────────────────────────────

class TestReports:
    def test_get_manager_200(self):
        r = get("/reports/applications", MANAGER)
        assert r.status_code == 200
        body = r.json()
        assert "items" in body and "summary" in body
        summary = body["summary"]
        assert {"total", "completed", "inProgressOrAssigned"} <= summary.keys()
        assert summary["total"] >= 0

    def test_get_executor_403(self):
        assert get("/reports/applications", EXECUTOR).status_code == 403

    def test_get_author_403(self):
        assert get("/reports/applications", AUTHOR).status_code == 403

    def test_get_no_auth_401(self):
        assert get("/reports/applications").status_code == 401

    def test_get_filter_by_status_returns_only_matching(self):
        r = get("/reports/applications", MANAGER, params={"status": "completed"})
        assert r.status_code == 200
        for row in r.json()["items"]:
            assert row["status"] == "completed"

    def test_get_filter_by_executor_id_200(self):
        r = get("/reports/applications", MANAGER, params={"executorId": "2"})
        assert r.status_code == 200

    def test_xls_manager_200(self):
        assert get("/reports/applications.xls", MANAGER).status_code == 200

    def test_xls_executor_403(self):
        assert get("/reports/applications.xls", EXECUTOR).status_code == 403

    def test_xls_author_403(self):
        assert get("/reports/applications.xls", AUTHOR).status_code == 403

    def test_xls_no_auth_401(self):
        assert get("/reports/applications.xls").status_code == 401
