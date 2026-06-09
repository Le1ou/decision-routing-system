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

import os

import pytest
import requests

# Use 127.0.0.1 rather than "localhost": on Windows, resolving "localhost"
# makes the client try IPv6 (::1) first and stall ~2s per request before
# falling back to the IPv4-bound server. Override with the BASE_URL env var.
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")

# ── Auth credentials (username, password) ────────────────────────────────────

MANAGER  = ("orlova_m",   "Manager!1")        # top-manager — all manage permissions, all departments
DEPT_MANAGER = ("kuznetsov_m", "Kuznetsov!7") # plain manager — OGE department (dept 2)
EXECUTOR = ("ivanov_i",   "SecretPassword!1") # executor — IT, employee_id 2
EXECUTOR2 = ("petrov_p",  "Pa$$w0rd")         # executor — IT, employee_id 3
AUTHOR   = ("fedorov_a",  "Fedorov!6")        # author — IT, employee_id 7
AUTHOR2  = ("novikova_e", "Novikova!5")       # author — HR, employee_id 6
BAD_AUTH = ("nobody",     "wrongpass")

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
GRADE_IDS = ["0", "1"]    # grade table starts at id 0: 0=junior 1=middle 2=senior 3=lead
AD_USER_IT = "1001"       # smirnova_t — AD user in IT (dept 1), addable by managers

# ── HTTP helpers ──────────────────────────────────────────────────────────────

# A shared session reuses the TCP connection (keep-alive) across requests,
# avoiding a fresh connect + name lookup on every call.
_session = requests.Session()

def _req(method, path, auth=None, **kwargs):
    return _session.request(method, f"{BASE_URL}{path}", auth=auth, **kwargs)

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
    "department":    {"1": 0.2, "2": 0.2, "3": 0.2, "4": 0.2},
    "managerAuthor": {"1": 0.2, "2": 0.2, "3": 0.2, "4": 0.2},
    "deadline":      0.2,
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
             json={"name": "Temporary WType", "departmentId": str(DEP_IT),
                   "complexity": "easy", "allowedGradeIds": GRADE_IDS})
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
        assert "top-manager" in body["user"]["roles"]
        perms = body["permissions"]
        assert set(perms.keys()) == {
            "canManageEmployees", "canManageWorkTypes",
            "canManagePrioritySettings", "canViewReports",
        }
        assert perms["canManageEmployees"] is True
        assert perms["canManageWorkTypes"] is True
        assert perms["canManagePrioritySettings"] is True
        assert perms["canViewReports"] is True

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
# GET /applications — every query parameter actually changes the result set
# ─────────────────────────────────────────────────────────────────────────────

