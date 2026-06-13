import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
import { useApplicationsStore } from "@app/providers/ApplicationsProvider";
import { useReferenceData } from "@app/providers/ReferenceDataProvider";
import { apiClient, ApiError, mapChatMessage } from "@shared/api";
import { env } from "@shared/config/env";
import { usePolling } from "@shared/hooks/usePolling";
import type { Complexity, Application, ApplicationAction, ChatMessage } from "@shared/model/domain";
import { actionLabels, priorityLabels, statusLabels } from "@shared/model/labels";
import {
  applyApplicationFilters,
  sortApplications,
  type ApplicationFilter,
  type ApplicationSortDirection,
  type ApplicationSortKey,
} from "@shared/model/applicationRules";

import "./ApplicationsPage.css";

export function ApplicationsPage() {
  const { currentUser, credentials } = useAuth();
  const {
    applicationItems,
    applicationsTotal,
    hasMoreApplications,
    isLoading,
    error,
    performAction,
    refreshApplicationDetail,
  } = useApplicationsStore();
  const { departments, positions, workTypes, employees } = useReferenceData();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sortKey, setSortKey] = useState<ApplicationSortKey>("priority");
  const [sortDirection, setSortDirection] = useState<ApplicationSortDirection>("default");
  const [filters, setFilters] = useState<ApplicationFilter>({});
  const [selectedApplicationId, setSelectedApplicationId] = useState<string | null>(null);
  const [isSidebarHidden, setIsSidebarHidden] = useState(false);
  const [notice, setNotice] = useState("");
  const [pendingAction, setPendingAction] = useState<ApplicationAction | null>(null);
  const [actionForm, setActionForm] = useState({
    comment: "",
    complexity: "medium",
    departmentId: "it",
    description: "",
    executorId: "",
    resultText: "",
    workTypeId: "",
  });
  const [actionError, setActionError] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatUnreadCount, setChatUnreadCount] = useState(0);
  const [chatDraft, setChatDraft] = useState("");
  const [chatError, setChatError] = useState("");
  const [isChatLoading, setIsChatLoading] = useState(false);

  const visibleApplications = useMemo(() => {
    if (!currentUser) {
      return [];
    }

    const getExecutorName = (executorId?: string) => employees.find((user) => user.id === executorId)?.fullName ?? "";

    return sortApplications(
      applyApplicationFilters(applicationItems, filters, currentUser, { getExecutorName }),
      sortKey,
      sortDirection,
    );
  }, [currentUser, employees, filters, applicationItems, sortDirection, sortKey]);

  useEffect(() => {
    const applicationIdFromUrl = searchParams.get("application");

    if (visibleApplications.length === 0) {
      setSelectedApplicationId(null);
      return;
    }

    if (applicationIdFromUrl && visibleApplications.some((application) => application.id === applicationIdFromUrl)) {
      setSelectedApplicationId(applicationIdFromUrl);
      return;
    }

    if (!selectedApplicationId || !visibleApplications.some((application) => application.id === selectedApplicationId)) {
      setSelectedApplicationId(visibleApplications[0].id);
    }
  }, [searchParams, selectedApplicationId, visibleApplications]);

  useEffect(() => {
    setNotice("");
    setActionError("");
    setPendingAction(null);
    setChatMessages([]);
    setChatUnreadCount(0);
    setChatDraft("");
    setChatError("");
  }, [selectedApplicationId]);

  useEffect(() => {
    if (selectedApplicationId) {
      void refreshApplicationDetail(selectedApplicationId);
    }
  }, [refreshApplicationDetail, selectedApplicationId]);

  usePolling(
    async () => {
      if (selectedApplicationId) {
        await refreshApplicationDetail(selectedApplicationId);
      }
    },
    env.pollIntervalMs,
    Boolean(currentUser && selectedApplicationId),
  );

  const loadChatMessages = useCallback(async (applicationId: string, options: { afterId?: string; showLoading?: boolean } = {}) => {
    if (!credentials) {
      setChatMessages([]);
      setChatUnreadCount(0);
      return;
    }

    if (options.showLoading) {
      setIsChatLoading(true);
    }

    try {
      const response = await apiClient.getMessages(credentials, applicationId, { afterId: options.afterId });
      const messages = response.items.map(mapChatMessage);

      setChatUnreadCount(response.unreadCount);
      setChatMessages((current) => {
        if (!options.afterId) {
          return messages;
        }

        const knownIds = new Set(current.map((message) => message.id));
        const nextMessages = messages.filter((message) => !knownIds.has(message.id));

        return nextMessages.length > 0 ? [...current, ...nextMessages] : current;
      });

      if (response.unreadCount > 0) {
        await apiClient.markChatRead(credentials, applicationId);
        setChatUnreadCount(0);
      }
    } catch {
      if (!options.afterId) {
        setChatError("Чат заявки недоступен.");
      }
    } finally {
      if (options.showLoading) {
        setIsChatLoading(false);
      }
    }
  }, [credentials]);

  const selectedApplication = visibleApplications.find((application) => application.id === selectedApplicationId) ?? visibleApplications[0];
  const applicationActions = selectedApplication?.availableActions ?? [];
  const applicationDepartment = departments.find((department) => department.id === selectedApplication?.departmentId);
  const applicationWorkType = workTypes.find((workType) => workType.id === selectedApplication?.workTypeId);
  const applicationDelegation = selectedApplication?.delegation;
  const author = selectedApplication?.author ?? employees.find((user) => user.id === selectedApplication?.authorId);
  const executor = selectedApplication?.executor ?? employees.find((user) => user.id === selectedApplication?.executorId);
  const previousExecutor = selectedApplication?.previousExecutor ?? employees.find((user) => user.id === selectedApplication?.previousExecutorId);
  const delegatingExecutor = selectedApplication?.delegatedByEmployee
    ?? employees.find((user) => user.id === applicationDelegation?.delegatedByEmployeeId)
    ?? previousExecutor;
  const authorDepartment = departments.find((department) => department.id === author?.departmentId);
  const authorJobTitle = positions.find((position) => position.id === author?.positionId);
  const executorDepartment = departments.find((department) => department.id === executor?.departmentId);
  const executorJobTitle = positions.find((position) => position.id === executor?.positionId);
  const delegatingExecutorDepartment = departments.find((department) => department.id === delegatingExecutor?.departmentId);
  const delegatingExecutorJobTitle = positions.find((position) => position.id === delegatingExecutor?.positionId);
  const applicationAttachments = selectedApplication?.attachments ?? [];
  const applicationExtraNames = selectedApplication?.attachmentNames ?? [];
  const lastChatMessageId = chatMessages.at(-1)?.id;
  const canWriteChat = selectedApplication && currentUser
    ? canCurrentUserWriteChat(selectedApplication, currentUser)
    : false;
  const busyExecutorApplication = applicationItems.find(
    (application) =>
      application.id !== selectedApplication?.id &&
      application.executorId === actionForm.executorId &&
      (application.status === "assigned" || application.status === "inProgress"),
  );

  const executorsForDepartment = employees.filter(
    (user) => user.role === "executor" && user.departmentId === selectedApplication?.departmentId && user.isActive,
  );
  const actionsWithForm: ApplicationAction[] = [
    "assignExecutor",
    "delegateInternal",
    "delegateExternal",
    "editDescription",
    "returnToNew",
    "complete",
    "changeWorkType",
    "reject",
    "cancel",
  ];

  useEffect(() => {
    if (selectedApplication?.id) {
      void loadChatMessages(selectedApplication.id, { showLoading: true });
    }
  }, [loadChatMessages, selectedApplication?.id]);

  usePolling(
    async () => {
      if (selectedApplication?.id) {
        await loadChatMessages(selectedApplication.id, { afterId: lastChatMessageId });
      }
    },
    Math.min(env.pollIntervalMs, 5000),
    Boolean(currentUser && selectedApplication?.id),
  );

  const handleApplicationAction = (action: ApplicationAction) => {
    if (!selectedApplication || !currentUser) {
      return;
    }

    if (actionsWithForm.includes(action)) {
      setPendingAction(action);
      setActionError("");
      setActionForm({
        comment: "",
        complexity: selectedApplication.assignedComplexity ?? applicationWorkType?.complexity ?? "medium",
        departmentId: selectedApplication.departmentId,
        description: selectedApplication.description,
        executorId: selectedApplication.executorId ?? executorsForDepartment[0]?.id ?? "",
        resultText: selectedApplication.resultText ?? "",
        workTypeId: selectedApplication.workTypeId,
      });
      return;
    }

    void applyAction(action);
  };

  const applyAction = async (action: ApplicationAction, payload: ActionPayload = {}) => {
    if (!selectedApplication || !currentUser) {
      return;
    }

    const currentComplexity = selectedApplication.assignedComplexity ?? applicationWorkType?.complexity ?? "medium";

    if (
      action === "delegateInternal" &&
      payload.complexity &&
      complexityOrder[payload.complexity as Complexity] < complexityOrder[currentComplexity]
    ) {
      setActionError("Новая сложность не может быть ниже текущей.");
      return;
    }

    const validationError = getActionValidationError(action, payload, selectedApplication);

    if (validationError) {
      setActionError(validationError);
      return;
    }

    try {
      await performAction(selectedApplication.id, {
        action,
        executorId: payload.executorId,
        departmentId: payload.departmentId,
        workTypeId: payload.workTypeId,
        comment: payload.comment,
        complexity: payload.complexity as Complexity | undefined,
        resultText: payload.resultText,
        description: payload.description,
      });

      setNotice(getActionNotice(action));
      setActionError("");
      setPendingAction(null);
    } catch {
      setActionError("Backend не применил действие. Проверьте обязательные поля и права пользователя.");
    }
  };

  const sendChatMessage = async () => {
    const text = chatDraft.trim();

    if (!credentials || !selectedApplication || !text) {
      return;
    }

    if (text.length > 2000) {
      setChatError("Сообщение не должно быть длиннее 2000 символов.");
      return;
    }

    try {
      await apiClient.sendMessage(credentials, selectedApplication.id, text);
      setChatDraft("");
      setChatError("");
      await loadChatMessages(selectedApplication.id);
      await apiClient.markChatRead(credentials, selectedApplication.id);
      setChatUnreadCount(0);
    } catch (sendError) {
      if (sendError instanceof ApiError && sendError.status === 409) {
        setChatError("Заявка закрыта, чат доступен только для чтения.");
      } else if (sendError instanceof ApiError && sendError.status === 403) {
        setChatError("У вас нет прав писать в чат этой заявки.");
      } else {
        setChatError("Backend не отправил сообщение.");
      }
    }
  };

  if (!currentUser) {
    return (
      <section className="applications-page applications-page--empty">
        <div className="applications-empty">Для текущей роли нет доступных заявок.</div>
      </section>
    );
  }

  if (isLoading) {
    return (
      <section className="applications-page applications-page--empty">
        <div className="applications-empty">Загружаем заявки...</div>
      </section>
    );
  }

  return (
    <section className={isSidebarHidden ? "applications-page applications-page--sidebar-hidden" : "applications-page"}>
      <aside className="applications-sidebar" aria-hidden={isSidebarHidden}>
        <div className="applications-toolbar">
          <select value={sortKey} onChange={(event) => setSortKey(event.target.value as ApplicationSortKey)} aria-label="Сортировка">
            <option value="priority">Сортировать по приоритету</option>
            <option value="status">Сортировать по статусу</option>
            <option value="createdAt">Сортировать по дате создания</option>
            <option value="finishedAt">Сортировать по дате закрытия</option>
          </select>
          <button
            type="button"
            aria-label={sortDirection === "default" ? "Включить обратный порядок сортировки" : "Вернуть порядок сортировки"}
            title={sortDirection === "default" ? "Обратный порядок" : "Порядок по умолчанию"}
            onClick={() => setSortDirection((current) => (current === "default" ? "reverse" : "default"))}
          >
            {sortDirection === "default" ? "↓" : "↑"}
          </button>
        </div>

        <div className="applications-filters" aria-label="Фильтры заявок">
          <input
            value={filters.applicationIdQuery ?? ""}
            onChange={(event) => setFilters((current) => ({ ...current, applicationIdQuery: event.target.value }))}
            placeholder="ID заявки"
            aria-label="Поиск по ID заявки"
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
          {currentUser.role === "manager" || currentUser.role === "top-manager" ? (
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

        <div className="applications-list">
          {hasMoreApplications ? (
            <div className="applications-list__more">
              Показаны первые {applicationItems.length} из {applicationsTotal} заявок. Уточните фильтр или ID.
            </div>
          ) : null}
          {visibleApplications.length > 0 ? (
            visibleApplications.map((application) => (
              <button
                className={application.id === selectedApplication?.id ? "application-row application-row--active" : "application-row"}
                type="button"
                key={application.id}
                onClick={() => {
                  setSelectedApplicationId(application.id);
                  setSearchParams({ application: application.id });
                  setNotice("");
                  setPendingAction(null);
                  setActionError("");
                }}
              >
                <strong>Заявка ID {application.id}</strong>
                <span>{application.title}</span>
                <small>
                  {statusLabels[application.status]} · {priorityLabels[application.priority]}
                </small>
              </button>
            ))
          ) : (
            <div className="applications-list__empty">Заявки не найдены</div>
          )}
        </div>
      </aside>

      {selectedApplication ? (
      <article className="application-card">
        {notice || error ? <div className="application-card__notice">{notice || error}</div> : null}
        <button
          className="application-card__toggle-list"
          type="button"
          onClick={() => setIsSidebarHidden((value) => !value)}
        >
          {isSidebarHidden ? "Показать список" : "Скрыть список"}
        </button>
        <button
          className="application-card__edit"
          type="button"
          aria-label="Редактировать"
          disabled={!applicationActions.includes("editDescription")}
          onClick={() => handleApplicationAction("editDescription")}
        >
          ✎
        </button>

        <header className="application-card__title">
          <h1>{applicationDepartment?.name ?? "Отдел не указан"} / Заявка ID {selectedApplication.id}</h1>
          <input value={selectedApplication.title} readOnly aria-label="Тема заявки" />
        </header>

        <div className="application-card__actions application-card__actions--top">
          {applicationActions.map((action) => (
            <button type="button" key={action} onClick={() => handleApplicationAction(action)}>
              {actionLabels[action]}
            </button>
          ))}
        </div>

        <div className="application-card__main">
          <section className="application-card__workarea">
            <div className="application-card__section-header">
              <strong>Описание</strong>
              <span>Предыдущий исполнитель: {previousExecutor?.fullName ?? "не назначен"}</span>
            </div>
            <textarea
              value={selectedApplication.description}
              readOnly
              aria-label="Описание заявки"
            />
            <label className="application-card__comment">
              <span>Комментарий исполнителя:</span>
              <textarea
                value={selectedApplication.executorComment ?? ""}
                placeholder="Комментарий появится после назначения или выполнения работ"
                readOnly
                aria-label="Комментарий исполнителя"
              />
            </label>
            {selectedApplication.managerComment ? (
              <label className="application-card__comment">
                <span>Комментарий руководителя:</span>
                <textarea
                  value={selectedApplication.managerComment}
                  readOnly
                  aria-label="Комментарий руководителя"
                />
              </label>
            ) : null}
            {selectedApplication.status === "completed" ? (
              <label className="application-card__comment">
                <span>Результат работы:</span>
                <textarea
                  value={selectedApplication.resultText ?? ""}
                  placeholder="Результат появится после завершения заявки"
                  readOnly
                  aria-label="Результат работы"
                />
              </label>
            ) : null}
            <section className="application-card__attachments">
              <h2>Вложения</h2>
              {applicationAttachments.length > 0 ? (
                <ul>
                  {applicationAttachments.map((attachment) => (
                    <li key={attachment.id}>
                      {attachment.url ? (
                        <a href={attachment.url} target="_blank" rel="noopener noreferrer">
                          {attachment.name}
                        </a>
                      ) : (
                        attachment.name
                      )}
                    </li>
                  ))}
                </ul>
              ) : applicationExtraNames.length > 0 ? (
                <ul>
                  {applicationExtraNames.map((name, index) => (
                    <li key={`${selectedApplication.id}-${name}-${index}`}>{name}</li>
                  ))}
                </ul>
              ) : (
                <p>Файлы не прикреплены</p>
              )}
            </section>

            <section className="application-chat" aria-label="Чат заявки">
              <header>
                <div>
                  <h2>Чат заявки</h2>
                  <span>{chatUnreadCount > 0 ? `${chatUnreadCount} непрочитанных` : "Сообщения по заявке"}</span>
                </div>
              </header>

              <div className="application-chat__messages">
                {isChatLoading ? (
                  <div className="application-chat__empty">Загружаем чат...</div>
                ) : chatMessages.length > 0 ? (
                  chatMessages.map((message) => (
                    <article
                      className={message.authorId === currentUser.id ? "application-chat__message application-chat__message--mine" : "application-chat__message"}
                      key={message.id}
                    >
                      <header>
                        <strong>{message.author?.fullName ?? "Участник заявки"}</strong>
                        <time>{formatDateTime(message.createdAt)}</time>
                      </header>
                      <p>{message.text}</p>
                    </article>
                  ))
                ) : (
                  <div className="application-chat__empty">В чате пока нет сообщений.</div>
                )}
              </div>

              {selectedApplication.status === "completed" || selectedApplication.status === "rejected" ? (
                <div className="application-chat__readonly">Заявка закрыта, чат доступен только для чтения.</div>
              ) : canWriteChat ? (
                <form
                  className="application-chat__form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void sendChatMessage();
                  }}
                >
                  <textarea
                    value={chatDraft}
                    onChange={(event) => {
                      setChatDraft(event.target.value);
                      setChatError("");
                    }}
                    maxLength={2000}
                    placeholder="Напишите сообщение"
                  />
                  <footer>
                    <span>{chatDraft.length}/2000</span>
                    <button type="submit" disabled={!chatDraft.trim()}>
                      Отправить
                    </button>
                  </footer>
                </form>
              ) : (
                <div className="application-chat__readonly">История доступна только участникам заявки.</div>
              )}
              {chatError ? <div className="application-chat__error">{chatError}</div> : null}
            </section>
          </section>

          <aside className="application-card__info">
            <div className="application-card__params">
              <label>
                Статус:
                <select value={selectedApplication.status} disabled>
                  <option value={selectedApplication.status}>{statusLabels[selectedApplication.status]}</option>
                </select>
              </label>
              <label>
                Приоритет:
                <input value={priorityLabels[selectedApplication.priority]} readOnly />
              </label>
              <label>
                Вид работ:
                <input value={applicationWorkType?.name ?? "-"} readOnly />
              </label>
              <label>
                Сложность:
                <input value={complexityLabels[selectedApplication.assignedComplexity ?? applicationWorkType?.complexity ?? "medium"]} readOnly />
              </label>
            </div>

            <section className="application-info-box">
              <h2>Автор заявки</h2>
              <p><b>ФИО:</b> {author?.fullName ?? "-"}</p>
              <p><b>Отдел:</b> {authorDepartment?.name ?? "-"}</p>
              <p><b>Должность:</b> {authorJobTitle?.name ?? "-"}</p>
            </section>

            <section className="application-info-box">
              <h2>Исполнитель</h2>
              <p><b>ФИО:</b> {executor?.fullName ?? "-"}</p>
              <p><b>Отдел:</b> {executorDepartment?.name ?? "-"}</p>
              <p><b>Должность:</b> {executorJobTitle?.name ?? "-"}</p>
            </section>

            {selectedApplication.delegatedFromDepartmentId || applicationDelegation ? (
              <section className="application-info-box">
                <h2>Делегирование</h2>
                <p><b>Из отдела:</b> {departments.find((department) => department.id === selectedApplication.delegatedFromDepartmentId)?.name ?? "-"}</p>
                <p><b>В отдел:</b> {departments.find((department) => department.id === selectedApplication.delegatedToDepartmentId)?.name ?? "-"}</p>
                <p><b>Комментарий:</b> {applicationDelegation?.comment ?? selectedApplication.executorComment ?? "-"}</p>
                <p><b>Кто делегировал:</b> {delegatingExecutor?.fullName ?? "-"}</p>
                <p><b>Отдел:</b> {delegatingExecutorDepartment?.name ?? "-"}</p>
                <p><b>Должность:</b> {delegatingExecutorJobTitle?.name ?? "-"}</p>
              </section>
            ) : null}

            <section className="application-info-box application-info-box--dates">
              <h2>Информация о заявке</h2>
              <p><b>Дата и время последнего изменения:</b> {formatDateTime(selectedApplication.updatedAt)}</p>
              <p><b>Дата и время создания заявки:</b> {formatDateTime(selectedApplication.createdAt)}</p>
              <p><b>Дата и время назначения исполнителя:</b> {formatDateTime(selectedApplication.assignedAt)}</p>
              <p><b>Дата и время взятия в работу заявки:</b> {formatDateTime(selectedApplication.startedAt)}</p>
              <p><b>Дата и время закрытия заявки:</b> {formatDateTime(selectedApplication.finishedAt)}</p>
            </section>
          </aside>
        </div>
      </article>
      ) : (
        <div className="application-card application-card--empty">
          <div className="applications-empty">Заявки не найдены. Измените параметры фильтрации.</div>
        </div>
      )}
      {pendingAction && selectedApplication ? (
        <div className="application-modal" role="dialog" aria-modal="true" aria-label={actionLabels[pendingAction]}>
          <form
            className="application-modal__panel"
            onSubmit={(event) => {
              event.preventDefault();
              void applyAction(pendingAction, actionForm);
            }}
          >
            <header>
              <h2>{actionLabels[pendingAction]}</h2>
              <button type="button" onClick={() => setPendingAction(null)} aria-label="Закрыть">×</button>
            </header>

            {pendingAction === "assignExecutor" ? (
              <>
                <label>
                  Исполнитель
                  <select
                    value={actionForm.executorId}
                    onChange={(event) => {
                      setActionForm((current) => ({ ...current, executorId: event.target.value }));
                      setActionError("");
                    }}
                  >
                    {executorsForDepartment.map((user) => (
                      <option value={user.id} key={user.id}>{user.fullName}</option>
                    ))}
                  </select>
                </label>
                {busyExecutorApplication ? (
                  <div className="application-modal__warning">
                    Исполнитель уже занят заявкой ID {busyExecutorApplication.id}. При назначении backend вернет прежнюю активную заявку в статус «Новый».
                  </div>
                ) : null}
              </>
            ) : null}

            {pendingAction === "delegateExternal" ? (
              <label>
                Новый отдел
                <select
                  value={actionForm.departmentId}
                  onChange={(event) => {
                    setActionForm((current) => ({ ...current, departmentId: event.target.value }));
                    setActionError("");
                  }}
                >
                  {departments
                    .filter((department) => department.id !== selectedApplication.departmentId)
                    .map((department) => (
                      <option value={department.id} key={department.id}>{department.name}</option>
                    ))}
                </select>
              </label>
            ) : null}

            {pendingAction === "delegateInternal" ? (
              <section className="application-modal__complexity">
                <div>
                  <span>Текущая сложность</span>
                  <strong>{complexityLabels[selectedApplication.assignedComplexity ?? applicationWorkType?.complexity ?? "medium"]}</strong>
                </div>
                <label>
                  Новая сложность
                  <select
                    value={actionForm.complexity}
                    onChange={(event) => {
                      setActionForm((current) => ({ ...current, complexity: event.target.value }));
                      setActionError("");
                    }}
                  >
                    <option value="easy">Легкая</option>
                    <option value="medium">Средняя</option>
                    <option value="hard">Высокая</option>
                    <option value="critical">Критичная</option>
                  </select>
                </label>
              </section>
            ) : null}

            {pendingAction === "delegateInternal" ? (
              <label>
                Новый вид работ, если требуется
                <select
                  value={actionForm.workTypeId}
                  onChange={(event) => {
                    setActionForm((current) => ({ ...current, workTypeId: event.target.value }));
                    setActionError("");
                  }}
                >
                  {workTypes
                    .filter((workType) => workType.departmentId === selectedApplication.departmentId)
                    .map((workType) => (
                      <option value={workType.id} key={workType.id}>{workType.name}</option>
                    ))}
                </select>
              </label>
            ) : null}

            {pendingAction === "editDescription" ? (
              <label>
                Описание проблемы
                <textarea
                  value={actionForm.description}
                  onChange={(event) => {
                    setActionForm((current) => ({ ...current, description: event.target.value }));
                    setActionError("");
                  }}
                  maxLength={1000}
                  placeholder="Уточните описание проблемы"
                />
              </label>
            ) : null}

            {pendingAction === "changeWorkType" ? (
              <label>
                Вид работ
                <select
                  value={actionForm.workTypeId}
                  onChange={(event) => {
                    setActionForm((current) => ({ ...current, workTypeId: event.target.value }));
                    setActionError("");
                  }}
                >
                  {workTypes
                    .filter((workType) => workType.departmentId === selectedApplication.departmentId)
                    .map((workType) => (
                      <option value={workType.id} key={workType.id}>{workType.name}</option>
                    ))}
                </select>
              </label>
            ) : null}

            {pendingAction === "complete" ? (
              <label>
                Состав / результат работ
                <textarea
                  value={actionForm.resultText}
                  onChange={(event) => {
                    setActionForm((current) => ({ ...current, resultText: event.target.value }));
                    setActionError("");
                  }}
                  placeholder="Опишите, какие работы выполнены"
                />
              </label>
            ) : null}

            {pendingAction !== "editDescription" && pendingAction !== "complete" && pendingAction !== "changeWorkType" ? (
            <label>
              Комментарий
              <textarea
                value={actionForm.comment}
                onChange={(event) => {
                  setActionForm((current) => ({ ...current, comment: event.target.value }));
                  setActionError("");
                }}
                placeholder="Необязательное пояснение для истории заявки"
              />
            </label>
            ) : null}

            {actionError ? <div className="application-modal__error">{actionError}</div> : null}

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

type ActionPayload = {
  comment?: string;
  complexity?: string;
  description?: string;
  departmentId?: string;
  executorId?: string;
  resultText?: string;
  workTypeId?: string;
};

const complexityOrder: Record<Complexity, number> = {
  easy: 1,
  medium: 2,
  hard: 3,
  critical: 4,
};

const complexityLabels: Record<Complexity, string> = {
  easy: "Легкая",
  medium: "Средняя",
  hard: "Высокая",
  critical: "Критичная",
};

function getActionValidationError(
  action: ApplicationAction,
  payload: ActionPayload,
  application: Application,
) {
  if (action === "assignExecutor" && !payload.executorId) {
    return "Выберите исполнителя.";
  }

  if (action === "delegateExternal") {
    if (!payload.departmentId) {
      return "Выберите отдел для делегирования.";
    }

    if (payload.departmentId === application.departmentId) {
      return "Выберите другой отдел.";
    }

  }

  if (action === "delegateInternal" && !payload.complexity) {
    return "Выберите новую сложность.";
  }

  if (action === "complete" && !payload.resultText?.trim()) {
    return "Заполните состав или результат выполненных работ.";
  }

  if (action === "editDescription") {
    const nextDescription = payload.description?.trim() ?? "";

    if (!nextDescription) {
      return "Описание не должно быть пустым.";
    }

    if (nextDescription === application.description.trim()) {
      return "Измените описание перед подтверждением.";
    }
  }

  if (action === "changeWorkType") {
    if (!payload.workTypeId) {
      return "Выберите вид работ.";
    }

    if (payload.workTypeId === application.workTypeId) {
      return "Выберите другой вид работ.";
    }
  }

  return "";
}

function getActionNotice(action: ApplicationAction) {
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

  if (action === "cancel") {
    return "Заявка отменена.";
  }

  if (action === "archive") {
    return "Заявка перемещена в архив и скрыта из списка.";
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

  return "Действие применено.";
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

function canCurrentUserWriteChat(application: Application, user: { id: string; role: string; departmentId: string }) {
  if (application.status === "completed" || application.status === "rejected") {
    return false;
  }

  if (application.authorId === user.id || application.executorId === user.id) {
    return true;
  }

  if (user.role === "top-manager") {
    return true;
  }

  return user.role === "manager" && (
    application.departmentId === user.departmentId ||
    application.delegatedToDepartmentId === user.departmentId ||
    application.delegatedFromDepartmentId === user.departmentId
  );
}
