import { useEffect, useMemo, useState } from "react";

import { useApplicationsStore } from "@app/providers/ApplicationsProvider";
import { useAuth } from "@app/providers/AuthProvider";
import { useReferenceData } from "@app/providers/ReferenceDataProvider";
import { apiClient } from "@shared/api";
import type { Application, ApplicationPriority, PrioritySettings } from "@shared/model/domain";
import { priorityLabels } from "@shared/model/labels";
import { Button } from "@shared/ui";

import "./PrioritySettingsPage.css";

export function PrioritySettingsPage() {
  const { currentUser, credentials } = useAuth();
  const { applicationItems } = useApplicationsStore();
  const { departments, employees, prioritySettings, refresh } = useReferenceData();
  const canEdit = currentUser?.role === "top-manager";
  const availableDepartments = useMemo(
    () => currentUser?.role === "manager"
      ? departments.filter((department) => department.id === currentUser.departmentId)
      : departments,
    [currentUser, departments],
  );
  const availableDepartmentIds = useMemo(() => new Set(availableDepartments.map((department) => department.id)), [availableDepartments]);
  const sampleApplications = applicationItems.filter((application) => {
    const author = employees.find((user) => user.id === application.authorId);

    return availableDepartmentIds.has(author?.departmentId ?? application.departmentId);
  });
  const activeSettings = prioritySettings ?? {
    department: Object.fromEntries(departments.map((department) => [department.id, department.value])),
    deadline: 0,
    managerAuthor: Object.fromEntries(departments.map((department) => [department.id, 0])),
    urgent: {
      thresholdHours: 24,
      bonus: 0.5,
    },
  };
  const [draftSettings, setDraftSettings] = useState<PrioritySettings | null>(null);
  const [departmentDrafts, setDepartmentDrafts] = useState<Record<string, { employeeApplicationDelayMinutes: number; deadlineNotificationRatio: number }>>({});
  const [sampleApplicationId, setSampleApplicationId] = useState("");
  const [notice, setNotice] = useState("");
  const displayedSettings = draftSettings ?? activeSettings;

  useEffect(() => {
    setDepartmentDrafts(
      Object.fromEntries(
        availableDepartments.map((department) => [
          department.id,
          {
            employeeApplicationDelayMinutes: department.employeeApplicationDelayMinutes,
            deadlineNotificationRatio: department.deadlineNotificationRatio,
          },
        ]),
      ),
    );
  }, [availableDepartments]);

  const sampleApplication = sampleApplications.find((application) => application.id === sampleApplicationId) ?? sampleApplications[0];
  const preview = useMemo(
    () => (sampleApplication ? calculatePriorityPreview(sampleApplication, displayedSettings, employees) : null),
    [displayedSettings, employees, sampleApplication],
  );
  const hasChanges = Boolean(draftSettings) && JSON.stringify(draftSettings) !== JSON.stringify(activeSettings);

  const updateDepartmentSetting = (departmentId: string, key: "department" | "managerAuthor", value: number) => {
    const max = key === "department" ? 1.25 : 1;
    const nextValue = Math.min(max, Math.max(0, value));

    setDraftSettings((current) => ({
      ...(current ?? activeSettings),
      [key]: {
        ...(current ?? activeSettings)[key],
        [departmentId]: nextValue,
      },
    }));
    setNotice("");
  };

  const updateDepartmentDraft = (
    departmentId: string,
    key: "employeeApplicationDelayMinutes" | "deadlineNotificationRatio",
    value: number,
  ) => {
    const nextValue = key === "deadlineNotificationRatio"
      ? Math.min(1, Math.max(0, value))
      : Math.max(0, Math.round(value));

    setDepartmentDrafts((current) => ({
      ...current,
      [departmentId]: {
        employeeApplicationDelayMinutes: current[departmentId]?.employeeApplicationDelayMinutes ?? 0,
        deadlineNotificationRatio: current[departmentId]?.deadlineNotificationRatio ?? 0,
        [key]: nextValue,
      },
    }));
    setNotice("");
  };

  const saveDepartmentSettings = async (departmentId: string) => {
    if (!credentials || !departmentDrafts[departmentId]) {
      return;
    }

    try {
      await apiClient.updateDepartmentSettings(credentials, departmentId, departmentDrafts[departmentId]);
      await refresh();
      setNotice("Настройки отдела сохранены.");
    } catch {
      setNotice("Не удалось сохранить настройки отдела.");
    }
  };

  const updateDeadlineSetting = (value: number) => {
    const nextValue = Math.min(1, Math.max(0, value));

    setDraftSettings((current) => ({ ...(current ?? activeSettings), deadline: nextValue }));
    setNotice("");
  };

  const saveSettings = async () => {
    if (!credentials || !draftSettings) {
      return;
    }

    const totalWeight = Object.values(draftSettings.department).reduce((sum, value) => sum + value, 0) +
      Object.values(draftSettings.managerAuthor).reduce((sum, value) => sum + value, 0) +
      draftSettings.deadline;

    if (totalWeight <= 0) {
      setNotice("Хотя бы один коэффициент должен быть больше 0.");
      return;
    }

    try {
      const { urgent: _urgent, ...editableSettings } = draftSettings;

      await apiClient.updatePrioritySettings(credentials, editableSettings);
      await refresh();
      setDraftSettings(null);
      setNotice("Коэффициенты сохранены.");
    } catch {
      setNotice("Не удалось сохранить коэффициенты.");
    }
  };

  const resetSettings = () => {
    setDraftSettings(null);
    setNotice("Изменения отменены.");
  };

  return (
    <section className="priority-page">
      <header className="priority-page__header">
        <div>
          <h1>Изменение приоритетности заявки</h1>
          <p>{canEdit ? "Топ-менеджер настраивает коэффициенты расчета приоритета." : "Руководитель видит коэффициенты, назначенные топ-менеджером."}</p>
        </div>
      </header>

      {notice ? <div className="priority-notice">{notice}</div> : null}

      <div className="priority-layout">
        <article className="priority-settings">
          <header>
            <h2>Коэффициенты</h2>
            <span>Формула: приоритет = коэффициент отдела автора * коэффициент срока исполнения + коэффициент руководителя-автора + бонус срочности</span>
          </header>

          <div className="priority-settings__list">
            <div className="priority-setting priority-setting--head" role="row">
              <span>Отдел</span>
              <span>Коэффициент отдела</span>
              <span>Коэффициент руководителя отдела</span>
            </div>
            {availableDepartments.map((department) => (
              <div className="priority-setting priority-setting--department" key={department.id}>
                <span>
                  <strong>{department.name}</strong>
                  <small>Коэффициенты применяются к заявкам авторов из этого отдела.</small>
                </span>
                <input
                  type="number"
                  min="0"
                  max="1.25"
                  step="0.05"
                  value={displayedSettings.department[department.id] ?? 0}
                  onChange={(event) => updateDepartmentSetting(department.id, "department", Number(event.target.value))}
                  aria-label={`Коэффициент отдела ${department.name}`}
                  disabled={!canEdit}
                />
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={displayedSettings.managerAuthor[department.id] ?? 0}
                  onChange={(event) => updateDepartmentSetting(department.id, "managerAuthor", Number(event.target.value))}
                  aria-label={`Коэффициент руководителя отдела ${department.name}`}
                  disabled={!canEdit}
                />
              </div>
            ))}
            <label className="priority-setting priority-setting--deadline">
              <span>
                <strong>Срок исполнения</strong>
                <small>Чем меньше времени осталось до дедлайна, тем ближе фактор срока к 1 и тем сильнее он поднимает итоговый приоритет.</small>
              </span>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={displayedSettings.deadline}
                onChange={(event) => updateDeadlineSetting(Number(event.target.value))}
                disabled={!canEdit}
              />
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={displayedSettings.deadline}
                onChange={(event) => updateDeadlineSetting(Number(event.target.value))}
                aria-label="Коэффициент срока исполнения"
                disabled={!canEdit}
              />
            </label>
          </div>
          <footer className="priority-settings__actions">
            <Button type="button" variant="secondary" onClick={resetSettings} disabled={!hasChanges}>
              Отмена
            </Button>
            <Button type="button" onClick={() => void saveSettings()} disabled={!hasChanges || !canEdit}>
              Подтвердить
            </Button>
          </footer>
        </article>

        <article className="priority-settings priority-department-settings">
          <header>
            <h2>Настройки отдела</h2>
            <span>Кулдаун назначения и момент уведомления о приближении дедлайна.</span>
          </header>

          <div className="priority-department-settings__list">
            {availableDepartments.map((department) => {
              const draft = departmentDrafts[department.id] ?? {
                employeeApplicationDelayMinutes: department.employeeApplicationDelayMinutes,
                deadlineNotificationRatio: department.deadlineNotificationRatio,
              };
              const hasDepartmentChanges =
                draft.employeeApplicationDelayMinutes !== department.employeeApplicationDelayMinutes ||
                draft.deadlineNotificationRatio !== department.deadlineNotificationRatio;

              return (
                <div className="priority-department-setting" key={department.id}>
                  <span>
                    <strong>{department.name}</strong>
                    <small>Обычный руководитель меняет только свой отдел, топ-менеджер — любой.</small>
                  </span>
                  <label>
                    Кулдаун, минут
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={draft.employeeApplicationDelayMinutes}
                      onChange={(event) => updateDepartmentDraft(department.id, "employeeApplicationDelayMinutes", Number(event.target.value))}
                    />
                  </label>
                  <label>
                    Порог дедлайна
                    <input
                      type="number"
                      min="0"
                      max="1"
                      step="0.05"
                      value={draft.deadlineNotificationRatio}
                      onChange={(event) => updateDepartmentDraft(department.id, "deadlineNotificationRatio", Number(event.target.value))}
                    />
                  </label>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => void saveDepartmentSettings(department.id)}
                    disabled={!hasDepartmentChanges}
                  >
                    Сохранить
                  </Button>
                </div>
              );
            })}
          </div>
        </article>

        <aside className="priority-preview">
          <header>
            <h2>Предварительный расчет</h2>
            <label>
              Тестовая заявка
              <select value={sampleApplication?.id ?? ""} onChange={(event) => setSampleApplicationId(event.target.value)}>
                {sampleApplications.map((application) => (
                  <option value={application.id} key={application.id}>
                    ID {application.id} · {application.title}
                  </option>
                ))}
              </select>
            </label>
          </header>

          {preview ? (
            <>
              <div className={`priority-preview__score priority-preview__score--${preview.priority}`}>
                <span>Итоговое значение</span>
                <strong>{preview.score.toFixed(2)}</strong>
                <em>{priorityLabels[preview.priority]}</em>
              </div>

              <div className="priority-preview__factors">
                {preview.factors.map((factor) => (
                  <div key={factor.label}>
                    <span>{factor.label}</span>
                    <strong>{factor.value.toFixed(2)}</strong>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="priority-preview__empty">Нет заявок для предварительного расчета.</div>
          )}
        </aside>
      </div>
    </section>
  );
}

function calculatePriorityPreview(application: Application, settings: PrioritySettings, employees: Array<{ id: string; departmentId: string; role: string }>) {
  const author = employees.find((user) => user.id === application.authorId);
  const departmentId = author?.departmentId ?? application.departmentId;
  const isManagerAuthor = author?.role === "manager" || author?.role === "top-manager";
  const deadlinePressure = getDeadlinePressure(application.createdAt, application.deadlineAt);
  const urgentBonus = getUrgentBonus(application.createdAt, application.deadlineAt, settings.urgent);
  const factorValues = {
    department: settings.department[departmentId] ?? 0,
    deadline: deadlinePressure * settings.deadline,
    managerAuthor: isManagerAuthor ? settings.managerAuthor[departmentId] ?? 0 : 0,
    urgent: urgentBonus,
  };

  const weightedSum = factorValues.department * factorValues.deadline + factorValues.managerAuthor + factorValues.urgent;
  const score = clamp(weightedSum, 0, 1);

  return {
    score,
    priority: getPriorityByScore(score),
    factors: [
      { label: "Отдел", value: factorValues.department },
      { label: "Срок", value: factorValues.deadline },
      { label: "Автор-руководитель", value: factorValues.managerAuthor },
      { label: "Бонус срочности", value: factorValues.urgent },
    ],
  };
}

function getDeadlinePressure(createdAt: string, deadlineAt: string) {
  const now = Date.now();
  const created = new Date(createdAt).getTime();
  const deadline = new Date(deadlineAt).getTime();

  if (!Number.isFinite(created) || !Number.isFinite(deadline) || deadline <= created) {
    return 1;
  }

  return clamp((now - created) / (deadline - created), 0, 1);
}

function getUrgentBonus(createdAt: string, deadlineAt: string, urgent: PrioritySettings["urgent"]) {
  const created = new Date(createdAt).getTime();
  const deadline = new Date(deadlineAt).getTime();
  const thresholdMs = urgent.thresholdHours * 60 * 60 * 1000;

  if (!Number.isFinite(created) || !Number.isFinite(deadline) || deadline - created > thresholdMs) {
    return 0;
  }

  return urgent.bonus;
}

function getPriorityByScore(score: number): ApplicationPriority {
  if (score >= 0.82) {
    return "critical";
  }

  if (score >= 0.62) {
    return "high";
  }

  if (score >= 0.38) {
    return "medium";
  }

  return "low";
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}