def _list_ids(auth, **params):
    """Return the ids returned by GET /applications for the given filters."""
    params.setdefault("pageSize", 100)
    r = get("/applications", auth, params=params)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def _make_app(auth, departmentId=str(DEP_IT), workTypeId=str(WORK_TYPE_IT_REPAIR)):
    body = {**VALID_APP_BODY, "departmentId": departmentId, "workTypeId": workTypeId}
    r = post("/applications", auth, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


class TestListApplicationFilters:
    def test_status_filter_returns_only_that_status(self):
        for st in ["new", "assigned", "inProgress", "completed", "rejected"]:
            items = get("/applications", MANAGER, params={"status": st, "pageSize": 100}).json()["items"]
            assert all(i["status"] == st for i in items), st

    def test_priority_filter_returns_only_that_priority(self):
        for pr in ["low", "medium", "high", "critical"]:
            items = get("/applications", MANAGER, params={"priority": pr, "pageSize": 100}).json()["items"]
            assert all(i["priority"] == pr for i in items), pr

    def test_application_id_isolates_single_application(self):
        new_id = _make_app(AUTHOR)
        items = get("/applications", MANAGER, params={"applicationId": new_id, "pageSize": 100}).json()["items"]
        assert [i["id"] for i in items] == [new_id]

    def test_created_by_me_includes_mine_excludes_others(self):
        mine = _make_app(AUTHOR)     # fedorov (emp 7)
        other = _make_app(AUTHOR2)   # novikova (emp 6)
        mine_list = _list_ids(AUTHOR, createdByMe=True)
        assert mine in mine_list
        assert other not in mine_list
        # Non-managers are scoped to their own involvement (role-based visibility),
        # so fedorov never sees novikova's application — with or without the flag.
        assert other not in _list_ids(AUTHOR)
        # A top-manager, by contrast, sees every author's application.
        assert other in _list_ids(MANAGER)

    def test_assigned_to_me_includes_mine_excludes_others(self):
        app = _make_app(AUTHOR)
        assert post(f"/applications/{app}/actions", MANAGER,
                    json={"action": "assignExecutor", "executorId": "2"}).status_code == 204  # ivanov
        assert app in _list_ids(EXECUTOR, assignedToMe=True)        # ivanov sees it
        assert app not in _list_ids(EXECUTOR2, assignedToMe=True)   # petrov does not

    def test_delegated_to_my_department(self):
        app = _make_app(AUTHOR, departmentId=str(DEP_IT))
        assert post(f"/applications/{app}/actions", MANAGER,
                    json={"action": "delegateExternal", "departmentId": str(DEP_OGE),
                          "comment": "to OGE"}).status_code == 204
        # OGE manager sees it under delegatedToMyDepartment; IT author does not
        assert app in _list_ids(DEPT_MANAGER, delegatedToMyDepartment=True)
        assert app not in _list_ids(AUTHOR, delegatedToMyDepartment=True)
        # the flag is what filters: without it the IT author still sees the app
        assert app in _list_ids(AUTHOR)

    def test_executor_name_filter(self):
        app = _make_app(AUTHOR)
        assert post(f"/applications/{app}/actions", MANAGER,
                    json={"action": "assignExecutor", "executorId": "2"}).status_code == 204  # ivanov
        assert app in _list_ids(MANAGER, executorName="Иванов")
        assert app not in _list_ids(MANAGER, executorName="Петров")

    def test_archived_application_hidden_from_list(self):
        # seed application 10 is completed + archived → never in the main list
        assert "10" not in _list_ids(MANAGER)

    def test_pagination_limits_page_size(self):
        body = get("/applications", MANAGER, params={"page": 1, "pageSize": 1}).json()
        assert len(body["items"]) <= 1
        assert body["pagination"]["pageSize"] == 1
        assert body["pagination"]["total"] >= 1

    def test_pagination_page_holds_distinct_items(self):
        # ensure there are at least 2 applications, then one page of size 2 has 2 distinct ids
        _make_app(AUTHOR); _make_app(AUTHOR)
        items = get("/applications", MANAGER, params={"page": 1, "pageSize": 2}).json()["items"]
        ids = [i["id"] for i in items]
        assert len(ids) == 2 and len(set(ids)) == 2

    def test_sort_by_priority_desc(self):
        rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        items = get("/applications", MANAGER,
                    params={"sortBy": "priority", "sortDirection": "desc", "pageSize": 100}).json()["items"]
        vals = [rank[i["priority"]] for i in items]
        assert vals == sorted(vals, reverse=True)

    def test_sort_by_created_at_direction_respected(self):
        asc = [i["createdAt"] for i in get("/applications", MANAGER,
               params={"sortBy": "createdAt", "sortDirection": "asc", "pageSize": 100}).json()["items"]]
        desc = [i["createdAt"] for i in get("/applications", MANAGER,
                params={"sortBy": "createdAt", "sortDirection": "desc", "pageSize": 100}).json()["items"]]
        assert asc == sorted(asc)              # non-decreasing
        assert desc == sorted(desc, reverse=True)  # non-increasing


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
        # Ensure the app is currently assigned to this executor: earlier reassignments
        # may have released it under the one-app-per-executor rule, so (re)assign first.
        assert post(f"/applications/{APP_ASSIGNED}/actions", MANAGER,
                    json={"action": "assignExecutor", "executorId": "2"}).status_code == 204
        r = post(f"/applications/{APP_ASSIGNED}/actions", EXECUTOR,
                 json={"action": "startWork"})
        assert r.status_code == 204

    def test_complete_without_result_text_400(self):
        # APP_IN_PROGRESS is inProgress and assigned to executor2 (petrov) — only the
        # assigned executor may complete it, and resultText is required.
        r = post(f"/applications/{APP_IN_PROGRESS}/actions", EXECUTOR2,
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
            assert emp["role"] == "executor"

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
                 json={"adUserId": AD_USER_IT, "role": "executor", "isActive": True})
        assert r.status_code == 201
        assert "id" in r.json()

    def test_executor_403(self):
        r = post("/employees", EXECUTOR,
                 json={"adUserId": "1002", "role": "executor", "isActive": True})
        assert r.status_code == 403

    def test_author_403(self):
        r = post("/employees", AUTHOR,
                 json={"adUserId": "1003", "role": "executor", "isActive": True})
        assert r.status_code == 403

    def test_no_auth_401(self):
        r = post("/employees",
                 json={"adUserId": AD_USER_IT, "role": "executor", "isActive": True})
        assert r.status_code == 401

    def test_invalid_ad_user_400(self):
        r = post("/employees", MANAGER,
                 json={"adUserId": "9999", "role": "executor", "isActive": True})
        assert r.status_code == 400

    def test_invalid_role_422(self):
        r = post("/employees", MANAGER,
                 json={"adUserId": AD_USER_IT, "role": "wizard", "isActive": True})
        assert r.status_code == 422

    def test_missing_fields_422(self):
        assert post("/employees", MANAGER, json={"adUserId": AD_USER_IT}).status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /employees/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateEmployee:
    def test_manager_set_active_204(self):
        assert patch("/employees/1", MANAGER, json={"isActive": True}).status_code == 204

    def test_manager_deactivate_reactivate_204(self):
        assert patch("/employees/2", MANAGER, json={"isActive": False}).status_code == 204
        assert patch("/employees/2", MANAGER, json={"isActive": True}).status_code == 204

    def test_manager_update_role_204(self):
        assert patch("/employees/3", MANAGER, json={"role": "executor"}).status_code == 204

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

    def test_author_can_filter_by_another_department(self):
        r = get("/work-types", AUTHOR, params={"departmentId": str(DEP_OGE)})
        assert r.status_code == 200
        items = r.json()["items"]
        assert items
        assert all(item["departmentId"] == str(DEP_OGE) for item in items)

    def test_plain_manager_can_filter_by_another_department(self):
        r = get("/work-types", DEPT_MANAGER, params={"departmentId": str(DEP_IT)})
        assert r.status_code == 200
        items = r.json()["items"]
        assert items
        assert all(item["departmentId"] == str(DEP_IT) for item in items)

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
                 json={"name": "New WType", "departmentId": str(DEP_IT),
                       "complexity": "easy", "allowedGradeIds": GRADE_IDS})
        assert r.status_code == 201
        assert "id" in r.json()

    def test_executor_403(self):
        r = post("/work-types", EXECUTOR,
                 json={"name": "X", "departmentId": str(DEP_IT),
                       "complexity": "easy", "allowedGradeIds": GRADE_IDS})
        assert r.status_code == 403

    def test_author_403(self):
        r = post("/work-types", AUTHOR,
                 json={"name": "X", "departmentId": str(DEP_IT),
                       "complexity": "easy", "allowedGradeIds": GRADE_IDS})
        assert r.status_code == 403

    def test_no_auth_401(self):
        r = post("/work-types",
                 json={"name": "X", "departmentId": str(DEP_IT),
                       "complexity": "easy", "allowedGradeIds": GRADE_IDS})
        assert r.status_code == 401

    def test_invalid_complexity_422(self):
        r = post("/work-types", MANAGER,
                 json={"name": "X", "departmentId": str(DEP_IT),
                       "complexity": "extreme", "allowedGradeIds": GRADE_IDS})
        assert r.status_code == 422

    def test_missing_grades_422(self):
        r = post("/work-types", MANAGER,
                 json={"name": "X", "departmentId": str(DEP_IT), "complexity": "easy"})
        assert r.status_code == 422

    def test_invalid_department_400(self):
        r = post("/work-types", MANAGER,
                 json={"name": "X", "departmentId": "9999",
                       "complexity": "easy", "allowedGradeIds": GRADE_IDS})
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
        body = r.json()
        assert set(body.keys()) == {"department", "managerAuthor", "deadline", "urgent"}
        assert {"thresholdHours", "bonus"} <= body["urgent"].keys()

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
        body = {**VALID_PRIORITY_BODY, "department": {"1": 1.5}}
        assert put("/priority-settings", MANAGER, json=body).status_code == 422

    def test_put_dept_manager_403(self):
        # Persisting priority settings is restricted to top-managers.
        assert put("/priority-settings", DEPT_MANAGER, json=VALID_PRIORITY_BODY).status_code == 403


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


