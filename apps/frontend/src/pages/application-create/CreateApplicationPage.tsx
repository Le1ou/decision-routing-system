import { FormEvent, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
import { useApplicationsStore } from "@app/providers/ApplicationsProvider";
import { departments, jobTitles, workTypes } from "@mocks/mockData";
import type { Application } from "@shared/model/domain";
import { Button } from "@shared/ui";

import "./CreateApplicationPage.css";

type CreateApplicationForm = {
  title: string;
  departmentId: string;
  workTypeId: string;
  deadlineAt: string;
  description: string;
  font: string;
  files: File[];
};

type CreateApplicationErrors = Partial<Record<keyof CreateApplicationForm, string>>;

export function CreateApplicationPage() {
  const navigate = useNavigate();
  const { currentUser } = useAuth();
  const { applicationItems, addApplication } = useApplicationsStore();
  const [form, setForm] = useState<CreateApplicationForm>({
    title: "",
    departmentId: departments[0]?.id ?? "",
    workTypeId: workTypes.find((workType) => workType.departmentId === departments[0]?.id)?.id ?? "",
    deadlineAt: "",
    description: "",
    font: "system",
    files: [],
  });
  const [errors, setErrors] = useState<CreateApplicationErrors>({});
  const [createdApplicationId, setCreatedApplicationId] = useState("");

  const availableWorkTypes = useMemo(
    () => workTypes.filter((workType) => workType.departmentId === form.departmentId),
    [form.departmentId],
  );
  const authorJobTitle = jobTitles.find((jobTitle) => jobTitle.id === currentUser?.jobTitleId);
  const authorDepartment = departments.find((department) => department.id === currentUser?.departmentId);

  const updateField = <Key extends keyof CreateApplicationForm>(field: Key, value: CreateApplicationForm[Key]) => {
    setForm((current) => ({ ...current, [field]: value }));
    setErrors((current) => ({ ...current, [field]: undefined }));
    setCreatedApplicationId("");
  };

  const validate = () => {
    const nextErrors: CreateApplicationErrors = {};

    if (!form.title.trim()) {
      nextErrors.title = "Укажите тему заявки.";
    }

    if (!form.departmentId) {
      nextErrors.departmentId = "Выберите отдел.";
    }

    if (!form.workTypeId) {
      nextErrors.workTypeId = "Выберите вид работ.";
    }

    if (!form.deadlineAt) {
      nextErrors.deadlineAt = "Укажите срок исполнения.";
    } else if (Number.isNaN(new Date(form.deadlineAt).getTime())) {
      nextErrors.deadlineAt = "Укажите корректный срок исполнения.";
    } else if (new Date(form.deadlineAt).getTime() <= Date.now()) {
      nextErrors.deadlineAt = "Срок исполнения должен быть в будущем.";
    }

    if (!form.description.trim()) {
      nextErrors.description = "Опишите проблему.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate()) {
      return;
    }

    if (!currentUser) {
      return;
    }

    const applicationId = String(getNextApplicationId(applicationItems));
    const now = new Date().toISOString();
    const createdApplication: Application = {
      id: applicationId,
      title: form.title.trim(),
      description: form.description.trim(),
      status: "new",
      priority: "medium",
      departmentId: form.departmentId,
      workTypeId: form.workTypeId,
      authorId: currentUser.id,
      attachmentNames: form.files.map((file) => file.name),
      isUnfinished: false,
      createdAt: now,
      deadlineAt: new Date(form.deadlineAt).toISOString(),
      updatedAt: now,
    };

    addApplication(createdApplication);
    setCreatedApplicationId(createdApplication.id);
  };

  return (
    <section className="create-application-page">
      <form className="create-window" onSubmit={handleSubmit} noValidate>
        <header className="create-window__header">
          <h1>Форма для создания заявки</h1>
          <button type="button" onClick={() => navigate("/")} aria-label="Закрыть">×</button>
        </header>

        {createdApplicationId ? (
          <div className="create-window__success">
            Заявка ID {createdApplicationId} создана в mock-режиме и получит статус «Новый».
            <button type="button" onClick={() => navigate(`/applications?application=${createdApplicationId}`)}>
              Открыть просмотр заявок
            </button>
          </div>
        ) : null}

        <div className="create-window__author">
          <span>Автор: <b>{currentUser?.fullName}</b></span>
          <span>Отдел: <b>{authorDepartment?.name ?? "-"}</b></span>
          <span>Должность: <b>{authorJobTitle?.name ?? "-"}</b></span>
        </div>

        <div className="create-window__row create-window__row--topic">
          <label>
            Тема:
            <input
              value={form.title}
              onChange={(event) => updateField("title", event.target.value)}
              aria-label="Тема"
              placeholder="Кратко опишите проблему"
            />
          </label>
          {errors.title ? <span className="create-window__error">{errors.title}</span> : null}
        </div>

        <label className="create-window__select-row">
          <span>Отдел:</span>
          <select
            value={form.departmentId}
            onChange={(event) => {
              const nextDepartmentId = event.target.value;
              const nextWorkTypeId = workTypes.find((workType) => workType.departmentId === nextDepartmentId)?.id ?? "";
              setForm((current) => ({ ...current, departmentId: nextDepartmentId, workTypeId: nextWorkTypeId }));
              setErrors((current) => ({ ...current, departmentId: undefined, workTypeId: undefined }));
              setCreatedApplicationId("");
            }}
            aria-label="Отдел"
          >
            {departments.map((department) => (
              <option value={department.id} key={department.id}>
                {department.name}
              </option>
            ))}
          </select>
          {errors.departmentId ? <small>{errors.departmentId}</small> : null}
        </label>

        <label className="create-window__select-row">
          <span>Вид работ:</span>
          <select
            value={form.workTypeId}
            onChange={(event) => updateField("workTypeId", event.target.value)}
            aria-label="Вид работ"
          >
            {availableWorkTypes.map((workType) => (
              <option value={workType.id} key={workType.id}>
                {workType.name}
              </option>
            ))}
          </select>
          {errors.workTypeId ? <small>{errors.workTypeId}</small> : null}
        </label>

        <label className="create-window__deadline">
          Срок исполнения:
          <input
            type="datetime-local"
            value={form.deadlineAt}
            onChange={(event) => updateField("deadlineAt", event.target.value)}
            aria-label="Срок исполнения"
          />
          {errors.deadlineAt ? <small>{errors.deadlineAt}</small> : null}
        </label>

        <div className="create-window__description">
          <div className="create-window__description-header">
            <label htmlFor="application-description">Описание проблемы</label>
            <span>{form.description.length}/1000</span>
          </div>
          <textarea
            id="application-description"
            value={form.description}
            onChange={(event) => updateField("description", event.target.value)}
            maxLength={1000}
            placeholder="Опишите, что произошло, где находится оборудование и какие признаки неисправности заметили."
            style={{ fontFamily: form.font === "serif" ? "Georgia, serif" : undefined }}
          />
          {errors.description ? <span className="create-window__error">{errors.description}</span> : null}
        </div>

        <div className="create-window__toolbar">
          <select
            value={form.font}
            onChange={(event) => updateField("font", event.target.value)}
            aria-label="Шрифт описания"
          >
            <option value="system">Aa</option>
            <option value="serif">Serif</option>
          </select>
          <label aria-label="Прикрепить файлы">
            <span>{form.files.length > 0 ? `Файлы: ${form.files.length}` : "Прикрепить файлы"}</span>
            <input
              type="file"
              multiple
              onChange={(event) => updateField("files", Array.from(event.target.files ?? []))}
            />
          </label>
        </div>

        <footer className="create-window__footer">
          <Button type="submit" variant="ghost" disabled={Boolean(createdApplicationId)}>
            Отправить
          </Button>
        </footer>
      </form>
    </section>
  );
}

function getNextApplicationId(applicationItems: Application[]) {
  const maxId = applicationItems.reduce((max, application) => {
    const numericPart = Number(application.id);

    return Number.isFinite(numericPart) ? Math.max(max, numericPart) : max;
  }, 1000);

  return maxId + 1;
}
