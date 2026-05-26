import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
import { useRequestsStore } from "@app/providers/RequestsProvider";
import { attachments, departments, positions, workTypes, mockUsers } from "@mocks/mockData";
import type { Complexity, Request, RequestAction, RequestStatus } from "@shared/model/domain";
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
  const { requestItems, updateRequest } = useRequestsStore();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sortKey, setSortKey] = useState<RequestSortKey>("priority");
  const [filters, setFilters] = useState<RequestFilter>({});
  const [selectedRequestId, setSelectedRequestId] = useState<string | null>(null);
  const [isSidebarHidden, setIsSidebarHidden] = useState(false);
  const [notice, setNotice] = useState("");
  const [pendingAction, setPendingAction] = useState<RequestAction | null>(null);
  const [actionForm, setActionForm] = useState({
    comment: "",
    complexity: "medium",
    departmentId: "it",
    description: "",
    executorId: "",
  });

  const visibleRequests = useMemo(() => {
    if (!currentUser) {
      return [];
    }

    const getExecutorName = (executorId?: string) => mockUsers.find((user) => user.id === executorId)?.fullName ?? "";

    return sortRequests(
      applyRequestFilters(filterRequestsByRole(requestItems, currentUser), filters, currentUser, { getExecutorName }),
      sortKey,
    );
  }, [currentUser, filters, requestItems, sortKey]);

  useEffect(() => {
    const requestIdFromUrl = searchParams.get("request");

    if (visibleRequests.length === 0) {
      setSelectedRequestId(null);
      return;
    }

    if (requestIdFromUrl && visibleRequests.some((request) => request.id === requestIdFromUrl)) {
      setSelectedRequestId(requestIdFromUrl);
      return;
    }

    if (!selectedRequestId || !visibleRequests.some((request) => request.id === selectedRequestId)) {
      setSelectedRequestId(visibleRequests[0].id);
    }
  }, [searchParams, selectedRequestId, visibleRequests]);

  const selectedRequest = visibleRequests.find((request) => request.id === selectedRequestId) ?? visibleRequests[0];
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
  const requestAttachmentNames = [
    ...attachments.filter((attachment) => attachment.requestId === selectedRequest?.id).map((attachment) => attachment.name),
    ...(selectedRequest?.attachmentNames ?? []),
  ];

  const executorsForDepartment = mockUsers.filter(
    (user) => user.role === "executor" && user.departmentId === selectedRequest?.departmentId && user.isActive,
  );
  const actionsWithForm: RequestAction[] = [
    "assignExecutor",
    "delegateInternal",
    "delegateExternal",
    "editDescription",
    "returnToNew",
  ];

  const handleRequestAction = (action: RequestAction) => {
    if (!selectedRequest || !currentUser) {
      return;
    }

    if (actionsWithForm.includes(action)) {
      setPendingAction(action);
      setActionForm({
        comment: "",
        complexity: selectedRequest.assignedComplexity ?? requestWorkType?.complexity ?? "medium",
        departmentId: selectedRequest.departmentId,
        description: selectedRequest.description,
        executorId: selectedRequest.executorId ?? executorsForDepartment[0]?.id ?? "",
      });
      return;
    }

    applyAction(action);
  };

  const applyAction = (action: RequestAction, payload: MockActionPayload = {}) => {
    if (!selectedRequest || !currentUser) {
      return;
    }

    const updatedAt = new Date().toISOString();

    updateRequest(selectedRequest.id, (request) => applyMockAction(request, action, currentUser.id, updatedAt, payload));

    setNotice(getMockActionNotice(action));
    setPendingAction(null);
  };

  if (!currentUser) {
    return (
      <section className="requests-page requests-page--empty">
        <div className="requests-empty">Для текущей роли нет доступных заявок.</div>
      </section>
    );
  }

  return (
    <section className={isSidebarHidden ? "requests-page requests-page--sidebar-hidden" : "requests-page"}>
      <aside className="requests-sidebar" aria-hidden={isSidebarHidden}>
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
              <button
                className={request.id === selectedRequest?.id ? "request-row request-row--active" : "request-row"}
                type="button"
                key={request.id}
                onClick={() => {
                  setSelectedRequestId(request.id);
                  setSearchParams({ request: request.id });
                }}
              >
                <strong>Заявка № {request.number.replace("DRS-", "")}</strong>
                <span>{request.title}</span>
              </button>
            ))
          ) : (
            <div className="requests-list__empty">Заявки не найдены</div>
          )}
        </div>
      </aside>

      {selectedRequest ? (
      <article className="request-card">
        {notice ? <div className="request-card__notice">{notice}</div> : null}
        <button
          className="request-card__toggle-list"
          type="button"
          onClick={() => setIsSidebarHidden((value) => !value)}
        >
          {isSidebarHidden ? "Показать список" : "Скрыть список"}
        </button>
        <button
          className="request-card__edit"
          type="button"
          aria-label="Редактировать"
          disabled={!requestActions.includes("editDescription")}
          onClick={() => handleRequestAction("editDescription")}
        >
          ✎
        </button>

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
                <button type="button" key={action} onClick={() => handleRequestAction(action)}>
                  {actionLabels[action]}
                </button>
              ))}
            </div>
            <section className="request-card__attachments">
              <h2>Вложения</h2>
              {requestAttachmentNames.length > 0 ? (
                <ul>
                  {requestAttachmentNames.map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
              ) : (
                <p>Файлы не прикреплены</p>
              )}
            </section>
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
      {pendingAction && selectedRequest ? (
        <div className="request-modal" role="dialog" aria-modal="true" aria-label={actionLabels[pendingAction]}>
          <form
            className="request-modal__panel"
            onSubmit={(event) => {
              event.preventDefault();
              applyAction(pendingAction, actionForm);
            }}
          >
            <header>
              <h2>{actionLabels[pendingAction]}</h2>
              <button type="button" onClick={() => setPendingAction(null)} aria-label="Закрыть">×</button>
            </header>

            {pendingAction === "assignExecutor" ? (
              <label>
                Исполнитель
                <select
                  value={actionForm.executorId}
                  onChange={(event) => setActionForm((current) => ({ ...current, executorId: event.target.value }))}
                >
                  {executorsForDepartment.map((user) => (
                    <option value={user.id} key={user.id}>{user.fullName}</option>
                  ))}
                </select>
              </label>
            ) : null}

            {pendingAction === "delegateExternal" ? (
              <label>
                Новый отдел
                <select
                  value={actionForm.departmentId}
                  onChange={(event) => setActionForm((current) => ({ ...current, departmentId: event.target.value }))}
                >
                  {departments
                    .filter((department) => department.id !== selectedRequest.departmentId)
                    .map((department) => (
                      <option value={department.id} key={department.id}>{department.name}</option>
                    ))}
                </select>
              </label>
            ) : null}

            {pendingAction === "delegateInternal" || pendingAction === "returnToNew" ? (
              <label>
                Сложность
                <select
                  value={actionForm.complexity}
                  onChange={(event) => setActionForm((current) => ({ ...current, complexity: event.target.value }))}
                >
                  <option value="easy">Легкая</option>
                  <option value="medium">Средняя</option>
                  <option value="hard">Высокая</option>
                  <option value="critical">Критичная</option>
                </select>
              </label>
            ) : null}

            {pendingAction === "editDescription" ? (
              <label>
                Описание проблемы
                <textarea
                  value={actionForm.description}
                  onChange={(event) => setActionForm((current) => ({ ...current, description: event.target.value }))}
                  maxLength={1000}
                  placeholder="Уточните описание проблемы"
                />
              </label>
            ) : null}

            {pendingAction !== "editDescription" ? (
            <label>
              Комментарий
              <textarea
                value={actionForm.comment}
                onChange={(event) => setActionForm((current) => ({ ...current, comment: event.target.value }))}
                placeholder="Добавьте пояснение для истории заявки"
              />
            </label>
            ) : null}

            <footer>
              <button type="button" onClick={() => setPendingAction(null)}>Отмена</button>
              <button type="submit">Подтвердить</button>
            </footer>
          </form>
        </div>
      ) : null}
    </section>
  );
}