# ─────────────────────────────────────────────────────────────────────────────
# /grades
# ─────────────────────────────────────────────────────────────────────────────

class TestGrades:
    def test_manager_200(self):
        r = get("/grades", MANAGER)
        assert r.status_code == 200
        assert len(r.json()["items"]) > 0

    def test_executor_200(self):
        assert get("/grades", EXECUTOR).status_code == 200

    def test_no_auth_401(self):
        assert get("/grades").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /work-types/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdateWorkType:
    @pytest.fixture(scope="class")
    def updatable_work_type_id(self):
        """A dedicated work type for PATCH tests (not shared with the delete tests)."""
        r = post("/work-types", MANAGER,
                 json={"name": "Updatable WType", "departmentId": str(DEP_IT),
                       "complexity": "easy", "allowedGradeIds": GRADE_IDS})
        assert r.status_code == 201, r.text
        return int(r.json()["id"])

    def test_manager_update_name_204(self, updatable_work_type_id):
        r = patch(f"/work-types/{updatable_work_type_id}", MANAGER, json={"name": "Renamed WType"})
        assert r.status_code == 204

    def test_manager_update_grades_204(self, updatable_work_type_id):
        r = patch(f"/work-types/{updatable_work_type_id}", MANAGER,
                  json={"allowedGradeIds": ["2", "3"]})
        assert r.status_code == 204

    def test_executor_403(self):
        assert patch(f"/work-types/{WORK_TYPE_IT_REPAIR}", EXECUTOR,
                     json={"name": "X"}).status_code == 403

    def test_empty_body_422(self):
        assert patch(f"/work-types/{WORK_TYPE_IT_REPAIR}", MANAGER, json={}).status_code == 422

    def test_not_found_404(self):
        assert patch("/work-types/99999", MANAGER, json={"name": "X"}).status_code == 404

    def test_no_auth_401(self):
        assert patch(f"/work-types/{WORK_TYPE_IT_REPAIR}", json={"name": "X"}).status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /employees/{id}
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteEmployee:
    @pytest.fixture(scope="class")
    def removable_employee_id(self):
        """Add an AD user, then remove them (does not touch AD)."""
        r = post("/employees", MANAGER,
                 json={"adUserId": "1002", "role": "executor", "isActive": True})
        assert r.status_code == 201, r.text
        return int(r.json()["id"])

    def test_manager_204(self, removable_employee_id):
        assert delete(f"/employees/{removable_employee_id}", MANAGER).status_code == 204

    def test_executor_403(self):
        assert delete("/employees/2", EXECUTOR).status_code == 403

    def test_not_found_404(self):
        assert delete("/employees/99999", MANAGER).status_code == 404

    def test_no_auth_401(self):
        assert delete("/employees/2").status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /departments/{id}/delegation-settings
