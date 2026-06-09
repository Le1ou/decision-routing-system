"""
Tests for the priority subsystem:
  • pure formula `priority_module.compute_priority_score` and `score_to_level`
    (no DB — the swappable part of the calculation);
  • end-to-end: creating an application computes a real priority (not the old stub).

Imports `src` → runs inside the backend container (как и test_events.py).
Server must be running with seeded data.
"""

import os
from datetime import datetime, timedelta, timezone

import pytest
import requests

from src import priority_module as pm

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")
AUTHOR = ("fedorov_a", "Fedorov!6")   # author, IT (не руководитель)

_session = requests.Session()


# ── Чистая формула (изолированная функция) ────────────────────────────────────

def test_score_to_level_thresholds():
    assert pm.score_to_level(0.0) == "low"
    assert pm.score_to_level(0.37) == "low"
    assert pm.score_to_level(0.38) == "medium"
    assert pm.score_to_level(0.61) == "medium"
    assert pm.score_to_level(0.62) == "high"
    assert pm.score_to_level(0.82) == "critical"
    assert pm.score_to_level(1.0) == "critical"


def test_far_deadline_just_created_is_low():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    score = pm.compute_priority_score(
        department_coeff=0.2, deadline_weight=0.2,
        created_at=now, deadline=now + timedelta(days=30), now=now,
        urgent_threshold_hours=24, urgent_bonus=0.5,
    )
    assert score == pytest.approx(0.0)          # k_времени=0, не срочно, не руководитель
    assert pm.score_to_level(score) == "low"


def test_urgent_bonus_applied():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    score = pm.compute_priority_score(
        department_coeff=0.2, deadline_weight=0.2,
        created_at=now, deadline=now + timedelta(hours=12), now=now,
        urgent_threshold_hours=24, urgent_bonus=0.5,
    )
    assert score == pytest.approx(0.5)          # k_времени=0 + бонус срочности 0.5
    assert pm.score_to_level(score) == "medium"


def test_manager_author_coeff_added():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    kwargs = dict(department_coeff=0.5, deadline_weight=1.0,
                  created_at=now, deadline=now + timedelta(days=10), now=now,
                  manager_author_coeff=0.3, urgent_threshold_hours=24, urgent_bonus=0.0)
    base = pm.compute_priority_score(is_manager_author=False, **kwargs)
    with_mgr = pm.compute_priority_score(is_manager_author=True, **kwargs)
    assert base == pytest.approx(0.0)
    assert with_mgr == pytest.approx(0.3)


def test_time_factor_grows_and_clamps():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = created + timedelta(hours=10)
    common = dict(department_coeff=1.0, deadline_weight=1.0, created_at=created,
                  deadline=deadline, urgent_threshold_hours=0, urgent_bonus=0.0)
    mid = pm.compute_priority_score(now=created + timedelta(hours=5), **common)   # 50%
    past = pm.compute_priority_score(now=deadline + timedelta(hours=5), **common)  # просрочка
    assert mid == pytest.approx(0.5)
    assert past == pytest.approx(1.0)           # зажато в [0,1]


# ── Интеграция: создание заявки считает реальный приоритет ────────────────────

def _create(deadline_at: str) -> str:
    body = {"name": "Priority test", "departmentId": "1", "workTypeId": "1",
            "deadlineAt": deadline_at, "description": "priority test"}
    r = _session.post(f"{BASE_URL}/applications", auth=AUTHOR, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _priority(app_id: str) -> str:
    r = _session.get(f"{BASE_URL}/applications/{app_id}", auth=AUTHOR)
    return r.json()["application"]["priority"]


def test_create_far_deadline_is_low():
    deadline = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert _priority(_create(deadline)) == "low"


def test_create_urgent_deadline_elevated():
    # Дедлайн в пределах порога срочности → бонус поднимает приоритет выше «Низкого».
    deadline = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    assert _priority(_create(deadline)) != "low"
