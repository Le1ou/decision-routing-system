"""
reports_api.py — отчёты по заявкам: JSON-предпросмотр и XLS-выгрузка
(вынесено из main.py при декомпозиции). Оба эндпоинта строятся на одном
SQL-запросе (_build_report_query); scope по отделу для обычного руководителя.
"""

import csv
import io
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from psycopg.rows import dict_row

from src.core import (
    _is_top_manager, _raise_for_db_error, _user_department_id, authObj,
    get_db_user, require_permission,
)
from src.schemas import ApplicationReportResponse, ApplicationReportRowOut

router = APIRouter(tags=["Reports"])


def _build_report_query(
    createdFrom=None, createdTo=None,
    finishedFrom=None, finishedTo=None,
    status_filter=None, executorId=None,
    department_id=None,
):
    base = """
        SELECT
            a.application_id,
            a.name,
            a.created_at,
            a.work_at,
            a.finished_at,
            s.name  AS status_name,
            p.name  AS priority_name,
            e.fio   AS executor_name,
            exec_link.employee_id AS executor_id,
            d.name  AS department_name,
            tw.name AS work_type_name
        FROM public.application a
        LEFT JOIN public.status   s  ON s.status_id   = a.status_id
        LEFT JOIN public.priority p  ON p.priority_id = a.priority_id
        LEFT JOIN public.department d ON d.department_id = a.department_id
        LEFT JOIN public.types_of_works tw ON tw.type_of_works_id = a.types_of_works
        LEFT JOIN public.employee_to_application exec_link
               ON exec_link.application_id = a.application_id
              AND exec_link.role_id = (SELECT role_id FROM public.role WHERE name = 'executor' LIMIT 1)
        LEFT JOIN public.employee e ON e.employee_id = exec_link.employee_id
        WHERE 1=1
    """
    params = []
    if createdFrom:
        base += " AND a.created_at >= %s"; params.append(createdFrom)
    if createdTo:
        base += " AND a.created_at <= %s"; params.append(createdTo)
    if finishedFrom:
        base += " AND a.finished_at >= %s"; params.append(finishedFrom)
    if finishedTo:
        base += " AND a.finished_at <= %s"; params.append(finishedTo)
    if status_filter:
        base += " AND s.name = %s"; params.append(status_filter)
    if executorId:
        base += " AND exec_link.employee_id = %s"; params.append(int(executorId))
    # Department scope: a regular manager only sees their own department's
    # applications (top-manager passes None → no restriction).
    if department_id is not None:
        base += " AND a.department_id = %s"; params.append(int(department_id))
    base += " ORDER BY a.created_at DESC"
    return base, params


def _fetch_report_rows(userData, createdFrom, createdTo, finishedFrom, finishedTo,
                       status_filter, executorId):
    """Общая часть обоих эндпоинтов: права, scope по отделу, выборка строк отчёта."""
    require_permission(userData, "canViewReports")
    db = get_db_user(userData)
    login = userData[0]
    # A regular manager only reports on their own department; top-manager on all.
    report_dept = None if _is_top_manager(login) else _user_department_id(db, login)

    query, params = _build_report_query(
        createdFrom, createdTo, finishedFrom, finishedTo, status_filter, executorId,
        department_id=report_dept,
    )

    with db.pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return cur.execute(query, params).fetchall()


def _build_export_file(rows) -> tuple[bytes, str, str]:
    """Файл отчёта → (содержимое, media_type, имя файла).

    openpyxl генерирует современный **xlsx** — отдаём его с честным MIME-типом и
    расширением (раньше файл назывался .xls с типом vnd.ms-excel, и Excel ругался
    на несоответствие формата). CSV-фоллбек, если openpyxl не установлен."""
    def _s(v):
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v) if v is not None else ""

    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Applications"
        ws.append([
            "ID", "Название", "Статус", "Приоритет",
            "Создана", "Начата", "Завершена",
            "Исполнитель", "Отдел", "Вид работ",
        ])
        for r in rows:
            ws.append([
                _s(r.get("application_id")),
                _s(r.get("name")),
                _s(r.get("status_name")),
                _s(r.get("priority_name")),
                _s(r.get("created_at")),
                _s(r.get("work_at")),
                _s(r.get("finished_at")),
                _s(r.get("executor_name")),
                _s(r.get("department_name")),
                _s(r.get("work_type_name")),
            ])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return (buf.read(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "applications.xlsx")
    except ImportError:
        # Fallback: CSV as plain text if openpyxl not installed
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["ID", "Name", "Status", "Priority",
                         "Created", "Started", "Finished",
                         "Executor", "Department", "WorkType"])
        for r in rows:
            writer.writerow([
                r.get("application_id"), r.get("name"),
                r.get("status_name"), r.get("priority_name"),
                r.get("created_at"), r.get("work_at"), r.get("finished_at"),
                r.get("executor_name"), r.get("department_name"), r.get("work_type_name"),
            ])
        return (out.getvalue().encode("utf-8-sig"),
                "text/csv; charset=utf-8",
                "applications.csv")


@router.get("/reports/applications", summary="Сформировать предварительный отчет по заявкам",
            description="Возвращает JSON-данные для предпросмотра отчета. Фильтры передаются query-параметрами, потому что формирование отчета не меняет состояние backend.",
            response_model=ApplicationReportResponse)
def report_applications(
    userData=Depends(authObj.authenticate),
    createdFrom:  Optional[str] = Query(default=None),
    createdTo:    Optional[str] = Query(default=None),
    finishedFrom: Optional[str] = Query(default=None),
    finishedTo:   Optional[str] = Query(default=None),
    status_filter:Optional[str] = Query(default=None, alias="status"),
    executorId:   Optional[str] = Query(default=None),
):
    try:
        rows = _fetch_report_rows(userData, createdFrom, createdTo,
                                  finishedFrom, finishedTo, status_filter, executorId)

        items = [ApplicationReportRowOut.model_validate(r).model_dump() for r in rows]
        total     = len(items)
        completed = sum(1 for i in items if i.get("status") == "completed")
        in_prog   = sum(1 for i in items if i.get("status") in ("inProgress", "assigned"))

        return {
            "items": items,
            "summary": {
                "total": total,
                "completed": completed,
                "inProgressOrAssigned": in_prog,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)


@router.get("/reports/applications.xls", summary="Скачать Excel-отчет по заявкам",
            description="Возвращает готовый Excel-файл (xlsx) по тем же фильтрам, что и предпросмотр отчета. "
                        "Генерация файла выполняется на backend; имя и тип файла — в заголовках ответа "
                        "(Content-Disposition: applications.xlsx). Путь сохранён как .xls для совместимости.")
def report_applications_xls(
    userData=Depends(authObj.authenticate),
    createdFrom:  Optional[str] = Query(default=None),
    createdTo:    Optional[str] = Query(default=None),
    finishedFrom: Optional[str] = Query(default=None),
    finishedTo:   Optional[str] = Query(default=None),
    status_filter:Optional[str] = Query(default=None, alias="status"),
    executorId:   Optional[str] = Query(default=None),
):
    try:
        rows = _fetch_report_rows(userData, createdFrom, createdTo,
                                  finishedFrom, finishedTo, status_filter, executorId)

        content, media_type, filename = _build_export_file(rows)
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    except HTTPException:
        raise
    except Exception as e:
        _raise_for_db_error(e)
