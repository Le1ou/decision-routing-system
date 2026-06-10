"""
notifications_api.py — уведомления текущего пользователя (вынесено из main.py).

Создание уведомлений живёт в подсистемах-источниках (управление/события/маршрутизация,
общие хелперы — db_helpers); здесь только чтение и отметка прочитанности.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from psycopg.rows import dict_row

from src.core import (
    _employee_id, _raise_for_db_error, authObj, get_db_user, row_or_404,
)
from src.schemas import NotificationOut, NotificationsResponse

router = APIRouter(tags=["Notifications"])


@router.get("/notifications", summary="Получить уведомления текущего пользователя",
            description="Текущий контракт рассчитан на pull-модель: backend создает уведомления при событиях системы, frontend периодически запрашивает список или обновляет его после действий пользователя.",
            response_model=NotificationsResponse)
def get_notifications(
    userData=Depends(authObj.authenticate),
    unreadOnly: bool = Query(default=False),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = _employee_id(login)

        if emp_id is None:
            return {"items": [], "unreadCount": 0}

        query = "SELECT * FROM public.notification WHERE employee_id = %s"
        if unreadOnly:
            query += " AND is_read = false"
        query += " ORDER BY created_at DESC"

        with db.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(query, (emp_id,)).fetchall()
                unread_count = cur.execute(
                    "SELECT COUNT(*) AS cnt FROM public.notification WHERE employee_id = %s AND is_read = false",
                    (emp_id,)
                ).fetchone()["cnt"]

        items = [NotificationOut.model_validate(r).model_dump() for r in rows]
        return {"items": items, "unreadCount": unread_count}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.post("/notifications/{notificationId}/read", status_code=204,
             summary="Отметить уведомление прочитанным")
def mark_notification_read(
    notificationId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        emp_id = _employee_id(userData[0])
        rows = db.getRowFromTable("notification", "notification_id", int(notificationId))
        row_or_404(rows, "Notification not found")
        # Уведомление можно отметить прочитанным только своё (иначе любой пользователь мог
        # бы менять is_read чужих уведомлений по id). Чужое → 404 (не раскрываем существование).
        if emp_id is None or rows[0].get("employee_id") != emp_id:
            raise HTTPException(status_code=404, detail="Notification not found")

        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE public.notification SET is_read = true "
                    "WHERE notification_id = %s AND employee_id = %s",
                    (int(notificationId), emp_id)
                )
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.post("/notifications/read-all", status_code=204,
             summary="Отметить все уведомления текущего пользователя прочитанными")
def mark_all_notifications_read(userData=Depends(authObj.authenticate)):
    try:
        db = get_db_user(userData)
        login = userData[0]
        emp_id = _employee_id(login)

        if emp_id is not None:
            with db.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE public.notification SET is_read = true WHERE employee_id = %s",
                        (emp_id,)
                    )
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)
