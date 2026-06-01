import { FormEvent, useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { departments, grades, workTypes } from "@mocks/mockData";
import type { Complexity, WorkType } from "@shared/model/domain";
import { Button } from "@shared/ui";

import "./WorkTypesPage.css";

type WorkTypeForm = {
  name: string;
  departmentId: string;
  complexity: Complexity;
  allowedGradeIds: string[];
};

type WorkTypeErrors = Partial<Record<keyof WorkTypeForm, string>>;

const complexityLabels: Record<Complexity, string> = {
  easy: "Легкая",
  medium: "Средняя",
  hard: "Высокая",
  critical: "Критичная",
};

const defaultAllowedGradeIdsByComplexity: Record<Complexity, string[]> = {
  easy: ["junior", "middle", "senior", "lead"],
  medium: ["junior", "middle", "senior", "lead"],
  hard: ["senior", "lead"],
  critical: ["lead"],
};

export function WorkTypesPage() {
  const { currentUser } = useAuth();
  const availableDepartments = currentUser?.role === "manager"
    ? departments.filter((department) => department.id === currentUser.departmentId)
    : departments;
  const initialDepartmentId = availableDepartments[0]?.id ?? "";
  const [items, setItems] = useState<WorkType[]>(workTypes);
  const [departmentId, setDepartmentId] = useState(initialDepartmentId);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [form, setForm] = useState<WorkTypeForm>({
    name: "",
    departmentId: initialDepartmentId,
    complexity: "medium",
    allowedGradeIds: defaultAllowedGradeIdsByComplexity.medium,
  });
  const [errors, setErrors] = useState<WorkTypeErrors>({});
  const [notice, setNotice] = useState("");

  const selectedDepartment = departments.find((department) => department.id === departmentId);
  const visibleItems = useMemo(
    () => items.filter((item) => item.departmentId === departmentId),
    [departmentId, items],
  );

  const departmentStats = useMemo(
    () =>
      departments.map((department) => ({
        ...department,
        workTypesCount: items.filter((item) => item.departmentId === department.id).length,
      })).filter((department) => availableDepartments.some((availableDepartment) => availableDepartment.id === department.id)),
    [availableDepartments, items],
  );

  const openCreateModal = () => {
    setForm({ name: "", departmentId, complexity: "medium", allowedGradeIds: defaultAllowedGradeIdsByComplexity.medium });
    setErrors({});
    setIsModalOpen(true);
  };

  const validate = () => {
    const nextErrors: WorkTypeErrors = {};

    if (!form.name.trim()) {
      nextErrors.name = "Укажите название вида работ.";
    }

    if (!form.departmentId) {
      nextErrors.departmentId = "Выберите отдел.";
    }

    if (items.some((item) => item.departmentId === form.departmentId && item.name.trim().toLowerCase() === form.name.trim().toLowerCase())) {
      nextErrors.name = "Такой вид работ уже есть в выбранном отделе.";
    }

    if (form.allowedGradeIds.length === 0) {
      nextErrors.complexity = "Выберите хотя бы одну допустимую позицию.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate()) {
      return;
    }

    const createdItem: WorkType = {
      id: `work-type-${Date.now()}`,
      name: form.name.trim(),
      departmentId: form.departmentId,
      complexity: form.complexity,
      allowedGradeIds: form.allowedGradeIds,
    };

    setItems((current) => [createdItem, ...current]);
    setDepartmentId(form.departmentId);
    setNotice(`Вид работ «${createdItem.name}» добавлен в mock-справочник.`);
    setIsModalOpen(false);
  };

  const handleDelete = (item: WorkType) => {
    setItems((current) => current.filter((currentItem) => currentItem.id !== item.id));
    setNotice(`Вид работ «${item.name}» удален из mock-справочника.`);
  };

  const updateWorkTypeComplexity = (item: WorkType, complexity: Complexity) => {
    setItems((current) =>
      current.map((currentItem) =>
        currentItem.id === item.id
          ? { ...currentItem, complexity, allowedGradeIds: defaultAllowedGradeIdsByComplexity[complexity] }
          : currentItem,
      ),
    );
    setNotice(`Для вида работ «${item.name}» обновлены сложность и допустимые позиции.`);
  };

  const toggleWorkTypeGrade = (item: WorkType, gradeId: string) => {
    const nextGradeIds = item.allowedGradeIds.includes(gradeId)
      ? item.allowedGradeIds.filter((id) => id !== gradeId)
      : [...item.allowedGradeIds, gradeId];

    if (nextGradeIds.length === 0) {
      setNotice("У вида работ должна остаться хотя бы одна допустимая позиция.");
      return;
    }

    setItems((current) =>
      current.map((currentItem) =>
        currentItem.id === item.id ? { ...currentItem, allowedGradeIds: nextGradeIds } : currentItem,
      ),
    );
    setNotice(`Матрица позиций для вида работ «${item.name}» обновлена.`);
  };

  return (
    <section className="work-types-page">
      <header className="work-types-page__header">
        <div>
          <h1>Виды работ</h1>
          <p>Справочник работ по отделам для маршрутизации заявок и расчета сложности.</p>
        </div>
      </header>

      {notice ? <div className="work-types-notice">{notice}</div> : null}

      <div className="work-types-layout">
        <aside className="work-types-departments" aria-label="Отделы">
          {departmentStats.map((department) => (
            <button
              className={department.id === departmentId ? "work-types-department work-types-department--active" : "work-types-department"}
              type="button"
              key={department.id}
              onClick={() => setDepartmentId(department.id)}
            >
              <span>{department.name}</span>
              <strong>{department.workTypesCount}</strong>
            </button>
          ))}
        </aside>

        <article className="work-types-table">
          <header className="work-types-table__header">
            <div>
              <h2>{selectedDepartment?.name ?? "Отдел не выбран"}</h2>
              <span>{visibleItems.length} видов работ</span>
            </div>
            <label>
              Отдел
              <select value={departmentId} onChange={(event) => setDepartmentId(event.target.value)}>
                {availableDepartments.map((department) => (
                  <option value={department.id} key={department.id}>
                    {department.name}
                  </option>
                ))}
              </select>
            </label>
            <Button type="button" onClick={openCreateModal}>
              Добавить вид работ
            </Button>
          </header>

          <div className="work-types-table__grid" role="table" aria-label="Виды работ">
            <div className="work-types-table__row work-types-table__row--head" role="row">
              <span role="columnheader">Название</span>
              <span role="columnheader">Сложность</span>
              <span role="columnheader">Допустимые позиции</span>
              <span role="columnheader">Использование</span>
              <span role="columnheader">Действия</span>
            </div>

            {visibleItems.length > 0 ? (
              visibleItems.map((item) => (
                <div className="work-types-table__row" role="row" key={item.id}>
                  <span role="cell">{item.name}</span>
                  <span role="cell">
                    <select
                      className={`work-types-complexity-select work-types-complexity-select--${item.complexity}`}
                      value={item.complexity}
                      onChange={(event) => updateWorkTypeComplexity(item, event.target.value as Complexity)}
                      aria-label={`Сложность ${item.name}`}
                    >
                      {Object.entries(complexityLabels).map(([value, label]) => (
                        <option value={value} key={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </span>
                  <span role="cell">
                    <span className="work-types-grades">
                      {grades.map((grade) => (
                        <label key={grade.id}>
                          <input
                            type="checkbox"
                            checked={item.allowedGradeIds.includes(grade.id)}
                            onChange={() => toggleWorkTypeGrade(item, grade.id)}
                          />
                          {grade.name}
                        </label>
                      ))}
                    </span>
                  </span>
                  <span role="cell">Доступен для новых заявок</span>
                  <span role="cell">
                    <button type="button" onClick={() => handleDelete(item)}>
                      Удалить
                    </button>
                  </span>
                </div>
              ))
            ) : (
              <div className="work-types-table__empty">Для выбранного отдела пока нет видов работ.</div>
            )}
          </div>
        </article>
      </div>

      {isModalOpen ? (
        <div className="work-types-modal" role="dialog" aria-modal="true" aria-label="Создание вида работ">
          <form className="work-types-modal__panel" onSubmit={handleCreate} noValidate>
            <header>
              <h2>Новый вид работ</h2>
              <button type="button" onClick={() => setIsModalOpen(false)} aria-label="Закрыть">
                ×
              </button>
            </header>

            <label>
              Название
              <input
                value={form.name}
                onChange={(event) => {
                  setForm((current) => ({ ...current, name: event.target.value }));
                  setErrors((current) => ({ ...current, name: undefined }));
                }}
                placeholder="Например, настройка промышленного терминала"
              />
              {errors.name ? <small>{errors.name}</small> : null}
            </label>

            <label>
              Отдел
              <select
                value={form.departmentId}
                onChange={(event) => {
                  setForm((current) => ({ ...current, departmentId: event.target.value }));
                  setErrors((current) => ({ ...current, departmentId: undefined }));
                }}
              >
                {availableDepartments.map((department) => (
                  <option value={department.id} key={department.id}>
                    {department.name}
                  </option>
                ))}
              </select>
              {errors.departmentId ? <small>{errors.departmentId}</small> : null}
            </label>

            <label>
              Сложность
              <select
                value={form.complexity}
                onChange={(event) => {
                  const complexity = event.target.value as Complexity;

                  setForm((current) => ({
                    ...current,
                    complexity,
                    allowedGradeIds: defaultAllowedGradeIdsByComplexity[complexity],
                  }));
                }}
              >
                {Object.entries(complexityLabels).map(([value, label]) => (
                  <option value={value} key={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>

            <div className="work-types-matrix-preview" aria-label="Допустимые позиции">
              <span>Допустимые позиции</span>
              <div className="work-types-grade-checks">
                {grades.map((grade) => (
                  <label key={grade.id}>
                    <input
                      type="checkbox"
                      checked={form.allowedGradeIds.includes(grade.id)}
                      onChange={(event) => {
                        setForm((current) => ({
                          ...current,
                          allowedGradeIds: event.target.checked
                            ? [...current.allowedGradeIds, grade.id]
                            : current.allowedGradeIds.filter((id) => id !== grade.id),
                        }));
                        setErrors((current) => ({ ...current, complexity: undefined }));
                      }}
                    />
                    {grade.name}
                  </label>
                ))}
              </div>
              {errors.complexity ? <small>{errors.complexity}</small> : null}
            </div>

            <footer>
              <button type="button" onClick={() => setIsModalOpen(false)}>
                Отмена
              </button>
              <button type="submit">Создать</button>
            </footer>
          </form>
        </div>
      ) : null}
    </section>
  );
}
