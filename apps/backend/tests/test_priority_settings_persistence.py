"""
Integration tests for the DB-backed priority-settings store.

Focus: the GET/PUT contract is unchanged, PUT values are read back (persisted in
public.priority_settings instead of an in-memory dict), and a regular manager is
scoped to their own department.

Usage (same as test_endpoints.py — server must be running with seeded data):
    pytest apps/backend/tests/test_priority_settings_persistence.py -v
"""

import os

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")

TOP_MANAGER  = ("orlova_m",    "Manager!1")     # top-manager, all departments
DEPT_MANAGER = ("kuznetsov_m", "Kuznetsov!7")   # plain manager, OGE (department 2)

_session = requests.Session()


def _get(auth):
    return _session.get(f"{BASE_URL}/priority-settings", auth=auth)


def _put(auth, body):
    return _session.put(f"{BASE_URL}/priority-settings", auth=auth, json=body)


def test_put_then_get_reflects_saved_values():
    """A top-manager PUT is read back by a subsequent GET (persisted, not lost)."""
    body = {
        "department":    {"1": 0.55, "2": 0.45},
        "managerAuthor": {"1": 0.35, "2": 0.25},
        "deadline":      0.65,
    }
    assert _put(TOP_MANAGER, body).status_code == 200

    data = _get(TOP_MANAGER).json()
    # Saved coefficients are present…
    assert data["department"]["1"] == 0.55
    assert data["department"]["2"] == 0.45
    assert data["managerAuthor"]["1"] == 0.35
    assert data["deadline"] == 0.65
    # …и для незаданного отдела k_отдела по умолчанию = важность отдела (department.value),
    # а managerAuthor по умолчанию = 0.2.
    deps = _session.get(f"{BASE_URL}/departments", auth=TOP_MANAGER).json()["items"]
    dep3_value = next(d["value"] for d in deps if str(d["id"]) == "3")
    assert data["department"]["3"] == pytest.approx(dep3_value)
    assert data["managerAuthor"]["3"] == pytest.approx(0.2)


def test_get_shape_is_stable():
    data = _get(TOP_MANAGER).json()
    assert set(data.keys()) == {"department", "managerAuthor", "deadline", "urgent"}
    assert isinstance(data["department"], dict)
    assert isinstance(data["managerAuthor"], dict)
    assert isinstance(data["deadline"], (int, float))
    # read-only параметры срочности для предпросмотра на фронте
    assert set(data["urgent"].keys()) == {"thresholdHours", "bonus"}
    assert isinstance(data["urgent"]["thresholdHours"], (int, float))
    assert isinstance(data["urgent"]["bonus"], (int, float))


def test_regular_manager_sees_only_own_department():
    """A plain manager (OGE, dept 2) gets a single-department view (read-only)."""
    data = _get(DEPT_MANAGER).json()
    assert set(data["department"].keys()) == {"2"}
    assert set(data["managerAuthor"].keys()) == {"2"}


def test_regular_manager_cannot_persist():
    body = {"department": {"2": 0.3}, "managerAuthor": {"2": 0.3}, "deadline": 0.3}
    assert _put(DEPT_MANAGER, body).status_code == 403
