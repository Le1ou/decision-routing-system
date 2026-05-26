import { useMemo, useState } from "react";

import { departments, mockUsers, positions, requests, workTypes } from "@mocks/mockData";
import type { RequestPriority } from "@shared/model/domain";
import { priorityLabels } from "@shared/model/labels";
import { Button } from "@shared/ui";

import "./PrioritySettingsPage.css";

type PrioritySettingKey = "department" | "position" | "workType" | "deadline" | "managerAuthor";
type PrioritySettings = Record<PrioritySettingKey, number>;

const initialSettings: PrioritySettings = {
  department: 0.25,
  position: 0.15,
  workType: 0.25,
  deadline: 0.25,
  managerAuthor: 0.1,
};

const settingLabels: Record<PrioritySettingKey, { title: string; hint: string }> = {
  department: {
    title: "Отдел автора",
    hint: "Учитывает приоритетность подразделения, из которого создана заявка.",
  },
  position: {
    title: "Должность автора",
    hint: "Повышает вес заявок от руководящих или ключевых должностей.",
  },
  workType: {
    title: "Вид работ",
    hint: "Использует сложность выбранного вида работ.",
  },
  deadline: {
    title: "Срок исполнения",
    hint: "Чем ближе срок, тем выше предварительная оценка.",
  },
  managerAuthor: {
    title: "Руководитель как автор",
    hint: "Дополнительный вес, если заявку создал руководитель.",
  },
};

const complexityValues = {
  easy: 0.25,
  medium: 0.5,
  hard: 0.75,
  critical: 1,
};

export function PrioritySettingsPage() {
  const [savedSettings, setSavedSettings] = useState<PrioritySettings>(initialSettings);
  const [draftSettings, setDraftSettings] = useState<PrioritySettings>(initialSettings);
  const [sampleRequestId, setSampleRequestId] = useState(requests[0]?.id ?? "");
  const [notice, setNotice] = useState("");

  const sampleRequest = requests.find((request) => request.id === sampleRequestId) ?? requests[0];
  const preview = useMemo(
    () => (sampleRequest ? calculatePriorityPreview(sampleRequest.id, draftSettings) : null),
    [draftSettings, sampleRequest],
  );
  const hasChanges = Object.keys(draftSettings).some(
    (key) => draftSettings[key as PrioritySettingKey] !== savedSettings[key as PrioritySettingKey],
  );

  const updateSetting = (key: PrioritySettingKey, value: number) => {
    const nextValue = Math.min(1, Math.max(0, value));

    setDraftSettings((current) => ({ ...current, [key]: nextValue }));
    setNotice("");
  };

  const saveSettings = () => {
    setSavedSettings(draftSettings);
    setNotice("Коэффициенты сохранены в mock-режиме.");
  };

  const resetSettings = () => {
    setDraftSettings(savedSettings);
    setNotice("Изменения отменены.");
  };

  return (
    <section className="priority-page">
      <header className="priority-page__header">
        <div>
          <h1>Изменение приоритетности заявки</h1>
          <p>Настройка влияния факторов на предварительный расчет приоритета в mock-режиме.</p>
        </div>
        <div className="priority-page__actions">
          <Button type="button" variant="secondary" onClick={resetSettings} disabled={!hasChanges}>
            Отмена
          </Button>
          <Button type="button" onClick={saveSettings} disabled={!hasChanges}>
            Подтвердить
          </Button>
        </div>
      </header>

      {notice ? <div className="priority-notice">{notice}</div> : null}

      <div className="priority-layout">
        <article className="priority-settings">
          <header>
            <h2>Коэффициенты</h2>
            <span>Значение каждого параметра от 0 до 1</span>
          </header>

          <div className="priority-settings__list">
            {(Object.keys(settingLabels) as PrioritySettingKey[]).map((key) => (
              <label className="priority-setting" key={key}>
                <span>
                  <strong>{settingLabels[key].title}</strong>
                  <small>{settingLabels[key].hint}</small>
                </span>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={draftSettings[key]}
                  onChange={(event) => updateSetting(key, Number(event.target.value))}
                />
                <input
                  type="number"
                  min="0"
                  max="1"
                  step="0.05"
                  value={draftSettings[key]}
                  onChange={(event) => updateSetting(key, Number(event.target.value))}
                  aria-label={settingLabels[key].title}
                />
              </label>
            ))}
          </div>
        </article>

        <aside className="priority-preview">
          <header>
            <h2>Предварительный расчет</h2>
            <label>
              Тестовая заявка
              <select value={sampleRequest?.id ?? ""} onChange={(event) => setSampleRequestId(event.target.value)}>
                {requests.map((request) => (
                  <option value={request.id} key={request.id}>
                    {request.number} · {request.title}
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

function calculatePriorityPreview(requestId: string, settings: PrioritySettings) {
  const request = requests.find((item) => item.id === requestId);

  if (!request) {
    return null;
  }

  const author = mockUsers.find((user) => user.id === request.authorId);
  const department = departments.find((item) => item.id === author?.departmentId);
  const position = positions.find((item) => item.id === author?.positionId);
  const workType = workTypes.find((item) => item.id === request.workTypeId);
  const factorValues = {
    department: department?.value ?? 0,
    position: position?.isTop ? 1 : 0.45,
    workType: workType ? complexityValues[workType.complexity] : 0,
    deadline: getDeadlinePressure(request.deadlineAt),
    managerAuthor: author?.role === "manager" ? 1 : 0,
  };

  const weightedSum = (Object.keys(settings) as PrioritySettingKey[]).reduce(
    (sum, key) => sum + settings[key] * factorValues[key],
    0,
  );
  const weightTotal = Object.values(settings).reduce((sum, value) => sum + value, 0) || 1;
  const score = Math.min(1, weightedSum / weightTotal);

  return {
    score,
    priority: getPriorityByScore(score),
    factors: [
      { label: "Отдел", value: factorValues.department },
      { label: "Должность", value: factorValues.position },
      { label: "Вид работ", value: factorValues.workType },
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

function getPriorityByScore(score: number): RequestPriority {
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
