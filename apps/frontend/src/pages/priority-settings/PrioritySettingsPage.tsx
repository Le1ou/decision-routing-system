import { useMemo, useState } from "react";

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
  const availableDepartments = currentUser?.role === "manager"
    ? departments.filter((department) => department.id === currentUser.departmentId)
    : departments;
  const availableDepartmentIds = new Set(availableDepartments.map((department) => department.id));
  const sampleApplications = applicationItems.filter((application) => {
    const author = employees.find((user) => user.id === application.authorId);

    return availableDepartmentIds.has(author?.departmentId ?? application.departmentId);
  });
  const activeSettings = prioritySettings ?? {
    department: Object.fromEntries(departments.map((department) => [department.id, department.value])),
    deadline: 0,
    managerAuthor: Object.fromEntries(departments.map((department) => [department.id, 0])),
  };
  const [draftSettings, setDraftSettings] = useState<PrioritySettings | null>(null);
  const [sampleApplicationId, setSampleApplicationId] = useState("");
  const [notice, setNotice] = useState("");
  const displayedSettings = draftSettings ?? activeSettings;

  const sampleApplication = sampleApplications.find((application) => application.id === sampleApplicationId) ?? sampleApplications[0];
  const preview = useMemo(
    () => (sampleApplication ? calculatePriorityPreview(sampleApplication, displayedSettings, employees) : null),
    [displayedSettings, employees, sampleApplication],
  );
  const hasChanges = Boolean(draftSettings) && JSON.stringify(draftSettings) !== JSON.stringify(activeSettings);

  const updateDepartmentSetting = (departmentId: string, key: "department" | "managerAuthor", value: number) => {
    const nextValue = Math.min(1, Math.max(0, value));

    setDraftSettings((current) => ({
      ...(current ?? activeSettings),
      [key]: {
        ...(current ?? activeSettings)[key],
        [departmentId]: nextValue,
      },
    }));
    setNotice("");
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
      await apiClient.updatePrioritySettings(credentials, draftSettings);
      await refresh();
      setDraftSettings(null);
      setNotice("Коэффициенты сохранены.");
    } catch {
      setNotice("Backend не сохранил коэффициенты.");
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
            <span>Формула: приоритет = коэффициент отдела автора * коэффициент срока исполнения + коэффициент руководителя-автора</span>
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
                  max="1"
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
  const deadlinePressure = getDeadlinePressure(application.deadlineAt);
  const factorValues = {
    department: settings.department[departmentId] ?? 0,
    deadline: deadlinePressure * settings.deadline,
    managerAuthor: isManagerAuthor ? settings.managerAuthor[departmentId] ?? 0 : 0,
  };

  const weightedSum = factorValues.department * factorValues.deadline + factorValues.managerAuthor;
  const score = Math.min(1, weightedSum);

  return {
    score,
    priority: getPriorityByScore(score),
    factors: [
      { label: "Отдел", value: factorValues.department },
      { label: "Срок", value: factorValues.deadline },
      { label: "Автор-руководитель", value: factorValues.managerAuthor },
    ],
  };
}

function getDeadlinePressure(deadlineAt: string) {
  const now = new Date("2026-05-26T12:00:00.000Z").getTime();
  const deadline = new Date(deadlineAt).getTime();
  const hoursLeft = (deadline - now) / 1000 / 60 / 60;

  if (hoursLeft <= 0) {
    return 1;
  }

  if (hoursLeft <= 24) {
    return 0.9;
  }

  if (hoursLeft <= 72) {
    return 0.65;
  }

  return 0.35;
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