type MockActionPayload = {
  comment?: string;
  complexity?: string;
  description?: string;
  departmentId?: string;
  executorId?: string;
};

function applyMockAction(
  request: Request,
  action: RequestAction,
  userId: string,
  updatedAt: string,
  payload: MockActionPayload = {},
): Request {
  const statusByAction: Partial<Record<RequestAction, RequestStatus>> = {
    startWork: "inProgress",
    reject: "rejected",
    complete: "completed",
    delegateInternal: "new",
    delegateExternal: "delegated",
    returnToNew: "new",
    confirmExternalDelegation: "new",
    declineExternalDelegation: "new",
  };

  if (action === "assignExecutor") {
    const executor =
      mockUsers.find((user) => user.id === payload.executorId) ??
      mockUsers.find((user) => user.role === "executor" && user.departmentId === request.departmentId && user.isActive);

    return {
      ...request,
      status: "assigned",
      executorId: executor?.id ?? request.executorId,
      assignedAt: updatedAt,
      updatedAt,
      managerComment: payload.comment || "Исполнитель назначен вручную в mock-режиме.",
    };
  }

  if (action === "editDescription") {
    return {
      ...request,
      updatedAt,
      description: payload.description?.trim() || request.description,
    };
  }

  if (action === "complete") {
    return {
      ...request,
      status: "completed",
      updatedAt,
      finishedAt: updatedAt,
      closedById: userId,
      resultText: request.resultText ?? "Работы выполнены в mock-режиме.",
    };
  }

  if (action === "startWork") {
    return {
      ...request,
      status: "inProgress",
      startedAt: updatedAt,
      updatedAt,
    };
  }

  if (action === "delegateInternal" || action === "returnToNew") {
    return {
      ...request,
      status: "new",
      previousExecutorId: request.executorId ?? request.previousExecutorId,
      executorId: undefined,
      isUnfinished: true,
      updatedAt,
      assignedComplexity: payload.complexity as Complexity | undefined,
      executorComment: payload.comment || "Заявка возвращена для переназначения в mock-режиме.",
    };
  }

  if (action === "delegateExternal") {
    return {
      ...request,
      status: "delegated",
      delegationId: request.delegationId ?? `delegation-${request.id}`,
      departmentId: payload.departmentId ?? request.departmentId,
      updatedAt,
      executorComment: payload.comment || "Запрошено делегирование в другой отдел.",
    };
  }

  return {
    ...request,
    status: statusByAction[action] ?? request.status,
    updatedAt,
  };
}

function getMockActionNotice(action: RequestAction) {
  if (action === "assignExecutor") {
    return "Исполнитель назначен. Заявка перешла в статус «Назначен исполнитель».";
  }

  if (action === "startWork") {
    return "Заявка взята в работу.";
  }

  if (action === "complete") {
    return "Заявка завершена.";
  }

  if (action === "reject") {
    return "Заявка отклонена.";
  }

  if (action === "delegateExternal") {
    return "Заявка отправлена на межотдельное делегирование.";
  }

  if (action === "delegateInternal" || action === "returnToNew") {
    return "Заявка возвращена в статус «Новый» для переназначения.";
  }

  if (action === "confirmExternalDelegation" || action === "declineExternalDelegation") {
    return "Решение по делегированию зафиксировано.";
  }

  return "Действие применено в mock-режиме.";
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
