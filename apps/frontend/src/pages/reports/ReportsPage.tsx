import { FormEvent, useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { useReferenceData } from "@app/providers/ReferenceDataProvider";
import { apiClient, type ApplicationReportResponseDto } from "@shared/api";
import type { ApplicationStatus } from "@shared/model/domain";
import { priorityLabels, statusLabels } from "@shared/model/labels";
import { Button } from "@shared/ui";

import "./ReportsPage.css";

type ReportFilters = {
  createdFrom: string;
  createdTo: string;
  finishedFrom: string;
  finishedTo: string;
  status: "all" | ApplicationStatus;
  executorId: "all" | string;
};

const initialFilters: ReportFilters = {
  createdFrom: "2026-05-19",
  createdTo: "2026-06-02",
  finishedFrom: "",
  finishedTo: "",
  status: "all",
  executorId: "all",
};

export function ReportsPage() {
  const { credentials, currentUser } = useAuth();
  const { employees } = useReferenceData();
  const [filters, setFilters] = useState<ReportFilters>(initialFilters);
  const [errors, setErrors] = useState<Partial<Record<keyof ReportFilters, string>>>({});
  const [report, setReport] = useState<ApplicationReportResponseDto | null>(null);
  const [notice, setNotice] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const executors = useMemo(
    () =>
      employees.filter(
        (user) =>
          user.role === "executor" &&
          (currentUser?.role !== "manager" || user.departmentId === currentUser.departmentId),
      ),
    [currentUser, employees],
  );

  const updateFilter = <Key extends keyof ReportFilters>(key: Key, value: ReportFilters[Key]) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
    setReport(null);
    setNotice("");
  };

  const validate = () => {
    const nextErrors: Partial<Record<keyof ReportFilters, string>> = {};

    if (!filters.createdFrom) {
      nextErrors.createdFrom = "Укажите начало периода создания.";
    }

    if (!filters.createdTo) {
      nextErrors.createdTo = "Укажите конец периода создания.";
    }

    if (filters.createdFrom && filters.createdTo && filters.createdFrom > filters.createdTo) {
      nextErrors.createdTo = "Конец периода создания не может быть раньше начала.";
    }

    if (filters.finishedFrom && filters.finishedTo && filters.finishedFrom > filters.finishedTo) {
      nextErrors.finishedTo = "Конец периода завершения не может быть раньше начала.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const getQuery = () => ({
    createdFrom: filters.createdFrom,
    createdTo: filters.createdTo,
    finishedFrom: filters.finishedFrom,
    finishedTo: filters.finishedTo,
    status: filters.status === "all" ? undefined : filters.status,
    executorId: filters.executorId === "all" ? undefined : filters.executorId,
  });

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate() || !credentials) {
      return;
    }

    setIsLoading(true);

    try {
      setReport(await apiClient.getApplicationReport(credentials, getQuery()));
      setNotice("Предварительный отчет сформирован.");
    } catch {
      setNotice("Backend не сформировал отчет.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleExport = async () => {
    if (!validate() || !credentials) {
      return;
    }

    try {
      const response = await fetch(apiClient.getApplicationReportXlsUrl(getQuery()), {
        headers: {
          Authorization: `Basic ${window.btoa(`${credentials.login}:${credentials.password}`)}`,
        },
      });

      if (!response.ok) {
        throw new Error("export failed");
      }

      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "applications-report.xls";
      link.click();
      URL.revokeObjectURL(url);
      setNotice("XLS-отчет скачан.");
    } catch {
      setNotice("Backend не выгрузил XLS.");
    }
  };

  const rows = report?.items ?? [];

  return (
    <section className="reports-page">
      <header className="reports-page__header">
        <div>
          <h1>Отчетность</h1>
          <p>Формирование отчета по выполнению заявок подчиненными.</p>
        </div>
      </header>

      {notice ? <div className="reports-notice">{notice}</div> : null}

      <form className="reports-filters" onSubmit={handleSubmit} noValidate>
        <div className="reports-filters__grid">
          <label>
            Созданы с
            <input type="date" value={filters.createdFrom} onChange={(event) => updateFilter("createdFrom", event.target.value)} />
            {errors.createdFrom ? <small>{errors.createdFrom}</small> : null}
          </label>
          <label>
            Созданы по
            <input type="date" value={filters.createdTo} onChange={(event) => updateFilter("createdTo", event.target.value)} />
            {errors.createdTo ? <small>{errors.createdTo}</small> : null}
          </label>
          <label>
            Завершены с
            <input type="date" value={filters.finishedFrom} onChange={(event) => updateFilter("finishedFrom", event.target.value)} />
            {errors.finishedFrom ? <small>{errors.finishedFrom}</small> : null}
          </label>
          <label>
            Завершены по
            <input type="date" value={filters.finishedTo} onChange={(event) => updateFilter("finishedTo", event.target.value)} />
            {errors.finishedTo ? <small>{errors.finishedTo}</small> : null}
          </label>
          <label>
            Статус
            <select value={filters.status} onChange={(event) => updateFilter("status", event.target.value as ReportFilters["status"])}>
              <option value="all">Все статусы</option>
              {Object.entries(statusLabels).map(([value, label]) => (
                <option value={value} key={value}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            Исполнитель
            <select value={filters.executorId} onChange={(event) => updateFilter("executorId", event.target.value)}>
              <option value="all">Все исполнители</option>
              {executors.map((executor) => (
                <option value={executor.id} key={executor.id}>{executor.fullName}</option>
              ))}
            </select>
          </label>
        </div>
        <footer>
          <Button type="submit" disabled={isLoading}>{isLoading ? "Формируем" : "Сформировать"}</Button>
        </footer>
      </form>

      <section className="reports-summary" aria-label="Сводка отчета">
        <div>
          <span>Строк в отчете</span>
          <strong>{report?.summary.total ?? 0}</strong>
        </div>
        <div>
          <span>Завершены</span>
          <strong>{report?.summary.completed ?? 0}</strong>
        </div>
        <div>
          <span>В работе или назначены</span>
          <strong>{report?.summary.inProgressOrAssigned ?? 0}</strong>
        </div>
      </section>

      <article className="reports-table">
        <header>
          <div>
            <h2>Предварительный просмотр</h2>
            <span>{report ? "Актуален" : "Сформируйте отчет"}</span>
          </div>
          <Button type="button" variant="secondary" onClick={() => void handleExport()} disabled={!report}>
            Выгрузить .xls
          </Button>
        </header>

        <div className="reports-table__grid" role="table" aria-label="Предварительный отчет">
          <div className="reports-table__row reports-table__row--head" role="row">
            <span role="columnheader">Заявка</span>
            <span role="columnheader">Статус</span>
            <span role="columnheader">Приоритет</span>
            <span role="columnheader">Исполнитель</span>
            <span role="columnheader">Вид работ</span>
            <span role="columnheader">Создана</span>
            <span role="columnheader">В работу</span>
            <span role="columnheader">Закрыта</span>
          </div>

          {rows.length > 0 ? (
            rows.map((row) => (
              <div className="reports-table__row" role="row" key={row.applicationId}>
                <span role="cell">
                  <strong>ID {row.applicationId}</strong>
                  <small>{row.departmentName ?? "-"}</small>
                </span>
                <span role="cell">{statusLabels[row.status]}</span>
                <span role="cell">{priorityLabels[row.priority]}</span>
                <span role="cell">{row.executorName ?? "-"}</span>
                <span role="cell">{row.workTypeName ?? "-"}</span>
                <span role="cell">{formatDateTime(row.createdAt)}</span>
                <span role="cell">{formatDateTime(row.startedAt ?? undefined)}</span>
                <span role="cell">{formatDateTime(row.finishedAt ?? undefined)}</span>
              </div>
            ))
          ) : (
            <div className="reports-table__empty">По выбранным фильтрам нет заявок.</div>
          )}
        </div>
      </article>
    </section>
  );
}

function formatDateTime(value?: string) {
  if (!value) {
    return "-";
  }

  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
