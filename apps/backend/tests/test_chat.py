"""
Integration tests for the application chat (chat_api): access matrix, afterId
incremental fetch, unread/read markers, closed-application read-only, and the
anti-spam notification rule.

Server must be running with freshly seeded data (same harness as test_endpoints.py).
Seeded application 4 = assigned_it: author fedorov (emp 7), executor ivanov (emp 2),
IT department; it carries a demo chat of 4 messages.
"""

import os

import pytest
import requests

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:3000")

TOP_MANAGER  = ("orlova_m",    "Manager!1")        # top-manager, IT
DEPT_MANAGER = ("kuznetsov_m", "Kuznetsov!7")      # plain manager, OGE (dept 2)
EXECUTOR_IT  = ("ivanov_i",    "SecretPassword!1") # executor IT, emp 2 (assigned to app 4)
EXECUTOR2    = ("petrov_p",    "Pa$$w0rd")         # executor IT, emp 3 (not involved in app 4)
AUTHOR_IT    = ("fedorov_a",   "Fedorov!6")        # author IT, emp 7 (author of app 4)
AUTHOR_HR    = ("novikova_e",  "Novikova!5")       # author HR, emp 6 (unrelated)

DEP_IT = 1
WORK_TYPE_IT = 1
APP_ASSIGNED = 4          # assigned_it: author fedorov, executor ivanov
APP_COMPLETED = 8         # completed seeded application

_session = requests.Session()


def _create_app(auth, department_id=DEP_IT, work_type_id=WORK_TYPE_IT):
    body = {"name": "Chat test app", "departmentId": str(department_id),
            "workTypeId": str(work_type_id), "deadlineAt": "2030-01-01T00:00:00Z",
            "description": "Chat test"}
    r = _session.post(f"{BASE_URL}/applications", auth=auth, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _messages(auth, app_id, **params):
    return _session.get(f"{BASE_URL}/applications/{app_id}/messages", auth=auth, params=params or None)


def _post(auth, app_id, text):
    return _session.post(f"{BASE_URL}/applications/{app_id}/messages", auth=auth, json={"text": text})


def _read(auth, app_id):
    return _session.post(f"{BASE_URL}/applications/{app_id}/messages/read", auth=auth)


def _assign(app_id, emp_id):
    r = _session.post(f"{BASE_URL}/applications/{app_id}/actions", auth=TOP_MANAGER,
                      json={"action": "assignExecutor", "executorId": str(emp_id)})
    assert r.status_code == 204, r.text


# ── Чтение и видимость ────────────────────────────────────────────────────────

def test_seeded_chat_visible_to_participants():
    r = _messages(AUTHOR_IT, APP_ASSIGNED)
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) >= 4
    # Сообщения по возрастанию id, есть текст и вложенный автор.
    ids = [int(m["id"]) for m in body["items"]]
    assert ids == sorted(ids)
    first = body["items"][0]
    assert first["text"] and first["author"] and first["author"]["fullName"]
    assert _messages(EXECUTOR_IT, APP_ASSIGNED).status_code == 200
    assert _messages(TOP_MANAGER, APP_ASSIGNED).status_code == 200   # руководитель видит


def test_unrelated_user_cannot_see_chat_404():
    # novikova (HR-автор) не причастна к ИТ-заявке 4 → как карточка, 404.
    assert _messages(AUTHOR_HR, APP_ASSIGNED).status_code == 404
    # petrov (ИТ-исполнитель, но не назначен на app 4) — тоже не причастен.
    assert _messages(EXECUTOR2, APP_ASSIGNED).status_code == 404


def test_messages_after_id_incremental():
    items = _messages(AUTHOR_IT, APP_ASSIGNED).json()["items"]
    assert len(items) >= 2
    mid = int(items[0]["id"])
    after = _messages(AUTHOR_IT, APP_ASSIGNED, afterId=mid).json()["items"]
    assert all(int(m["id"]) > mid for m in after)
    assert len(after) == len(items) - 1


# ── Отправка и права ──────────────────────────────────────────────────────────

def test_author_and_executor_can_post():
    app_id = _create_app(AUTHOR_IT)
    _assign(app_id, 2)   # ivanov
    assert _post(AUTHOR_IT, app_id, "Вопрос по заявке.").status_code == 201
    assert _post(EXECUTOR_IT, app_id, "Ответ исполнителя.").status_code == 201
    assert _post(TOP_MANAGER, app_id, "Комментарий руководителя.").status_code == 201
    assert len(_messages(AUTHOR_IT, app_id).json()["items"]) == 3


