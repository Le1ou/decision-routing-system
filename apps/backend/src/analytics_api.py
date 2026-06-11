"""
analytics_api.py — HTTP-слой подсистемы аналитики (вынесено из main.py).

Вся вычислительная логика — в analytics_module; здесь только авторизация и scope.
Предварительная версия (см. docs/backend-functions.md §4) — у фронтенда пока нет
потребителя, формат JSON может измениться после согласования (docs/analytics-contract.md).
Доступ: право canViewReports; обычный руководитель — только свой отдел, top-manager —
все. from/to фильтруют по дате создания заявки; их отсутствие = «за всё время».
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src import analytics_module as analytics
from src.core import (
    _is_top_manager, _raise_for_db_error, _user_department_id, authObj,
    get_db_user, require_permission,
)

router = APIRouter(tags=["Analytics"])


def _analytics_scope(userData):
    """(db, department_id) — None для top-manager (все отделы), иначе свой отдел."""
    require_permission(userData, "canViewReports")
    db = get_db_user(userData)
    login = userData[0]
    dept = None if _is_top_manager(login) else _user_department_id(db, login)
    return db, dept


@router.get("/analytics/applications", summary="Аналитика по заявкам")
def analytics_applications(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.applications_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.get("/analytics/executors", summary="Аналитика по исполнителям")
def analytics_executors(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.executors_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.get("/analytics/work-types", summary="Аналитика по видам работ")
def analytics_work_types(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.work_types_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.get("/analytics/departments", summary="Аналитика по отделам")
def analytics_departments(
    userData=Depends(authObj.authenticate),
    createdFrom: Optional[str] = Query(default=None),
    createdTo:   Optional[str] = Query(default=None),
):
    try:
        db, dept = _analytics_scope(userData)
        return analytics.departments_stats(db, dept, createdFrom, createdTo)
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)
