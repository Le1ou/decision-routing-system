import { useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { departments, positions, requests, workTypes, mockUsers } from "@mocks/mockData";
import { actionLabels, priorityLabels, statusLabels } from "@shared/model/labels";
import {
  applyRequestFilters,
  filterRequestsByRole,
  getAvailableRequestActions,
  sortRequests,
  type RequestFilter,
  type RequestSortKey,
} from "@shared/model/requestRules";

import "./RequestsPage.css";

export function RequestsPage() {
  const { currentUser } = useAuth();
  const [sortKey, setSortKey] = useState<RequestSortKey>("priority");
  const [filters, setFilters] = useState<RequestFilter>({});

  const visibleRequests = useMemo(() => {
    if (!currentUser) {
      return [];
    }

    const getExecutorName = (executorId?: string) => mockUsers.find((user) => user.id === executorId)?.fullName ?? "";

    return sortRequests(
      applyRequestFilters(filterRequestsByRole(requests, currentUser), filters, currentUser, { getExecutorName }),
      sortKey,
    );
  }, [currentUser, filters, sortKey]);

  const selectedRequest = visibleRequests[0];
  const requestActions = currentUser && selectedRequest ? getAvailableRequestActions(selectedRequest, currentUser) : [];
  const requestDepartment = departments.find((department) => department.id === selectedRequest?.departmentId);
  const requestWorkType = workTypes.find((workType) => workType.id === selectedRequest?.workTypeId);
  const author = mockUsers.find((user) => user.id === selectedRequest?.authorId);
  const executor = mockUsers.find((user) => user.id === selectedRequest?.executorId);
  const previousExecutor = mockUsers.find((user) => user.id === selectedRequest?.previousExecutorId);
  const authorDepartment = departments.find((department) => department.id === author?.departmentId);
  const authorPosition = positions.find((position) => position.id === author?.positionId);
  const executorDepartment = departments.find((department) => department.id === executor?.departmentId);
  const executorPosition = positions.find((position) => position.id === executor?.positionId);

  if (!currentUser) {
    return (
      <section className="requests-page requests-page--empty">
        <div className="requests-empty">Для текущей роли нет доступных заявок.</div>
      </section>
    );
  }

  return (
    <section className="requests-page">
      <aside className="requests-sidebar">
        <div className="requests-toolbar">
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value as RequestSortKey)} aria-label="Сортировка">
            <option value="priority">Сортировать по приоритету</option>
            <option value="status">Сортировать по статусу</option>
            <option value="createdAt">Сортировать по дате создания</option>
            <option value="finishedAt">Сортировать по дате закрытия</option>
          </select>
          <button type="button" aria-label="Направление сортировки">↕</button>
        </div>

        <div className="requests-filters" aria-label="Фильтры заявок">
          <input
            value={filters.requestNumberQuery ?? ""}
            onChange={(event) => setFilters((current) => ({ ...current, requestNumberQuery: event.target.value }))}
            placeholder="Номер заявки"
            aria-label="Поиск по номеру заявки"
          />
          <input
            value={filters.executorQuery ?? ""}
            onChange={(event) => setFilters((current) => ({ ...current, executorQuery: event.target.value }))}
            placeholder="ФИО исполнителя"
            aria-label="Поиск по ФИО исполнителя"
          />
          {currentUser.role !== "author" ? (
            <label>
              <input
                type="checkbox"
                checked={filters.createdByMe ?? false}
                onChange={(event) => setFilters((current) => ({ ...current, createdByMe: event.target.checked }))}
              />
              Созданные мной
            </label>
          ) : null}
          {currentUser.role === "executor" ? (
            <label>
              <input
                type="checkbox"
                checked={filters.assignedToMe ?? false}
                onChange={(event) => setFilters((current) => ({ ...current, assignedToMe: event.target.checked }))}
              />
              Назначенные на меня
            </label>
          ) : null}
          {currentUser.role === "manager" ? (
            <label>
              <input
                type="checkbox"
                checked={filters.delegatedFromAnotherDepartment ?? false}
                onChange={(event) =>
                  setFilters((current) => ({ ...current, delegatedFromAnotherDepartment: event.target.checked }))
                }
              />
              Делегированы из другого отдела
            </label>
          ) : null}
        </div>

        <div className="requests-list">
          {visibleRequests.length > 0 ? (
            visibleRequests.map((request) => (
              <article className="request-row" key={request.id}>
                <strong>Заявка № {request.number.replace("DRS-", "")}</strong>
                <span>{request.title}</span>
              </article>
            ))
          ) : (
            <div className="requests-list__empty">Заявки не найдены</div>
          )}
        </div>
      </aside>

      {selectedRequest ? (
      <article className="request-card">
        <button className="request-card__edit" type="button" aria-label="Редактировать">✎</button>

        <header className="request-card__title">
          <h1>{requestDepartment?.name ?? "Отдел не указан"} / Заявка № {selectedRequest.number.replace("DRS-", "")}</h1>
          <input value={selectedRequest.title} readOnly aria-label="Тема заявки" />
        </header>

        <div className="request-card__main">
          <section className="request-card__workarea">
            <div className="request-card__section-header">
              <strong>Описание</strong>
              <span>Предыдущий исполнитель: {previousExecutor?.fullName ?? "не назначен"}</span>
            </div>
            <textarea
              value={selectedRequest.description}
              readOnly
              aria-label="Описание заявки"
            />
            <label className="request-card__comment">
              <span>Комментарий исполнителя:</span>
              <textarea
                value={selectedRequest.executorComment ?? selectedRequest.resultText ?? ""}
                placeholder="Комментарий появится после назначения или выполнения работ"
                readOnly
                aria-label="Комментарий исполнителя"
              />
            </label>
            <div className="request-card__actions">
              {requestActions.map((action) => (
                <button type="button" key={action}>{actionLabels[action]}</button>
              ))}
            </div>
          </section>

          <aside className="request-card__info">
            <div className="request-card__params">
              <label>
                Статус:
                <select value={selectedRequest.status} disabled>
                  <option value={selectedRequest.status}>{statusLabels[selectedRequest.status]}</option>
                </select>
              </label>
              <label>
                Приоритет:
                <input value={priorityLabels[selectedRequest.priority]} readOnly />
              </label>
              <label>
                Вид работ:
                <input value={requestWorkType?.name ?? "-"} readOnly />
              </label>
            </div>

            <section className="request-info-box">
              <h2>Автор заявки</h2>
              <p><b>ФИО:</b> {author?.fullName ?? "-"}</p>
              <p><b>Отдел:</b> {authorDepartment?.name ?? "-"}</p>
              <p><b>Должность:</b> {authorPosition?.name ?? "-"}</p>
            </section>

            <section className="request-info-box">
              <h2>Исполнитель</h2>
              <p><b>ФИО:</b> {executor?.fullName ?? "-"}</p>
              <p><b>Отдел:</b> {executorDepartment?.name ?? "-"}</p>
              <p><b>Должность:</b> {executorPosition?.name ?? "-"}</p>
            </section>

            <section className="request-info-box request-info-box--dates">
              <h2>Информация о заявке</h2>
              <p><b>Дата и время последнего изменения:</b> {formatDateTime(selectedRequest.updatedAt)}</p>
              <p><b>Дата и время создания заявки:</b> {formatDateTime(selectedRequest.createdAt)}</p>
              <p><b>Дата и время назначения исполнителя:</b> {formatDateTime(selectedRequest.assignedAt)}</p>
              <p><b>Дата и время взятия в работу заявки:</b> {formatDateTime(selectedRequest.startedAt)}</p>
              <p><b>Дата и время закрытия заявки:</b> {formatDateTime(selectedRequest.finishedAt)}</p>
            </section>
          </aside>
        </div>
      </article>
      ) : (
        <div className="request-card request-card--empty">
          <div className="requests-empty">Заявки не найдены. Измените параметры фильтрации.</div>
        </div>
      )}
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
