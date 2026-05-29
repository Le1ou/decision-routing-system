import { FormEvent, useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { departments, mockUsers, applications, workTypes } from "@mocks/mockData";
import type { ApplicationStatus } from "@shared/model/domain";
import { priorityLabels, statusLabels } from "@shared/model/labels";
import { filterApplicationsByRole } from "@shared/model/applicationRules";
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
  createdTo: "2026-05-26",
  finishedFrom: "",
  finishedTo: "",
  status: "all",
  executorId: "all",
};

export function ReportsPage() {
  const { currentUser } = useAuth();
  const [filters, setFilters] = useState<ReportFilters>(initialFilters);
  const [errors, setErrors] = useState<Partial<Record<keyof ReportFilters, string>>>({});
  const [isReportReady, setIsReportReady] = useState(true);
  const [notice, setNotice] = useState("");

  const visibleApplications = useMemo(
    () => (currentUser ? filterApplicationsByRole(applications, currentUser) : []),
    [currentUser],
  );
  const executors = useMemo(
    () =>
      mockUsers.filter(
        (user) =>
          user.role === "executor" &&
          (currentUser?.role !== "manager" || user.departmentId === currentUser.departmentId),
      ),
    [currentUser],
  );
  const reportRows = useMemo(
    () =>
      visibleApplications.filter((application) => {
        const createdAt = toDateOnly(application.createdAt);
        const finishedAt = application.finishedAt ? toDateOnly(application.finishedAt) : "";
        const matchesCreatedFrom = !filters.createdFrom || createdAt >= filters.createdFrom;
        const matchesCreatedTo = !filters.createdTo || createdAt <= filters.createdTo;
        const matchesFinishedFrom = !filters.finishedFrom || (finishedAt && finishedAt >= filters.finishedFrom);
        const matchesFinishedTo = !filters.finishedTo || (finishedAt && finishedAt <= filters.finishedTo);
        const matchesStatus = filters.status === "all" || application.status === filters.status;
        const matchesExecutor = filters.executorId === "all" || application.executorId === filters.executorId;

        return matchesCreatedFrom && matchesCreatedTo && matchesFinishedFrom && matchesFinishedTo && matchesStatus && matchesExecutor;
      }),
    [filters, visibleApplications],
  );

  const completedCount = reportRows.filter((application) => application.status === "completed").length;
  const inProgressCount = reportRows.filter((application) => application.status === "inProgress" || application.status === "assigned").length;

  const updateFilter = <Key extends keyof ReportFilters>(key: Key, value: ReportFilters[Key]) => {
    setFilters((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
    setIsReportReady(false);
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

    if (filters.finishedFrom && filters.createdFrom && filters.finishedFrom < filters.createdFrom) {
      nextErrors.finishedFrom = "Период завершения не должен начинаться раньше периода создания.";
    }

    if (filters.finishedFrom && filters.finishedTo && filters.finishedFrom > filters.finishedTo) {
      nextErrors.finishedTo = "Конец периода завершения не может быть раньше начала.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate()) {
      return;
    }

    setIsReportReady(true);
    setNotice("Предварительный отчет сформирован на mock-данных.");
  };

  const handleExport = () => {
    if (!validate()) {
      return;
    }

    if (!isReportReady) {
      setNotice("Сначала сформируйте отчет с текущими фильтрами.");
      return;
    }

    setNotice("Выгрузка .xls показана как UI-действие. Реальный файл подключим после согласования с backend.");
  };

  return (
    <section className="reports-page">
      <header className="reports-page__header">
        <div>
          <h1>Отчетность</h1>
          <p>Формирование отчета по выполнению заявок подчиненными на mock-данных.</p>
        </div>
      </header>

      {notice ? <div className="reports-notice">{notice}</div> : null}

      <form className="reports-filters" onSubmit={handleSubmit} noValidate>
        <div className="reports-filters__grid">
          <label>
            Созданы с
            <input
              type="date"
              value={filters.createdFrom}
              onChange={(event) => updateFilter("createdFrom", event.target.value)}
            />
            {errors.createdFrom ? <small>{errors.createdFrom}</small> : null}
          </label>
          <label>
            Созданы по
            <input
              type="date"
              value={filters.createdTo}
              onChange={(event) => updateFilter("createdTo", event.target.value)}
            />
            {errors.createdTo ? <small>{errors.createdTo}</small> : null}
          </label>
          <label>
            Завершены с
            <input
              type="date"
              value={filters.finishedFrom}
              onChange={(event) => updateFilter("finishedFrom", event.target.value)}
            />
            {errors.finishedFrom ? <small>{errors.finishedFrom}</small> : null}
          </label>
          <label>
            Завершены по
            <input
              type="date"
              value={filters.finishedTo}
              onChange={(event) => updateFilter("finishedTo", event.target.value)}
            />
            {errors.finishedTo ? <small>{errors.finishedTo}</small> : null}
          </label>
          <label>
            Статус
            <select value={filters.status} onChange={(event) => updateFilter("status", event.target.value as ReportFilters["status"])}>
              <option value="all">Все статусы</option>
              {Object.entries(statusLabels).map(([value, label]) => (
                <option value={value} key={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Исполнитель
            <select value={filters.executorId} onChange={(event) => updateFilter("executorId", event.target.value)}>
              <option value="all">Все исполнители</option>
              {executors.map((executor) => (
                <option value={executor.id} key={executor.id}>
                  {executor.fullName}
                </option>
              ))}
            </select>
          </label>
        </div>
        <footer>
          <Button type="submit">Сформировать</Button>
        </footer>
      </form>

      <section className="reports-summary" aria-label="Сводка отчета">
        <div>
          <span>Строк в отчете</span>
          <strong>{reportRows.length}</strong>
        </div>
        <div>
          <span>Завершены</span>
          <strong>{completedCount}</strong>
        </div>
        <div>
          <span>В работе или назначены</span>
          <strong>{inProgressCount}</strong>
        </div>
      </section>

      <article className="reports-table">
        <header>
          <div>
            <h2>Предварительный просмотр</h2>
            <span>{isReportReady ? "Актуален" : "Измените фильтры и сформируйте отчет"}</span>
          </div>
          <Button type="button" variant="secondary" onClick={handleExport} disabled={!isReportReady}>
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

          {reportRows.length > 0 ? (
            reportRows.map((application) => {
              const executor = mockUsers.find((user) => user.id === application.executorId);
              const workType = workTypes.find((item) => item.id === application.workTypeId);
              const department = departments.find((item) => item.id === application.departmentId);

              return (
                <div className="reports-table__row" role="row" key={application.id}>
                  <span role="cell">
                    <strong>ID {application.id}</strong>
                    <small>{department?.name ?? "-"}</small>
                  </span>
                  <span role="cell">{statusLabels[application.status]}</span>
                  <span role="cell">{priorityLabels[application.priority]}</span>
                  <span role="cell">{executor?.fullName ?? "-"}</span>
                  <span role="cell">{workType?.name ?? "-"}</span>
                  <span role="cell">{formatDateTime(application.createdAt)}</span>
                  <span role="cell">{formatDateTime(application.startedAt)}</span>
                  <span role="cell">{formatDateTime(application.finishedAt)}</span>
                </div>
              );
            })
          ) : (
            <div className="reports-table__empty">По выбранным фильтрам нет заявок.</div>
          )}
        </div>
      </article>
    </section>
  );
}

function toDateOnly(value: string) {
  return value.slice(0, 10);
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
