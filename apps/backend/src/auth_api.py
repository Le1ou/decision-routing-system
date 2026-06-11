"""
auth_api.py — текущий пользователь (/auth/me). Вынесено из main.py при декомпозиции.
"""

from fastapi import APIRouter, Depends, HTTPException

from src.application_module import configData
from src.core import (
    _base_role, _expand_roles, _raise_for_db_error, _user_cfg, authObj,
    get_db_user, held_permissions, row_or_404,
)
from src.schemas import CurrentUserOut, CurrentUserResponse

router = APIRouter(tags=["Auth"])


@router.get("/auth/me", summary="Получить текущего пользователя",
            description="Возвращает пользователя, найденного через Basic Auth/Active Directory, его роль, отдел, должность и права frontend.",
            response_model=CurrentUserResponse)
def get_current_user(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_cfg = _user_cfg(login)

        emp_id = user_cfg.get("employee_id")
        rows = db.getRowFromTable("employee", "employee_id", emp_id)
        row_or_404(rows, "Employee not found")
        row = rows[0]

        # Enrich with login and the cumulative AD roles (not stored in DB).
        # A manager, for example, also implicitly holds author + executor.
        row["login"]  = login
        row["roles"]  = _expand_roles(_base_role(login))

        # Resolve job title (должность) from post_grade → post.
        # positionId is the post_id; postName is the post name (both come from AD).
        row["post_id"]   = ""
        row["post_name"] = ""
        pg_rows = db.getRowFromTable("post_grade", "post_grade_id", row.get("post_grade_id"))
        if pg_rows:
            post_rows = db.getRowFromTable("post", "post_id", pg_rows[0]["post_post_id"])
            if post_rows:
                row["post_id"]   = post_rows[0]["post_id"]
                row["post_name"] = post_rows[0]["name"]

        user_out = CurrentUserOut.model_validate(row)
        # One query for all permission roles (was one query per permission).
        perms = held_permissions(login, configData["PERMISSIONS"])
        return {"user": user_out.model_dump(), "permissions": perms}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)
