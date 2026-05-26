import { FormEvent, useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { departments, mockUsers, positions as initialPositions } from "@mocks/mockData";
import type { Position, User, UserRole } from "@shared/model/domain";
import { roleLabels } from "@shared/model/labels";
import { Button } from "@shared/ui";

import "./EmployeesPage.css";

type EmployeeForm = {
  fullName: string;
  login: string;
  role: UserRole;
  departmentId: string;
  positionId: string;
  isActive: boolean;
};

type EmployeeErrors = Partial<Record<keyof EmployeeForm, string>>;
type ActivityFilter = "all" | "active" | "inactive";
type PositionForm = {
  name: string;
  isTop: boolean;
};
type PositionErrors = Partial<Record<keyof PositionForm, string>>;

export function EmployeesPage() {
  const { currentUser } = useAuth();
  const initialDepartmentId = currentUser?.role === "manager" ? currentUser.departmentId : departments[0]?.id ?? "";
  const [employees, setEmployees] = useState<User[]>(mockUsers);
  const [positions, setPositions] = useState<Position[]>(initialPositions);
  const [departmentId, setDepartmentId] = useState(initialDepartmentId);
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("all");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isPositionModalOpen, setIsPositionModalOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<EmployeeForm>({
    fullName: "",
    login: "",
    role: "executor",
    departmentId: initialDepartmentId,
    positionId: initialPositions[0]?.id ?? "",
    isActive: true,
  });
  const [errors, setErrors] = useState<EmployeeErrors>({});
  const [positionForm, setPositionForm] = useState<PositionForm>({
    name: "",
    isTop: false,
  });
  const [positionErrors, setPositionErrors] = useState<PositionErrors>({});

  const visibleEmployees = useMemo(
    () =>
      employees.filter((employee) => {
        const matchesDepartment = employee.departmentId === departmentId;
        const matchesActivity =
          activityFilter === "all" ||
          (activityFilter === "active" && employee.isActive) ||
          (activityFilter === "inactive" && !employee.isActive);

        return matchesDepartment && matchesActivity;
      }),
    [activityFilter, departmentId, employees],
  );

  const department = departments.find((item) => item.id === departmentId);
  const totalInDepartment = employees.filter((employee) => employee.departmentId === departmentId).length;
  const activeInDepartment = employees.filter((employee) => employee.departmentId === departmentId && employee.isActive).length;
  const positionsWithUsage = positions.map((position) => ({
    ...position,
    usageCount: employees.filter((employee) => employee.positionId === position.id).length,
  }));

  const openCreateModal = () => {
    setForm({
      fullName: "",
      login: "",
      role: "executor",
      departmentId,
      positionId: positions[0]?.id ?? "",
      isActive: true,
    });
    setErrors({});
    setIsModalOpen(true);
  };

  const openPositionModal = () => {
    setPositionForm({ name: "", isTop: false });
    setPositionErrors({});
    setIsPositionModalOpen(true);
  };

  const validate = () => {
    const nextErrors: EmployeeErrors = {};

    if (!form.fullName.trim()) {
      nextErrors.fullName = "Укажите ФИО сотрудника.";
    }

    if (!form.login.trim()) {
      nextErrors.login = "Укажите логин.";
    }

    if (employees.some((employee) => employee.login.toLowerCase() === form.login.trim().toLowerCase())) {
      nextErrors.login = "Такой логин уже используется.";
    }

    if (!form.departmentId) {
      nextErrors.departmentId = "Выберите отдел.";
    }

    if (!form.positionId) {
      nextErrors.positionId = "Выберите должность.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate()) {
      return;
    }

    const createdEmployee: User = {
      id: `user-${Date.now()}`,
      login: form.login.trim(),
      fullName: form.fullName.trim(),
      role: form.role,
      departmentId: form.departmentId,
      positionId: form.positionId,
      isActive: form.isActive,
    };

    setEmployees((current) => [createdEmployee, ...current]);
    setDepartmentId(form.departmentId);
    setActivityFilter("all");
    setNotice(`Сотрудник «${createdEmployee.fullName}» добавлен в mock-справочник.`);
    setIsModalOpen(false);
  };

  const validatePosition = () => {
    const nextErrors: PositionErrors = {};

    if (!positionForm.name.trim()) {
      nextErrors.name = "Укажите название должности.";
    }

    if (positions.some((position) => position.name.trim().toLowerCase() === positionForm.name.trim().toLowerCase())) {
      nextErrors.name = "Такая должность уже есть.";
    }

    setPositionErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleCreatePosition = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validatePosition()) {
      return;
    }

    const createdPosition: Position = {
      id: `position-${Date.now()}`,
      name: positionForm.name.trim(),
      isTop: positionForm.isTop,
    };

    setPositions((current) => [createdPosition, ...current]);
    setForm((current) => ({ ...current, positionId: current.positionId || createdPosition.id }));
    setNotice(`Должность «${createdPosition.name}» добавлена в mock-справочник.`);
    setIsPositionModalOpen(false);
  };

  const toggleActivity = (employee: User) => {
    setEmployees((current) =>
      current.map((currentEmployee) =>
        currentEmployee.id === employee.id
          ? { ...currentEmployee, isActive: !currentEmployee.isActive }
          : currentEmployee,
      ),
    );
    setNotice(
      `Сотрудник «${employee.fullName}» отмечен как ${employee.isActive ? "неактивный" : "активный"}.`,
    );
  };

  return (
    <section className="employees-page">
      <header className="employees-page__header">
        <div>
          <h1>Управление сотрудниками</h1>
          <p>Mock-справочник сотрудников отдела с ролями, должностями и признаком активности.</p>
        </div>
        <Button type="button" onClick={openCreateModal}>
          Добавить сотрудника
        </Button>
      </header>

      {notice ? <div className="employees-notice">{notice}</div> : null}

      <section className="employees-summary" aria-label="Сводка по сотрудникам">
        <div>
          <span>Выбранный отдел</span>
          <strong>{department?.name ?? "-"}</strong>
        </div>
        <div>
          <span>Всего сотрудников</span>
          <strong>{totalInDepartment}</strong>
        </div>
        <div>
          <span>Активны</span>
          <strong>{activeInDepartment}</strong>
        </div>
      </section>

      <article className="employees-table">
        <header className="employees-table__toolbar">
          <label>
            Отдел
            <select value={departmentId} onChange={(event) => setDepartmentId(event.target.value)}>
              {departments.map((departmentItem) => (
                <option value={departmentItem.id} key={departmentItem.id}>
                  {departmentItem.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Активность
            <select value={activityFilter} onChange={(event) => setActivityFilter(event.target.value as ActivityFilter)}>
              <option value="all">Все сотрудники</option>
              <option value="active">Только активные</option>
              <option value="inactive">Только неактивные</option>
            </select>
          </label>
        </header>

        <div className="employees-table__grid" role="table" aria-label="Сотрудники">
          <div className="employees-table__row employees-table__row--head" role="row">
            <span role="columnheader">ФИО</span>
            <span role="columnheader">Логин</span>
            <span role="columnheader">Роль</span>
            <span role="columnheader">Должность</span>
            <span role="columnheader">Статус</span>
            <span role="columnheader">Действие</span>
          </div>

          {visibleEmployees.length > 0 ? (
            visibleEmployees.map((employee) => {
              const position = positions.find((item) => item.id === employee.positionId);

              return (
                <div className="employees-table__row" role="row" key={employee.id}>
                  <span role="cell">{employee.fullName}</span>
                  <span role="cell">{employee.login}</span>
                  <span role="cell">{roleLabels[employee.role]}</span>
                  <span role="cell">{position?.name ?? "-"}</span>
                  <span role="cell">
                    <span className={employee.isActive ? "employees-status employees-status--active" : "employees-status"}>
                      {employee.isActive ? "Активен" : "Неактивен"}
                    </span>
                  </span>
                  <span role="cell">
                    <button type="button" onClick={() => toggleActivity(employee)}>
                      {employee.isActive ? "Деактивировать" : "Активировать"}
                    </button>
                  </span>
                </div>
              );
            })
          ) : (
            <div className="employees-table__empty">Сотрудники по выбранным фильтрам не найдены.</div>
          )}
        </div>
      </article>

      <article className="positions-table">
        <header>
          <div>
            <h2>Должности</h2>
            <span>{positions.length} записей в справочнике</span>
          </div>
          <Button type="button" variant="secondary" onClick={openPositionModal}>
            Добавить должность
          </Button>
        </header>

        <div className="positions-table__grid" role="table" aria-label="Должности">
          <div className="positions-table__row positions-table__row--head" role="row">
            <span role="columnheader">Название</span>
            <span role="columnheader">Тип</span>
            <span role="columnheader">Сотрудников</span>
          </div>

          {positionsWithUsage.map((position) => (
            <div className="positions-table__row" role="row" key={position.id}>
              <span role="cell">{position.name}</span>
              <span role="cell">
                <span className={position.isTop ? "position-type position-type--top" : "position-type"}>
                  {position.isTop ? "Руководящая" : "Обычная"}
                </span>
              </span>
              <span role="cell">{position.usageCount}</span>
            </div>
          ))}
        </div>
      </article>

      {isModalOpen ? (
        <div className="employees-modal" role="dialog" aria-modal="true" aria-label="Создание сотрудника">
          <form className="employees-modal__panel" onSubmit={handleCreate} noValidate>
            <header>
              <h2>Новый сотрудник</h2>
              <button type="button" onClick={() => setIsModalOpen(false)} aria-label="Закрыть">
                ×
              </button>
            </header>

            <label>
              ФИО
              <input
                value={form.fullName}
                onChange={(event) => {
                  setForm((current) => ({ ...current, fullName: event.target.value }));
                  setErrors((current) => ({ ...current, fullName: undefined }));
                }}
                placeholder="Иванов Иван Иванович"
              />
              {errors.fullName ? <small>{errors.fullName}</small> : null}
            </label>

            <label>
              Логин
              <input
                value={form.login}
                onChange={(event) => {
                  setForm((current) => ({ ...current, login: event.target.value }));
                  setErrors((current) => ({ ...current, login: undefined }));
                }}
                placeholder="ivanov"
              />
              {errors.login ? <small>{errors.login}</small> : null}
            </label>

            <div className="employees-modal__grid">
              <label>
                Роль
                <select
                  value={form.role}
                  onChange={(event) => setForm((current) => ({ ...current, role: event.target.value as UserRole }))}
                >
                  <option value="author">Автор</option>
                  <option value="executor">Исполнитель</option>
                  <option value="manager">Руководитель</option>
                </select>
              </label>

              <label>
                Активность
                <select
                  value={form.isActive ? "active" : "inactive"}
                  onChange={(event) => setForm((current) => ({ ...current, isActive: event.target.value === "active" }))}
                >
                  <option value="active">Активен</option>
                  <option value="inactive">Неактивен</option>
                </select>
              </label>
            </div>

            <label>
              Отдел
              <select
                value={form.departmentId}
                onChange={(event) => {
                  setForm((current) => ({ ...current, departmentId: event.target.value }));
                  setErrors((current) => ({ ...current, departmentId: undefined }));
                }}
              >
                {departments.map((departmentItem) => (
                  <option value={departmentItem.id} key={departmentItem.id}>
                    {departmentItem.name}
                  </option>
                ))}
              </select>
              {errors.departmentId ? <small>{errors.departmentId}</small> : null}
            </label>

            <label>
              Должность
              <select
                value={form.positionId}
                onChange={(event) => {
                  setForm((current) => ({ ...current, positionId: event.target.value }));
                  setErrors((current) => ({ ...current, positionId: undefined }));
                }}
              >
                {positions.map((position) => (
                  <option value={position.id} key={position.id}>
                    {position.name}
                  </option>
                ))}
              </select>
              {errors.positionId ? <small>{errors.positionId}</small> : null}
            </label>

            <footer>
              <button type="button" onClick={() => setIsModalOpen(false)}>
                Отмена
              </button>
              <button type="submit">Создать</button>
            </footer>
          </form>
        </div>
      ) : null}

      {isPositionModalOpen ? (
        <div className="employees-modal" role="dialog" aria-modal="true" aria-label="Создание должности">
          <form className="employees-modal__panel" onSubmit={handleCreatePosition} noValidate>
            <header>
              <h2>Новая должность</h2>
              <button type="button" onClick={() => setIsPositionModalOpen(false)} aria-label="Закрыть">
                ×
              </button>
            </header>

            <label>
              Название
              <input
                value={positionForm.name}
                onChange={(event) => {
                  setPositionForm((current) => ({ ...current, name: event.target.value }));
                  setPositionErrors((current) => ({ ...current, name: undefined }));
                }}
                placeholder="Например, мастер смены"
              />
              {positionErrors.name ? <small>{positionErrors.name}</small> : null}
            </label>

            <label>
              Тип должности
              <select
                value={positionForm.isTop ? "top" : "regular"}
                onChange={(event) => setPositionForm((current) => ({ ...current, isTop: event.target.value === "top" }))}
              >
                <option value="regular">Обычная</option>
                <option value="top">Руководящая</option>
              </select>
            </label>

            <footer>
              <button type="button" onClick={() => setIsPositionModalOpen(false)}>
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
