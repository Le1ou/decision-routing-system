import { FormEvent, useEffect, useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { useReferenceData } from "@app/providers/ReferenceDataProvider";
import { apiClient } from "@shared/api";
import type { Complexity, WorkType } from "@shared/model/domain";
import { Button } from "@shared/ui";

import "./WorkTypesPage.css";
import {
  createDefaultMatrix,
  getMatrixSelectionCount,
  getPositionGradeSummary,
  hydrateWorkTypeMatrix,
  toWorkTypeAccessPayload,
  toggleMatrixGrade,
  type WorkTypeMatrix,
} from "./workTypeMatrix";

type WorkTypeForm = {
  name: string;
  departmentId: string;
  complexity: Complexity;
  matrix: WorkTypeMatrix;
};

type WorkTypeErrors = Partial<Record<keyof WorkTypeForm, string>>;

const complexityLabels: Record<Complexity, string> = {
  easy: "Легкая",
  medium: "Средняя",
  hard: "Высокая",
};

export function WorkTypesPage() {
  const { currentUser, credentials } = useAuth();
  const { departments, grades, positions, workTypes, refresh } = useReferenceData();
  const availableDepartments = currentUser?.role === "manager"
    ? departments.filter((department) => department.id === currentUser.departmentId)
    : departments;
  const initialDepartmentId = availableDepartments[0]?.id ?? "";
  const [departmentId, setDepartmentId] = useState(initialDepartmentId);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [form, setForm] = useState<WorkTypeForm>({
    name: "",
    departmentId: initialDepartmentId,
    complexity: "medium",
    matrix: createDefaultMatrix(positions, grades),
  });
  const [expandedWorkTypeIds, setExpandedWorkTypeIds] = useState<string[]>([]);
  const [errors, setErrors] = useState<WorkTypeErrors>({});
  const [notice, setNotice] = useState("");

  const activeDepartmentId = departmentId || initialDepartmentId;
  const selectedDepartment = departments.find((department) => department.id === activeDepartmentId);

  useEffect(() => {
    if (!departmentId && initialDepartmentId) {
      setDepartmentId(initialDepartmentId);
      setForm((current) => ({ ...current, departmentId: initialDepartmentId, matrix: createDefaultMatrix(positions, grades) }));
    }
  }, [departmentId, grades, initialDepartmentId, positions]);

  const visibleItems = useMemo(
    () => workTypes.filter((item) => item.departmentId === activeDepartmentId),
    [activeDepartmentId, workTypes],
  );

  const departmentStats = useMemo(
    () =>
      departments.map((department) => ({
        ...department,
        workTypesCount: workTypes.filter((item) => item.departmentId === department.id).length,
      })).filter((department) => availableDepartments.some((availableDepartment) => availableDepartment.id === department.id)),
    [availableDepartments, workTypes],
  );

  const openCreateModal = () => {
    setForm({ name: "", departmentId: activeDepartmentId, complexity: "medium", matrix: createDefaultMatrix(positions, grades) });
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

    if (workTypes.some((item) => item.departmentId === form.departmentId && item.name.trim().toLowerCase() === form.name.trim().toLowerCase())) {
      nextErrors.name = "Такой вид работ уже есть в выбранном отделе.";
    }

    if (getMatrixSelectionCount(form.matrix) === 0) {
      nextErrors.complexity = "Выберите хотя бы одну должность и позицию.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate() || !credentials) {
      return;
    }

    try {
      const accessPayload = toWorkTypeAccessPayload(form.matrix);

      await apiClient.createWorkType(credentials, {
        name: form.name.trim(),
        departmentId: form.departmentId,
        complexity: form.complexity,
        allowedGradeIds: accessPayload.allowedGradeIds,
        allowedPositionIds: accessPayload.allowedPositionIds,
      });
      await refresh();
      setDepartmentId(form.departmentId);
      setNotice(`Вид работ «${form.name.trim()}» добавлен.`);
      setIsModalOpen(false);
    } catch {
      setNotice("Не удалось создать вид работ.");
    }
  };

  const handleDelete = async (item: WorkType) => {
    if (!credentials) {
      return;
    }

    try {
      await apiClient.deleteWorkType(credentials, item.id);
      await refresh();
      setNotice(`Вид работ «${item.name}» удален.`);
    } catch {
      setNotice("Не удалось удалить вид работ.");
    }
  };

  const updateWorkTypeComplexity = async (item: WorkType, complexity: Complexity) => {
    if (!credentials) {
      return;
    }

    try {
      await apiClient.updateWorkType(credentials, item.id, { complexity });
      await refresh();
      setNotice(`Для вида работ «${item.name}» обновлена сложность.`);
    } catch {
      setNotice("Не удалось обновить сложность вида работ.");
    }
  };

  const toggleWorkTypeMatrixGrade = async (item: WorkType, positionId: string, gradeId: string) => {
    const matrix = hydrateWorkTypeMatrix(item, positions);
    const nextMatrix = toggleMatrixGrade(matrix, positionId, gradeId);
    const accessPayload = toWorkTypeAccessPayload(nextMatrix);

    if (accessPayload.allowedGradeIds.length === 0) {
      setNotice("У вида работ должна остаться хотя бы одна допустимая должность и позиция.");
      return;
    }

    if (!credentials) {
      return;
    }

    try {
      await apiClient.updateWorkType(credentials, item.id, accessPayload);
      await refresh();
      setNotice(`Матрица допуска для вида работ «${item.name}» обновлена.`);
    } catch {
      setNotice("Не удалось обновить матрицу допуска.");
    }
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
              <select value={activeDepartmentId} onChange={(event) => setDepartmentId(event.target.value)}>
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
              <span role="columnheader">Должности и позиции</span>
              <span role="columnheader">Использование</span>
              <span role="columnheader">Действия</span>
            </div>

            {visibleItems.length > 0 ? (
              visibleItems.map((item) => {
                const matrix = hydrateWorkTypeMatrix(item, positions);
                const matrixWarning = getMatrixWarning(matrix, positions);
                const isExpanded = expandedWorkTypeIds.includes(item.id);

                return (
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
                      <button
                        className="work-types-matrix-toggle"
                        type="button"
                        onClick={() =>
                          setExpandedWorkTypeIds((current) =>
                            current.includes(item.id)
                              ? current.filter((id) => id !== item.id)
                              : [...current, item.id],
                          )
                        }
                      >
                        {isExpanded ? "Скрыть матрицу" : "Показать матрицу"}
                      </button>
                      <small className="work-types-positions">
                        {getPositionGradeSummary(matrix, positions, grades)}
                      </small>
                      {isExpanded ? (
                        <WorkTypeMatrixEditor
                          grades={grades}
                          matrix={matrix}
                          positions={positions}
                          onToggle={(positionId, gradeId) => void toggleWorkTypeMatrixGrade(item, positionId, gradeId)}
                        />
                      ) : null}
                      {matrixWarning ? <small className="work-types-warning">{matrixWarning}</small> : null}
                    </span>
                    <span role="cell">Доступен для новых заявок</span>
                    <span role="cell">
                      <button type="button" onClick={() => void handleDelete(item)}>
                        Удалить
                      </button>
                    </span>
                  </div>
                );
              })
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

            <div className="work-types-matrix-preview" aria-label="Допустимые должности и позиции">
              <span>Допустимые должности и позиции</span>
              <WorkTypeMatrixEditor
                grades={grades}
                matrix={form.matrix}
                positions={positions}
                onToggle={(positionId, gradeId) => {
                  setForm((current) => ({
                    ...current,
                    matrix: toggleMatrixGrade(current.matrix, positionId, gradeId),
                  }));
                  setErrors((current) => ({ ...current, complexity: undefined }));
                }}
              />
              <small className="work-types-positions">
                {getPositionGradeSummary(form.matrix, positions, grades)}
              </small>
              {getMatrixWarning(form.matrix, positions) ? (
                <small className="work-types-warning">{getMatrixWarning(form.matrix, positions)}</small>
              ) : null}
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

function WorkTypeMatrixEditor({
  grades,
  matrix,
  positions,
  onToggle,
}: {
  grades: Array<{ id: string; name: string }>;
  matrix: WorkTypeMatrix;
  positions: Array<{ id: string; name: string; gradeIds: string[] }>;
  onToggle: (positionId: string, gradeId: string) => void;
}) {
  return (
    <div className="work-types-matrix-editor">
      {positions.map((position) => {
        const availableGradeIds = position.gradeIds.length > 0 ? position.gradeIds : grades.map((grade) => grade.id);

        return (
          <details className="work-types-position-matrix" key={position.id}>
            <summary>
              <span>{position.name}</span>
              <b>{matrix[position.id]?.length ?? 0}</b>
            </summary>
            <div className="work-types-grade-checks">
              {grades.map((grade) => (
                <label className={!availableGradeIds.includes(grade.id) ? "work-types-grade-checks__item--disabled" : ""} key={grade.id}>
                  <input
                    type="checkbox"
                    checked={(matrix[position.id] ?? []).includes(grade.id)}
                    disabled={!availableGradeIds.includes(grade.id)}
                    onChange={() => onToggle(position.id, grade.id)}
                  />
                  {grade.name}
                </label>
              ))}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function getMatrixWarning(matrix: WorkTypeMatrix, positions: Array<{ id: string; gradeIds: string[] }>) {
  const hasUnavailableGrade = positions.some((position) => {
    const availableGradeIds = new Set(position.gradeIds);

    return (matrix[position.id] ?? []).some((gradeId) => position.gradeIds.length > 0 && !availableGradeIds.has(gradeId));
  });

  return hasUnavailableGrade ? "Выбрана позиция, которой нет у должности: автоназначение может не найти исполнителя." : "";
}
