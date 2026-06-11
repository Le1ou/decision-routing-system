"""
priority_api.py — настройки расчёта приоритета (вынесено из main.py при декомпозиции).

Сами коэффициенты персистятся в public.priority_settings (см. priority_settings_store);
формула приоритета — priority_module.
"""

from fastapi import APIRouter, Depends, HTTPException

from src import priority_module
from src import priority_settings_store as ps_store
from src.core import (
    _is_top_manager, _raise_for_db_error, _user_department_id, authObj,
    get_db_user, require_permission, require_top_manager,
)
from src.schemas import PrioritySettingsModel, PrioritySettingsResponse

router = APIRouter(tags=["Priority"])


@router.get("/priority-settings", summary="Получить коэффициенты расчета приоритета",
            description="Обычный руководитель получает настройки только своего отдела в режиме чтения. top-manager получает все отделы и может редактировать. Дополнительно отдаёт read-only параметры срочности (urgent) для предпросмотра на фронте.",
            response_model=PrioritySettingsResponse)
def get_priority_settings(userData=Depends(authObj.authenticate)):
    try:
        require_permission(userData, "canManagePrioritySettings")
        db = get_db_user(userData)
        login = userData[0]
        settings = ps_store.load_effective(db)

        # Read-only параметры бонуса срочности (config.json → priority).
        _urgent = priority_module._load_urgent_cfg()
        urgent = {"thresholdHours": _urgent["threshold_hours"], "bonus": _urgent["bonus"]}

        if _is_top_manager(login):
            return {**settings, "urgent": urgent}

        # A regular manager only sees their own department's coefficients.
        own = _user_department_id(db, login)
        own_key = str(own) if own is not None else None
        return {
            "department":    {own_key: settings["department"].get(own_key, ps_store.DEFAULT_COEFF)} if own_key else {},
            "managerAuthor": {own_key: settings["managerAuthor"].get(own_key, ps_store.DEFAULT_COEFF)} if own_key else {},
            "deadline":      settings["deadline"],
            "urgent":        urgent,
        }
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.put("/priority-settings", summary="Сохранить коэффициенты расчета приоритета",
            description="Доступно только top-manager.",
            response_model=PrioritySettingsModel)
def update_priority_settings(
    payload: PrioritySettingsModel,
    userData=Depends(authObj.authenticate),
):
    try:
        require_permission(userData, "canManagePrioritySettings")
        db = get_db_user(userData)
        login = userData[0]
        require_top_manager(login)   # only a top-manager may persist settings
        ps_store.save(db, dict(payload.department), dict(payload.managerAuthor), payload.deadline)
        # Echo back exactly what was saved (unchanged API contract); the merged
        # per-department defaults are applied on read (GET /priority-settings).
        return {
            "department":    dict(payload.department),
            "managerAuthor": dict(payload.managerAuthor),
            "deadline":      payload.deadline,
        }
    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)