# ─────────────────────────────────────────────────────────────────────────────

class TestDepartmentDelegationSettings:
    def test_top_manager_any_department_204(self):
        r = patch(f"/departments/{DEP_OGE}/delegation-settings", MANAGER,
                  json={"delegatedToSameDepartment": True})
        assert r.status_code == 204

    def test_manager_own_department_204(self):
        # kuznetsov_m manages OGE (department 2).
        r = patch(f"/departments/{DEP_OGE}/delegation-settings", DEPT_MANAGER,
                  json={"delegatedToSameDepartment": False})
        assert r.status_code == 204

    def test_manager_other_department_403(self):
        # kuznetsov_m cannot touch the IT department.
        r = patch(f"/departments/{DEP_IT}/delegation-settings", DEPT_MANAGER,
                  json={"delegatedToSameDepartment": True})
        assert r.status_code == 403

    def test_executor_403(self):
        r = patch(f"/departments/{DEP_OGE}/delegation-settings", EXECUTOR,
                  json={"delegatedToSameDepartment": True})
        assert r.status_code == 403

    def test_not_found_404(self):
        r = patch("/departments/99999/delegation-settings", MANAGER,
                  json={"delegatedToSameDepartment": True})
        assert r.status_code == 404

    def test_no_auth_401(self):
        r = patch(f"/departments/{DEP_OGE}/delegation-settings",
                  json={"delegatedToSameDepartment": True})
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# cancel / archive actions
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelAndArchive:
    def test_author_can_cancel_new_204(self):
        new_id = int(post("/applications", AUTHOR, json=VALID_APP_BODY).json()["id"])
        r = post(f"/applications/{new_id}/actions", AUTHOR,
                 json={"action": "cancel", "comment": "Создано ошибочно."})
        assert r.status_code == 204
        detail = get(f"/applications/{new_id}", AUTHOR).json()["application"]
        assert detail["status"] == "rejected"

    def test_manager_can_archive_rejected_204(self):
        new_id = int(post("/applications", AUTHOR, json=VALID_APP_BODY).json()["id"])
        post(f"/applications/{new_id}/actions", AUTHOR, json={"action": "cancel"})
        r = post(f"/applications/{new_id}/actions", MANAGER, json={"action": "archive"})
        assert r.status_code == 204
        # Archived applications drop out of the main list.
        listed = get("/applications", MANAGER, params={"applicationId": str(new_id)}).json()["items"]
        assert all(item["id"] != str(new_id) for item in listed)

    def test_executor_cannot_archive_403(self):
        r = post(f"/applications/{APP_COMPLETED}/actions", EXECUTOR, json={"action": "archive"})
        assert r.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Content helpers — assert the data we get back, not just the status code
