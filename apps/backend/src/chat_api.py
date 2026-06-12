"""
chat_api.py — чат заявки: переписка между автором, назначенным исполнителем и
руководителем-в-scope (вынесено отдельным роутером).

Модель (pull / polling, без WebSocket — у нас Basic Auth на каждый запрос):
  • GET  /applications/{id}/messages?afterId= — сообщения (инкрементально по afterId)
    + unreadCount для текущего пользователя; читать может любой, кто видит карточку.
  • POST /applications/{id}/messages {text} — добавить сообщение; писать могут автор,
    назначенный исполнитель и руководитель-в-scope; в закрытой заявке (completed/
    rejected) — 409 (история read-only).
  • POST /applications/{id}/messages/read — обновить маркер прочитанности (UPSERT в
    application_chat_read).

Доступ переиспользует _action_scope/_can_view_application из applications_api (та же
видимость, что у карточки заявки). Уведомление собеседнику — best-effort после коммита,
с анти-спамом: только если у адресата ещё нет непрочитанных сообщений этого чата.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response
from psycopg.rows import dict_row

from src import db_helpers
from src.application_module import project_timezone
from src.applications_api import _action_scope, _can_view_application, _user_dict
from src.core import (
    DBController, _employee_id, _get_user_role, _raise_for_db_error, authObj,
    get_db_user, login_by_employee_map, row_or_404,
)
from src.schemas import ChatMessagesResponse, CreateChatMessagePayload, IdResponse

router = APIRouter(tags=["Chat"])


def _load_app_scope_row(cur, application_id):
    """Строка заявки с полями, нужными для проверки причастности и уведомлений."""
    return cur.execute(
        """
        SELECT a.application_id, a.name, a.department_id, s.name AS status_name,
               author_link.employee_id AS author_id,
               exec_link.employee_id   AS executor_id,
               dl.delegated_to          AS delegated_to
        FROM public.application a
        LEFT JOIN public.status s ON s.status_id = a.status_id
        LEFT JOIN public.employee_to_application author_link
               ON author_link.application_id = a.application_id
              AND author_link.role_id = (SELECT role_id FROM public.role WHERE name = 'author' LIMIT 1)
        LEFT JOIN public.employee_to_application exec_link
               ON exec_link.application_id = a.application_id
              AND exec_link.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
        LEFT JOIN public.delegated dl ON dl.delegated_id = a.delegated_id
        WHERE a.application_id = %s
        """,
        (int(application_id),),
    ).fetchone()


def _can_write(db, login, app_row, user_role) -> bool:
    """Писать могут автор, назначенный исполнитель и руководитель-в-scope."""
    is_author, is_exec, mgr_scope = _action_scope(db, login, app_row, user_role)
    return bool(is_author or is_exec or mgr_scope)


@router.get("/applications/{applicationId}/messages", summary="Получить сообщения чата заявки",
            description="Сообщения чата заявки + число непрочитанных для текущего пользователя. "
                        "afterId — вернуть только сообщения с message_id больше указанного "
                        "(инкрементальная дозагрузка для опроса). Читать может любой, кто видит карточку.",
            response_model=ChatMessagesResponse)
def get_messages(
    applicationId: int = Path(...),
    afterId: Optional[int] = Query(default=None, ge=0),
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_role = _get_user_role(login)
        emp_id = _employee_id(login)

        with db.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                app_row = _load_app_scope_row(cur, applicationId)
                row_or_404(app_row, "Application not found")
                # Видимость чата = видимость карточки (чужую заявку — 404).
                if not _can_view_application(db, login, app_row, user_role):
                    raise HTTPException(status_code=404, detail="Application not found")

                query = ("SELECT * FROM public.application_message "
                         "WHERE application_id = %s")
                params = [int(applicationId)]
                if afterId:
                    query += " AND message_id > %s"
                    params.append(int(afterId))
                query += " ORDER BY message_id ASC"
                rows = cur.execute(query, params).fetchall()

                # Непрочитанные = сообщения других после last_read_at пользователя.
                unread = 0
                if emp_id is not None:
                    unread = cur.execute(
                        """
                        SELECT COUNT(*) AS cnt FROM public.application_message m
                        WHERE m.application_id = %s
                          AND m.author_employee_id IS DISTINCT FROM %s
                          AND m.created_at > COALESCE(
                              (SELECT last_read_at FROM public.application_chat_read
                               WHERE application_id = %s AND employee_id = %s),
                              '-infinity'::timestamptz)
                        """,
                        (int(applicationId), emp_id, int(applicationId), emp_id),
                    ).fetchone()["cnt"]

                login_by_emp = login_by_employee_map()
                items = []
                for r in rows:
                    item = {
                        "message_id": r["message_id"],
                        "application_id": r["application_id"],
                        "author_employee_id": r["author_employee_id"],
                        "text": r["text"],
                        "created_at": r["created_at"],
                        "author": _user_dict(cur, r["author_employee_id"], login_by_emp),
                    }
                    items.append(item)

        return {"items": items, "unreadCount": unread}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.post("/applications/{applicationId}/messages", status_code=201,
             summary="Отправить сообщение в чат заявки",
             description="Писать могут автор, назначенный исполнитель и руководитель-в-scope. "
                         "После завершения/отклонения заявки чат read-only (409).",
             response_model=IdResponse)
def post_message(
    payload: CreateChatMessagePayload,
    applicationId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_role = _get_user_role(login)
        emp_id = _employee_id(login)
        now = datetime.now(project_timezone)

        with db.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                app_row = _load_app_scope_row(cur, applicationId)
                row_or_404(app_row, "Application not found")
                if not _can_write(db, login, app_row, user_role):
                    # Чужой/непричастный не должен даже знать о существовании — 404.
                    if not _can_view_application(db, login, app_row, user_role):
                        raise HTTPException(status_code=404, detail="Application not found")
                    raise HTTPException(status_code=403, detail="Not permitted to post in this chat")
                if app_row.get("status_name") in ("completed", "rejected"):
                    raise HTTPException(status_code=409,
                                        detail="Chat is read-only for a closed application")

                msg_id = cur.execute(
                    "INSERT INTO public.application_message "
                    "(application_id, author_employee_id, text, created_at) "
                    "VALUES (%s, %s, %s, %s) RETURNING message_id",
                    (int(applicationId), emp_id, payload.text, now),
                ).fetchone()["message_id"]
                # Отправитель прочитал собственное сообщение — двигаем его маркер.
                if emp_id is not None:
                    cur.execute(
                        "INSERT INTO public.application_chat_read (application_id, employee_id, last_read_at) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (application_id, employee_id) DO UPDATE SET last_read_at = EXCLUDED.last_read_at",
                        (int(applicationId), emp_id, now),
                    )

        _notify_recipients(applicationId, app_row, emp_id, now)
        return {"id": str(msg_id)}

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.post("/applications/{applicationId}/messages/read", status_code=204,
             summary="Отметить чат заявки прочитанным",
             description="Двигает маркер прочитанности текущего пользователя на текущий момент.")
def mark_chat_read(
    applicationId: int = Path(...),
    userData=Depends(authObj.authenticate),
):
    try:
        db = get_db_user(userData)
        login = userData[0]
        user_role = _get_user_role(login)
        emp_id = _employee_id(login)
        now = datetime.now(project_timezone)

        with db.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                app_row = _load_app_scope_row(cur, applicationId)
                row_or_404(app_row, "Application not found")
                if not _can_view_application(db, login, app_row, user_role):
                    raise HTTPException(status_code=404, detail="Application not found")
                if emp_id is not None:
                    cur.execute(
                        "INSERT INTO public.application_chat_read (application_id, employee_id, last_read_at) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (application_id, employee_id) DO UPDATE SET last_read_at = EXCLUDED.last_read_at",
                        (int(applicationId), emp_id, now),
                    )
        return Response(status_code=204)

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


def _notify_recipients(application_id, app_row, sender_emp, at) -> None:
    """Уведомить собеседников о новом сообщении (best-effort, post-commit).

    Получатели — причастные к заявке (автор, исполнитель, руководители отдела), кроме
    отправителя. Анти-спам: уведомляем только тех, у кого ещё НЕТ непрочитанных
    сообщений в этом чате (чтобы серия реплик дала одно уведомление, а не на каждую).
    """
    try:
        name = app_row.get("name") or f"#{application_id}"
        with DBController.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                recipients = set()
                if app_row.get("author_id"):
                    recipients.add(app_row["author_id"])
                if app_row.get("executor_id"):
                    recipients.add(app_row["executor_id"])
                for mid in db_helpers.dept_manager_ids(cur, app_row.get("department_id")):
                    recipients.add(mid)
                recipients.discard(sender_emp)

                for rid in recipients:
                    # Сколько непрочитанных у адресата ДО только что добавленного
                    # сообщения (исключаем его, чтобы не подавить уведомление о нём же).
                    unread = cur.execute(
                        """
                        SELECT COUNT(*) AS cnt FROM public.application_message m
                        WHERE m.application_id = %s
                          AND m.author_employee_id IS DISTINCT FROM %s
                          AND m.created_at < %s
                          AND m.created_at > COALESCE(
                              (SELECT last_read_at FROM public.application_chat_read
                               WHERE application_id = %s AND employee_id = %s),
                              '-infinity'::timestamptz)
                        """,
                        (int(application_id), rid, at, int(application_id), rid),
                    ).fetchone()["cnt"]
                    if unread == 0:
                        db_helpers.notify(cur, f"Новое сообщение в чате заявки «{name}».",
                                          rid, application_id, at)
    except Exception as e:
        print(f"[chat] notify failed for app={application_id}: {e}")
