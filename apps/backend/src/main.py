"""
main.py — точка сборки приложения.

После декомпозиции здесь остались только:
  • импорт src.core (на импорте выполняет миграции, гранты и сидинг БД);
  • фоновый цикл подсистем событий и маршрутизации (lifespan);
  • создание FastAPI-приложения, CORS и подключение роутеров.

Логика по доменам:
  schemas.py            — Pydantic-модели контракта API
  core.py               — инфраструктура: подключения, роли, права, scope
  applications_api.py   — заявки (список/создание/карточка/действия/вложения)
  directories_api.py    — справочники (отделы, сотрудники, виды работ, …)
  auth_api.py           — /auth/me
  priority_api.py       — настройки приоритета (+ priority_module, priority_settings_store)
  notifications_api.py  — уведомления
  reports_api.py        — отчёты и XLS
  analytics_api.py      — аналитика (+ analytics_module)
  events_module.py      — подсистема событий (дедлайны, просрочка, пересчёт приоритета)
  routing_module.py     — подсистема маршрутизации (авто-назначение, вытеснение)
  s3_module.py          — S3-клиенты вложений
  db_helpers.py         — общие SQL-хелперы (уведомления, журнал статусов)
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.application_module import configData
# Импорт core выполняет bootstrap: миграции, Postgres-роли/гранты, сидинг БД
# (или восстановление состояния из S3 — см. config.json → "startup").
from src import backup_module
from src.core import BACKUP_ON_SHUTDOWN, DBController, _ad_directory
from src import events_module as events
from src import (
    analytics_api, applications_api, auth_api, directories_api,
    notifications_api, priority_api, reports_api,
)

# ─────────────────────────── Events subsystem loop ───────────────────────────
# Background loop that drives the events subsystem (deadline notifications +
# overdue marking + priority recompute) and the routing subsystem. Runs
# in-process via asyncio; the synchronous DB tick is offloaded to a thread so it
# never blocks the event loop. Configured via the "events" block in
# apps/backend/config.json:
#   "enabled"      — true/false (default true)
#   "tick_seconds" — interval between ticks (default 60)
# The first tick happens AFTER one interval, so the fast test suite never triggers it.
_events_cfg = configData.get("events", {}) or {}
_events_enabled = _events_cfg.get("enabled", True)
EVENTS_ENABLED = (_events_enabled if isinstance(_events_enabled, bool)
                  else str(_events_enabled).strip().lower() in ("1", "true", "yes", "on"))
# ENV override (EVENTS_ENABLED=false) — для детерминированного прогона тестов: фоновый
# цикл маршрутизации/событий мутирует сид со временем (вытеснения и т.п.), поэтому при
# тестах его удобно выключить, не трогая config.json/compose. Тесты вызывают run_routing/
# run_tick напрямую, фоновый цикл им не нужен.
_env_events = os.environ.get("EVENTS_ENABLED")
if _env_events is not None and _env_events.strip() != "":   # пустая строка = «не задано»
    EVENTS_ENABLED = _env_events.strip().lower() in ("1", "true", "yes", "on")
try:
    EVENTS_TICK_SECONDS = max(1, int(_events_cfg.get("tick_seconds", 60)))
except (TypeError, ValueError):
    EVENTS_TICK_SECONDS = 60


@asynccontextmanager
async def lifespan(_app):
    task = None
    if EVENTS_ENABLED:
        async def _loop():
            from src import routing_module
            while True:
                await asyncio.sleep(EVENTS_TICK_SECONDS)
                try:
                    result = await asyncio.to_thread(events.run_tick, DBController)
                    if result.get("expired") or result.get("deadlineNotifications"):
                        print(f"[events] tick: {result}")
                except Exception as e:
                    print(f"[events] tick error: {e}")
                # Маршрутизация — отдельным шагом после событий (использует свежий приоритет).
                try:
                    routed = await asyncio.to_thread(routing_module.run_routing, DBController)
                    if routed.get("assigned") or routed.get("evicted") or routed.get("escalated"):
                        print(f"[routing] tick: {routed}")
                except Exception as e:
                    print(f"[routing] tick error: {e}")
        task = asyncio.create_task(_loop())
        print(f"[events] background loop enabled (tick={EVENTS_TICK_SECONDS}s).")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        # Резервная копия при аккуратном выключении: дамп БД + снимок onboarding-
        # состояния каталога в S3 (см. backup_module). Best-effort: сбой бэкапа не
        # должен мешать остановке процесса.
        if BACKUP_ON_SHUTDOWN:
            try:
                await asyncio.to_thread(backup_module.backup_database)
                await asyncio.to_thread(backup_module.save_directory_snapshot, _ad_directory())
            except Exception as e:
                print(f"[backup] shutdown backup failed: {e}")


app = FastAPI(
    title="Decision Routing System API",
    version="0.1.0",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Auth",          "description": "Текущий пользователь"},
        {"name": "Applications",  "description": "Производственные заявки"},
        {"name": "Directories",   "description": "Отделы, сотрудники, должности и виды работ"},
        {"name": "Priority",      "description": "Настройки расчета приоритета"},
        {"name": "Notifications", "description": "Уведомления текущего пользователя"},
        {"name": "Reports",       "description": "Отчеты и XLS-выгрузка"},
        {"name": "Analytics",     "description": "Статистика по заявкам, исполнителям, видам работ и отделам"},
    ],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth_api.router)
app.include_router(applications_api.router)
app.include_router(directories_api.router)
app.include_router(priority_api.router)
app.include_router(notifications_api.router)
app.include_router(reports_api.router)
app.include_router(analytics_api.router)