# ─────────────────────────────────────────────────────────────────────────────

def _detail(app_id, auth):
    r = get(f"/applications/{app_id}", auth)
    assert r.status_code == 200, r.text
    return r.json()["application"]


def _act(app_id, auth, **body):
    return post(f"/applications/{app_id}/actions", auth, json=body)


def _create_assigned(executor_id="2"):
    """Create a fresh application and assign it to an executor (default ivanov, emp 2)."""
    app_id = int(_make_app(AUTHOR))
    assert _act(app_id, MANAGER, action="assignExecutor", executorId=executor_id).status_code == 204
    return app_id


def _set_internal_confirmation(dep_id, required: bool):
    """Toggle a department's 'confirmation required for internal delegation' flag."""
    assert patch(f"/departments/{dep_id}/delegation-settings", MANAGER,
                 json={"delegatedToSameDepartment": required}).status_code == 204


# ─────────────────────────────────────────────────────────────────────────────
# /auth/me — role ladder is expanded correctly per user
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthMeContent:
    def test_top_manager_has_full_role_ladder(self):
        roles = get("/auth/me", MANAGER).json()["user"]["roles"]
        assert set(roles) == {"author", "executor", "manager", "top-manager"}

    def test_plain_manager_roles_exclude_top(self):
        roles = get("/auth/me", DEPT_MANAGER).json()["user"]["roles"]
        assert set(roles) == {"author", "executor", "manager"}

    def test_executor_roles(self):
        roles = get("/auth/me", EXECUTOR).json()["user"]["roles"]
        assert set(roles) == {"author", "executor"}

    def test_author_roles_and_identity(self):
        u = get("/auth/me", AUTHOR).json()["user"]
        assert u["roles"] == ["author"]
        assert u["fullName"] and u["departmentId"]


# ─────────────────────────────────────────────────────────────────────────────
# /departments — values respect the contract's 0..1 bounds
# ─────────────────────────────────────────────────────────────────────────────

class TestDepartmentsContent:
    def test_ratios_within_contract_bounds(self):
        items = get("/departments", MANAGER).json()["items"]
        assert items
        for d in items:
            # value = коэффициент важности отдела (k_отдела), диапазон [0, 1.25].
            assert 0 <= d["value"] <= 1.25, d
            assert 0 <= d["deadlineNotificationRatio"] <= 1, d
            assert d["employeeApplicationDelayMinutes"] >= 0, d
            assert isinstance(d["delegatedToSameDepartment"], bool), d


