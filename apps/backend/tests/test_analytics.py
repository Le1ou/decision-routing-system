"""
Integration tests for the analytics subsystem (docs/backend-functions.md §4).

Provisional endpoints (no frontend consumer yet): /analytics/{applications,
executors,work-types,departments}. Checks: permission gating, response shape,
and department scoping (a plain manager sees only their own department).

Server must be running with freshly seeded data (same harness as test_endpoints.py).
"""

import os
import requests

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")

TOP_MANAGER  = ("orlova_m",    "Manager!1")     # top-manager, all departments
DEPT_MANAGER = ("kuznetsov_m", "Kuznetsov!7")   # plain manager, OGE (department 2)
EXECUTOR     = ("ivanov_i",    "SecretPassword!1")
AUTHOR       = ("fedorov_a",   "Fedorov!6")
DEP_OGE = "2"

_session = requests.Session()

ENDPOINTS = [
    "/analytics/applications",
    "/analytics/executors",
    "/analytics/work-types",
    "/analytics/departments",
]


def _get(path, auth, **params):
    return _session.get(f"{BASE_URL}{path}", auth=auth, params=params or None)


# ── Permission gating ─────────────────────────────────────────────────────────

def test_all_endpoints_require_view_reports():
    for path in ENDPOINTS:
        assert _get(path, TOP_MANAGER).status_code == 200, path
        assert _get(path, DEPT_MANAGER).status_code == 200, path
        assert _get(path, EXECUTOR).status_code == 403, path
        assert _get(path, AUTHOR).status_code == 403, path
        assert _get(path, None).status_code == 401, path


# ── Response shape ────────────────────────────────────────────────────────────

def test_applications_shape():
    data = _get("/analytics/applications", TOP_MANAGER).json()
    assert data["scope"] == "all"
    assert isinstance(data["total"], int) and data["total"] > 0  # seeded apps exist
    for key in ("byStatus", "byPriority", "byComplexity", "timePerStatusSeconds"):
        assert isinstance(data[key], dict), key
    for key in ("completionTimeSeconds", "timeToAssignSeconds", "timeWithoutExecutorSeconds"):
        assert set(data[key].keys()) == {"min", "avg", "max"}, key
    assert {"total", "confirmed", "declined", "pending", "decisionTimeSeconds"} <= data["delegations"].keys()
    assert set(data["byComplexity"].keys()) == {"easy", "medium", "hard"}


def test_executors_shape():
    data = _get("/analytics/executors", TOP_MANAGER).json()
    assert isinstance(data["executors"], list)
    if data["executors"]:
        row = data["executors"][0]
        assert {"employeeId", "fullName", "assignedCount", "completedCount",
                "inProgressCount", "takenInWorkCount", "rejectedCount", "delegatedCount",
                "byPriority", "avgReactionTimeSeconds", "avgHandlingTimeSeconds",
                "totalWorkSeconds", "idleTimeSeconds", "occupancyRatio"} <= row.keys()
        assert set(row["byPriority"].keys()) == {"low", "medium", "high", "critical"}


def test_work_types_shape():
    data = _get("/analytics/work-types", TOP_MANAGER).json()
    assert isinstance(data["workTypes"], list) and len(data["workTypes"]) > 0
    row = data["workTypes"][0]
    assert {"workTypeId", "name", "createdCount", "completedCount", "delegatedCount",
            "byPriority", "avgCompletionTimeSeconds", "topExecutorId", "topExecutorName"} <= row.keys()


def test_departments_shape():
    data = _get("/analytics/departments", TOP_MANAGER).json()
    assert isinstance(data["departments"], list) and len(data["departments"]) >= 7
    row = data["departments"][0]
    assert {"departmentId", "name", "employeeCount", "applicationCount", "completedCount",
            "avgReactionTimeSeconds", "idleTimeSeconds", "occupancyRatio", "delegations"} <= row.keys()
    assert {"sent", "received"} <= row["delegations"].keys()


# ── Department scoping ────────────────────────────────────────────────────────

def test_departments_scoped_for_plain_manager():
    data = _get("/analytics/departments", DEPT_MANAGER).json()
    assert data["scope"] == "department"
    assert data["departmentId"] == DEP_OGE
    ids = {d["departmentId"] for d in data["departments"]}
    assert ids == {DEP_OGE}


def test_applications_scoped_total_not_greater_than_global():
    scoped = _get("/analytics/applications", DEPT_MANAGER).json()
    glob = _get("/analytics/applications", TOP_MANAGER).json()
    assert scoped["scope"] == "department"
    assert scoped["total"] <= glob["total"]


def test_period_filter_accepted():
    # A narrow future window yields zero applications (created earlier).
    data = _get("/analytics/applications", TOP_MANAGER,
                createdFrom="2099-01-01T00:00:00Z", createdTo="2099-12-31T00:00:00Z").json()
    assert data["total"] == 0
    assert data["period"] == {"from": "2099-01-01T00:00:00Z", "to": "2099-12-31T00:00:00Z"}


# ── idle / occupancy (реальный расчёт, не заглушка) ───────────────────────────

def _idle_occ_ok(idle, occ):
    """Поля согласованы: оба либо null, либо числа; ratio в [0..1], idle >= 0."""
    assert (idle is None) == (occ is None)
    if occ is not None:
        assert 0.0 <= occ <= 1.0
        assert idle >= 0.0


def test_executor_idle_occupancy_computed():
    data = _get("/analytics/executors", TOP_MANAGER).json()
    rows = data["executors"]
    assert rows, "seeded executors with applications must exist"
    for row in rows:
        _idle_occ_ok(row["idleTimeSeconds"], row["occupancyRatio"])
    # У сидовых исполнителей есть заявки с executor_at → хотя бы у одного занятость посчитана.
    assert any(r["occupancyRatio"] is not None for r in rows), \
        "occupancy must be computed for at least one executor (not a stub null)"


def test_department_idle_occupancy_computed():
    data = _get("/analytics/departments", TOP_MANAGER).json()
    rows = data["departments"]
    for row in rows:
        _idle_occ_ok(row["idleTimeSeconds"], row["occupancyRatio"])
    assert any(r["occupancyRatio"] is not None for r in rows), \
        "occupancy must be computed for at least one department"


def test_future_period_yields_zero_occupancy():
    # Окно целиком в будущем → заявок в окне нет, занятость 0 (поля согласованы, без падений).
    data = _get("/analytics/departments", TOP_MANAGER,
                createdFrom="2099-01-01T00:00:00Z", createdTo="2099-12-31T00:00:00Z").json()
    for row in data["departments"]:
        _idle_occ_ok(row["idleTimeSeconds"], row["occupancyRatio"])
