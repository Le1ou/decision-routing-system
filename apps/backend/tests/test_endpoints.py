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
BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")

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
        # the flag is what filters: without it fedorov still sees novikova's app
        assert other in _list_ids(AUTHOR)

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
        assert set(r.json().keys()) == {"department", "managerAuthor", "deadline"}

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