# ─────────────────────────────────────────────────────────────────────────────
# GET /applications/{id} — nested author / executor / department / workType
# ─────────────────────────────────────────────────────────────────────────────

class TestApplicationDetailContent:
    def test_nested_objects_present_and_consistent(self):
        app_id = _create_assigned("2")  # ivanov is executor, IT department
        d = _detail(app_id, MANAGER)

        assert d["executorId"] == "2"
        assert d["authorId"]

        author = d["author"]
        assert author and author["id"] == d["authorId"]
        assert author["fullName"]
        assert author["role"] in ("author", "executor", "manager", "top-manager")

        ex = d["executor"]
        assert ex and ex["id"] == "2"
        assert ex["role"] == "executor"
        assert ex["fullName"]

        dep = d["department"]
        assert dep and dep["id"] == d["departmentId"]
        assert dep["name"]
        assert 0 <= dep["deadlineNotificationRatio"] <= 1

        wt = d["workType"]
        assert wt and wt["id"] == d["workTypeId"]
        assert wt["complexity"] in ("easy", "medium", "hard", "critical")
        assert isinstance(wt["allowedGradeIds"], list)

    def test_executor_absent_on_new_application(self):
        app_id = int(_make_app(AUTHOR))
        d = _detail(app_id, MANAGER)
        assert d["author"] is not None
        assert d["executor"] is None
        assert d.get("executorId") is None


# ─────────────────────────────────────────────────────────────────────────────
# Action comments — persisted to the actor's column and surfaced on the card
# ─────────────────────────────────────────────────────────────────────────────

class TestActionComments:
    def test_manager_comment_persisted_on_assign(self):
        app_id = int(_make_app(AUTHOR))
        assert _act(app_id, MANAGER, action="assignExecutor",
                    executorId="2", comment="Назначил вручную").status_code == 204
        d = _detail(app_id, MANAGER)
        assert d["managerComment"] == "Назначил вручную"
        assert d.get("executorComment") is None

    def test_executor_comment_persisted_on_reject(self):
        app_id = _create_assigned("2")
        assert _act(app_id, EXECUTOR, action="reject", comment="Не моя зона").status_code == 204
        d = _detail(app_id, MANAGER)
        assert d["executorComment"] == "Не моя зона"

    def test_result_text_persisted_on_complete(self):
        app_id = _create_assigned("2")
        assert _act(app_id, EXECUTOR, action="startWork").status_code == 204
        assert _act(app_id, EXECUTOR, action="complete", resultText="Готово").status_code == 204
        d = _detail(app_id, MANAGER)
        assert d["status"] == "completed"
        assert d["resultText"] == "Готово"


# ─────────────────────────────────────────────────────────────────────────────
# delegateInternal — executor re-addresses within the department
# ─────────────────────────────────────────────────────────────────────────────