def test_unrelated_user_cannot_post():
    app_id = _create_app(AUTHOR_IT)
    _assign(app_id, 2)
    # petrov причастности не имеет → 404 (не раскрываем заявку).
    assert _post(EXECUTOR2, app_id, "x").status_code == 404
    # novikova (HR) — тоже.
    assert _post(AUTHOR_HR, app_id, "x").status_code == 404


def test_empty_text_422():
    app_id = _create_app(AUTHOR_IT)
    assert _post(AUTHOR_IT, app_id, "").status_code == 422


def test_closed_application_chat_is_read_only_409():
    # Завершённую заявку читать можно, писать — нельзя.
    assert _messages(TOP_MANAGER, APP_COMPLETED).status_code == 200
    assert _post(TOP_MANAGER, APP_COMPLETED, "поздно").status_code == 409


def test_post_to_missing_application_404():
    assert _post(TOP_MANAGER, 999999, "x").status_code == 404


# ── Непрочитанные и маркер прочтения ──────────────────────────────────────────

def test_unread_count_and_mark_read():
    app_id = _create_app(AUTHOR_IT)
    _assign(app_id, 2)   # ivanov
    # Автор пишет два сообщения — для исполнителя они непрочитанные.
    assert _post(AUTHOR_IT, app_id, "Первое.").status_code == 201
    assert _post(AUTHOR_IT, app_id, "Второе.").status_code == 201

    exec_view = _messages(EXECUTOR_IT, app_id).json()
    assert exec_view["unreadCount"] == 2
    # Свои сообщения автору непрочитанными не считаются.
    assert _messages(AUTHOR_IT, app_id).json()["unreadCount"] == 0

    assert _read(EXECUTOR_IT, app_id).status_code == 204
    assert _messages(EXECUTOR_IT, app_id).json()["unreadCount"] == 0

    # Новое сообщение после прочтения снова поднимает счётчик.
    assert _post(AUTHOR_IT, app_id, "Третье.").status_code == 201
    assert _messages(EXECUTOR_IT, app_id).json()["unreadCount"] == 1


def test_posting_marks_own_messages_read():
    app_id = _create_app(AUTHOR_IT)
    _assign(app_id, 2)
    assert _post(AUTHOR_IT, app_id, "От автора.").status_code == 201
    # Исполнитель отвечает — его собственный ответ не считается ему непрочитанным,
    # но сообщение автора (до маркера) — да? Нет: ответ двигает маркер на now,
    # сообщение автора было раньше → остаётся непрочитанным до явного read.
    assert _post(EXECUTOR_IT, app_id, "От исполнителя.").status_code == 201
    # У автора непрочитано ровно одно (ответ исполнителя).
    assert _messages(AUTHOR_IT, app_id).json()["unreadCount"] == 1


# ── Уведомление с анти-спамом ─────────────────────────────────────────────────

def _chat_notif_count(auth, app_id):
    items = _session.get(f"{BASE_URL}/notifications", auth=auth).json()["items"]
    return sum(1 for n in items
               if str(n.get("applicationId")) == str(app_id) and "чате" in n.get("text", ""))


def test_chat_notification_antispam():
    app_id = _create_app(AUTHOR_IT)
    _assign(app_id, 2)   # ivanov
    # Исполнитель читает чат (маркер на now), чтобы стартовать «с чистого листа».
    _read(EXECUTOR_IT, app_id)

    before = _chat_notif_count(EXECUTOR_IT, app_id)
    assert _post(AUTHOR_IT, app_id, "Сообщение 1").status_code == 201
    after_first = _chat_notif_count(EXECUTOR_IT, app_id)
    assert after_first == before + 1, "первое сообщение должно создать уведомление"

    # Вторая реплика подряд (исполнитель ещё не читал) — без нового уведомления.
    assert _post(AUTHOR_IT, app_id, "Сообщение 2").status_code == 201
    assert _chat_notif_count(EXECUTOR_IT, app_id) == after_first, "анти-спам: дубля быть не должно"

    # Исполнитель прочитал — следующее сообщение снова уведомляет.
    _read(EXECUTOR_IT, app_id)
    assert _post(AUTHOR_IT, app_id, "Сообщение 3").status_code == 201
    assert _chat_notif_count(EXECUTOR_IT, app_id) == after_first + 1