class TestDelegateInternal:
    def test_available_to_assigned_executor(self):
        app_id = _create_assigned("2")
        assert "delegateInternal" in _detail(app_id, EXECUTOR)["availableActions"]

    def test_requires_complexity_400(self):
        app_id = _create_assigned("2")
        assert _act(app_id, EXECUTOR, action="delegateInternal").status_code == 400

    def test_immediate_path_returns_to_new(self):
        _set_internal_confirmation(DEP_IT, False)
        app_id = _create_assigned("2")
        assert _act(app_id, EXECUTOR, action="delegateInternal",
                    complexity="hard", comment="Слишком сложно").status_code == 204
        d = _detail(app_id, MANAGER)
        assert d["status"] == "new"
        assert d["isUnfinished"] is True
        assert d["assignedComplexity"] == "hard"
        assert d["previousExecutorId"] == "2"
        assert d["previousExecutor"]["id"] == "2"
        assert d["previousExecutor"]["fullName"]
        assert d["executorComment"] == "Слишком сложно"   # executor action comment

    def test_complexity_cannot_be_lowered_400(self):
        _set_internal_confirmation(DEP_IT, False)
        app_id = _create_assigned("2")
        # raise the assigned complexity to hard
        assert _act(app_id, EXECUTOR, action="delegateInternal", complexity="hard").status_code == 204
        assert _act(app_id, MANAGER, action="assignExecutor", executorId="2").status_code == 204
        # lowering below hard is rejected
        assert _act(app_id, EXECUTOR, action="delegateInternal", complexity="easy").status_code == 400
        # same level is accepted
        assert _act(app_id, EXECUTOR, action="delegateInternal", complexity="hard").status_code == 204

    def test_optional_work_type_change_applied(self):
        _set_internal_confirmation(DEP_IT, False)
        app_id = _create_assigned("2")
        assert _act(app_id, EXECUTOR, action="delegateInternal",
                    complexity="medium", workTypeId="2").status_code == 204
        assert _detail(app_id, MANAGER)["workTypeId"] == "2"

    def test_confirmation_required_then_confirm(self):
        _set_internal_confirmation(DEP_IT, True)
        try:
            app_id = _create_assigned("2")
            assert _act(app_id, EXECUTOR, action="delegateInternal",
                        complexity="medium", comment="Нужен специалист").status_code == 204
            d = _detail(app_id, MANAGER)
            assert d["status"] == "delegated"
            assert d["assignedComplexity"] == "medium"
            deleg = d["delegation"]
            assert deleg is not None
            assert deleg["delegatedByEmployeeId"] == "2"
            assert d["delegatedByEmployee"]["id"] == "2"
            assert d["delegatedByEmployee"]["fullName"]
            # internal delegation: source and target department are the same
            assert deleg["delegatedFromDepartmentId"] == deleg["delegatedToDepartmentId"]

            assert _act(app_id, MANAGER, action="confirmExternalDelegation").status_code == 204
            d2 = _detail(app_id, MANAGER)
            assert d2["status"] == "new"
            assert d2["isUnfinished"] is True
            assert d2["previousExecutorId"] == "2"
            assert d2.get("delegationId") is None
        finally:
            _set_internal_confirmation(DEP_IT, False)

    def test_confirmation_required_then_decline_keeps_executor(self):
        _set_internal_confirmation(DEP_IT, True)
        try:
            app_id = _create_assigned("2")
            assert _act(app_id, EXECUTOR, action="delegateInternal", complexity="medium").status_code == 204
            assert _detail(app_id, MANAGER)["status"] == "delegated"
            # manager refuses → executor keeps it; work never started → back to assigned
            assert _act(app_id, MANAGER, action="declineExternalDelegation").status_code == 204
            d = _detail(app_id, MANAGER)
            assert d["status"] == "assigned"
            assert d.get("delegationId") is None
        finally:
            _set_internal_confirmation(DEP_IT, False)


# ─────────────────────────────────────────────────────────────────────────────
# Role-based list visibility (#1) — verified by what each role can/can't see
# ─────────────────────────────────────────────────────────────────────────────

class TestRoleVisibility:
    def test_executor_sees_only_apps_they_are_involved_in(self):
        uninvolved = _make_app(AUTHOR2)            # ivanov is neither author nor executor
        assert uninvolved not in _list_ids(EXECUTOR)
        mine = _make_app(AUTHOR)
        assert _act(int(mine), MANAGER, action="assignExecutor", executorId="2").status_code == 204
        assert mine in _list_ids(EXECUTOR)         # now ivanov is the executor

    def test_plain_manager_scoped_to_own_department(self):
        it_app = _make_app(AUTHOR)                 # IT department, not delegated
        assert it_app not in _list_ids(DEPT_MANAGER)   # OGE manager can't see it
        assert _act(int(it_app), MANAGER, action="delegateExternal",
                    departmentId=str(DEP_OGE), comment="to OGE").status_code == 204
        assert it_app in _list_ids(DEPT_MANAGER)       # now delegated into OGE → visible

    def test_top_manager_sees_every_department(self):
        a = _make_app(AUTHOR)
        b = _make_app(AUTHOR2)
        seen = _list_ids(MANAGER)
        assert a in seen and b in seen


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /employees — soft-delete frees the AD person for re-adding (#4)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmployeeReadd:
    def test_deleted_employee_reappears_in_ad_and_can_be_readded(self):
        candidates = get("/ad/users", MANAGER).json()["items"]
        assert candidates, "expected at least one addable AD user"
        ad_id = candidates[0]["adUserId"]

        emp_id = post("/employees", MANAGER,
                      json={"adUserId": ad_id, "role": "executor", "isActive": True}).json()["id"]
        # onboarded → no longer an addable candidate
        assert all(c["adUserId"] != ad_id for c in get("/ad/users", MANAGER).json()["items"])

        assert delete(f"/employees/{emp_id}", MANAGER).status_code == 204
        # freed → reappears as a candidate
        assert any(c["adUserId"] == ad_id for c in get("/ad/users", MANAGER).json()["items"])
        # and can be added again
        assert post("/employees", MANAGER,
                    json={"adUserId": ad_id, "role": "executor", "isActive": True}).status_code == 201


# ─────────────────────────────────────────────────────────────────────────────
# returnToNew — manager sends an active application back for redistribution
# ─────────────────────────────────────────────────────────────────────────────

class TestReturnToNew:
    def test_manager_returns_assigned_to_new_unfinished(self):
        app_id = _create_assigned("2")
        assert "returnToNew" in _detail(app_id, MANAGER)["availableActions"]
        assert _act(app_id, MANAGER, action="returnToNew").status_code == 204
        d = _detail(app_id, MANAGER)
        assert d["status"] == "new"
        assert d["isUnfinished"] is True
        assert d["previousExecutorId"] == "2"
        assert d["previousExecutor"]["id"] == "2"

    def test_manager_returns_in_progress_to_new(self):
        app_id = _create_assigned("2")
        assert _act(app_id, EXECUTOR, action="startWork").status_code == 204
        assert "returnToNew" in _detail(app_id, MANAGER)["availableActions"]
        assert _act(app_id, MANAGER, action="returnToNew").status_code == 204
        assert _detail(app_id, MANAGER)["status"] == "new"

    def test_executor_cannot_return_to_new_403(self):
        app_id = _create_assigned("2")
        assert "returnToNew" not in _detail(app_id, EXECUTOR)["availableActions"]
        assert _act(app_id, EXECUTOR, action="returnToNew").status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# External delegation is a tool executors can use too — only from `assigned` (§7.3)
# ─────────────────────────────────────────────────────────────────────────────

class TestExecutorExternalDelegation:
    def test_executor_can_delegate_externally_from_assigned(self):
        app_id = _create_assigned("2")
        assert "delegateExternal" in _detail(app_id, EXECUTOR)["availableActions"]
        assert _act(app_id, EXECUTOR, action="delegateExternal",
                    departmentId=str(DEP_OGE), comment="Не профиль отдела").status_code == 204
        assert _detail(app_id, MANAGER)["status"] == "delegated"

    def test_executor_cannot_delegate_externally_in_progress_403(self):
        app_id = _create_assigned("2")
        assert _act(app_id, EXECUTOR, action="startWork").status_code == 204
        assert "delegateExternal" not in _detail(app_id, EXECUTOR)["availableActions"]
        assert _act(app_id, EXECUTOR, action="delegateExternal",
                    departmentId=str(DEP_OGE), comment="x").status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Involvement-based action gating — executor identity + manager-as-executor
# ─────────────────────────────────────────────────────────────────────────────

class TestInvolvementGating:
    def test_unassigned_executor_has_no_actions_and_is_forbidden(self):
        app_id = _create_assigned("2")           # assigned to ivanov (emp 2)
        d = _detail(app_id, EXECUTOR2)           # petrov is not involved
        assert d["availableActions"] == []
        assert _act(app_id, EXECUTOR2, action="startWork").status_code == 403

    def test_manager_assigned_as_executor_gets_executor_actions(self):
        # The OGE manager (kuznetsov, emp 8) is assigned as executor of an IT app.
        app_id = int(_make_app(AUTHOR))
        assert _act(app_id, MANAGER, action="assignExecutor", executorId="8").status_code == 204
        d = _detail(app_id, DEPT_MANAGER)
        # out-of-department manager → no manager-tier actions, but executor-tier via involvement
        assert "startWork" in d["availableActions"]
        assert "assignExecutor" not in d["availableActions"]
        assert _act(app_id, DEPT_MANAGER, action="startWork").status_code == 204
        assert _detail(app_id, MANAGER)["status"] == "inProgress"
