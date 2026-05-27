import { FormEvent, useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { departments, mockUsers } from "@mocks/mockData";
import type { Position, User } from "@shared/model/domain";
import { roleLabels } from "@shared/model/labels";
import { Button } from "@shared/ui";

import "./EmployeesPage.css";

type AdUser = Pick<User, "id" | "login" | "fullName" | "departmentId"> & {
  adPostName: string;
};

type EmployeeForm = {
  adUserId: string;
  positionId: string;
  isActive: boolean;
};

type EmployeeErrors = Partial<Record<keyof EmployeeForm, string>>;
type ActivityFilter = "all" | "active" | "inactive";

const initialPositions: Position[] = [
  { id: "grade-junior", name: "Младший", isTop: false },
  { id: "grade-senior", name: "Старший", isTop: false },
  { id: "grade-lead", name: "Ведущий", isTop: true },
  { id: "grade-chief", name: "Главный", isTop: true },
];

const initialEmployees: User[] = mockUsers.map((user) => ({
  ...user,
  positionId: getInitialPositionId(user.login),
  isActive: user.role === "manager" ? false : user.isActive,
}));

const adUsers: AdUser[] = [
  ...mockUsers.map((user) => ({
    id: user.id,
    login: user.login,
    fullName: user.fullName,
    departmentId: user.departmentId,
    adPostName: getMockAdPostName(user.login),
  })),
  {
    id: "ad-user-5",
    login: "nikitin_av",
    fullName: "Никитин Алексей Викторович",
    departmentId: "it",
    adPostName: "Инженер",
  },
  {
    id: "ad-user-6",
    login: "sokolova_ev",
    fullName: "Соколова Елена Викторовна",
    departmentId: "oge",
    adPostName: "Специалист",
  },
];

export function EmployeesPage() {
  const { currentUser } = useAuth();
  const initialDepartmentId = currentUser?.role === "manager" ? currentUser.departmentId : departments[0]?.id ?? "";
  const [employees, setEmployees] = useState<User[]>(initialEmployees);
  const [departmentId, setDepartmentId] = useState(initialDepartmentId);
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("all");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<EmployeeForm>({
    adUserId: "",
    positionId: initialPositions[0]?.id ?? "",
    isActive: true,
  });
  const [errors, setErrors] = useState<EmployeeErrors>({});

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

  const employeeLogins = new Set(employees.map((employee) => employee.login));
  const availableAdUsers = adUsers.filter((user) => !employeeLogins.has(user.login));
  const selectedAdUser = adUsers.find((user) => user.id === form.adUserId);
  const department = departments.find((item) => item.id === departmentId);
  const totalInDepartment = employees.filter((employee) => employee.departmentId === departmentId).length;
  const activeInDepartment = employees.filter((employee) => employee.departmentId === departmentId && employee.isActive).length;

  const openCreateModal = () => {
    const firstAvailableAdUser = availableAdUsers[0];

    setForm({
      adUserId: firstAvailableAdUser?.id ?? "",
      positionId: initialPositions[0]?.id ?? "",
      isActive: true,
    });
    setErrors({});
    setIsModalOpen(true);
  };

  const validate = () => {
    const nextErrors: EmployeeErrors = {};

    if (!form.adUserId || !selectedAdUser) {
      nextErrors.adUserId = "Выберите пользователя из AD.";
    }

    if (selectedAdUser && employees.some((employee) => employee.login === selectedAdUser.login)) {
      nextErrors.adUserId = "Этот пользователь уже добавлен в систему.";
    }

    if (!form.positionId) {
      nextErrors.positionId = "Выберите позицию.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleCreate = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate() || !selectedAdUser) {
      return;
    }

    const createdEmployee: User = {
      id: `user-${Date.now()}`,
      login: selectedAdUser.login,
      fullName: selectedAdUser.fullName,
      role: "executor",
      departmentId: selectedAdUser.departmentId,
      positionId: form.positionId,
      isActive: form.isActive,
    };

    setEmployees((current) => [createdEmployee, ...current]);
    setDepartmentId(createdEmployee.departmentId);
    setActivityFilter("all");
    setNotice(`Пользователь AD «${createdEmployee.fullName}» добавлен в систему.`);
    setIsModalOpen(false);
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
      `Сотрудник «${employee.fullName}» ${employee.isActive ? "исключен из распределения заявок" : "доступен для распределения заявок"}.`,
    );
  };

  const updateEmployeePosition = (employee: User, positionId: string) => {
    const nextPosition = initialPositions.find((position) => position.id === positionId);

    setEmployees((current) =>
      current.map((currentEmployee) =>
        currentEmployee.id === employee.id ? { ...currentEmployee, positionId } : currentEmployee,
      ),
    );
    setNotice(`Сотруднику «${employee.fullName}» назначена позиция «${nextPosition?.name ?? "-"}».`);
  };

  return (
    <section className="employees-page">
      <header className="employees-page__header">
        <div>
          <h1>Управление сотрудниками</h1>
          <p>Руководитель добавляет из AD сотрудников-исполнителей и настраивает их позицию и участие в распределении заявок.</p>
        </div>
      </header>

      {notice ? <div className="employees-notice">{notice}</div> : null}

      <section className="employees-summary" aria-label="Сводка по сотрудникам">
        <div>
          <span>Выбранный отдел</span>
          <strong>{department?.name ?? "-"}</strong>
        </div>
        <div>
          <span>Сотрудников в системе</span>
          <strong>{totalInDepartment}</strong>
        </div>
        <div>
          <span>Принимают заявки</span>
          <strong>{activeInDepartment}</strong>
        </div>
      </section>

      <article className="employees-table">
        <header className="employees-table__toolbar">
          <label>
            Отдел AD
            <select value={departmentId} onChange={(event) => setDepartmentId(event.target.value)}>
              {departments.map((departmentItem) => (
                <option value={departmentItem.id} key={departmentItem.id}>
                  {departmentItem.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Участие в распределении
            <select value={activityFilter} onChange={(event) => setActivityFilter(event.target.value as ActivityFilter)}>
              <option value="all">Все сотрудники</option>
              <option value="active">Принимают заявки</option>
              <option value="inactive">Не принимают заявки</option>
            </select>
          </label>
          <Button type="button" onClick={openCreateModal}>
            Добавить сотрудника
          </Button>
        </header>

        <div className="employees-table__grid" role="table" aria-label="Сотрудники">
          <div className="employees-table__row employees-table__row--head" role="row">
            <span role="columnheader">ФИО из AD</span>
            <span role="columnheader">Логин AD</span>
            <span role="columnheader">Роль</span>
            <span role="columnheader">Должность AD</span>
            <span role="columnheader">Позиция</span>
            <span role="columnheader">Статус</span>
            <span role="columnheader">Действие</span>
          </div>

          {visibleEmployees.length > 0 ? (
            visibleEmployees.map((employee) => {
              return (
                <div className="employees-table__row" role="row" key={employee.id}>
                  <span role="cell">{employee.fullName}</span>
                  <span role="cell">{employee.login}</span>
                  <span role="cell">{roleLabels[employee.role]}</span>
                  <span role="cell">{getMockAdPostName(employee.login)}</span>
                  <span role="cell">
                    <select
                      className="employees-position-select"
                      value={employee.positionId}
                      onChange={(event) => updateEmployeePosition(employee, event.target.value)}
                      aria-label={`Позиция ${employee.fullName}`}
                    >
                      {initialPositions.map((position) => (
                        <option value={position.id} key={position.id}>
                          {position.name}
                        </option>
                      ))}
                    </select>
                  </span>
                  <span role="cell">
                    <span className={employee.isActive ? "employees-status employees-status--active" : "employees-status"}>
                      {employee.isActive ? "Принимает заявки" : "Не принимает"}
                    </span>
                  </span>
                  <span role="cell">
                    <button type="button" onClick={() => toggleActivity(employee)}>
                      {employee.isActive ? "Отключить" : "Включить"}
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

      {isModalOpen ? (
        <div className="employees-modal" role="dialog" aria-modal="true" aria-label="Добавление сотрудника из AD">
          <form className="employees-modal__panel" onSubmit={handleCreate} noValidate>
            <header>
              <h2>Добавить сотрудника из AD</h2>
              <button type="button" onClick={() => setIsModalOpen(false)} aria-label="Закрыть">
                ×
              </button>
            </header>

            {availableAdUsers.length > 0 ? (
              <>
                <label>
                  Пользователь AD
                  <select
                    value={form.adUserId}
                    onChange={(event) => {
                      setForm((current) => ({ ...current, adUserId: event.target.value }));
                      setErrors((current) => ({ ...current, adUserId: undefined }));
                    }}
                  >
                    {availableAdUsers.map((user) => (
                      <option value={user.id} key={user.id}>
                        {user.fullName} · {user.login}
                      </option>
                    ))}
                  </select>
                  {errors.adUserId ? <small>{errors.adUserId}</small> : null}
                </label>

                <div className="employees-ad-card" aria-label="Данные из AD">
                  <div>
                    <span>Отдел AD</span>
                    <strong>{getDepartmentName(selectedAdUser?.departmentId)}</strong>
                  </div>
                  <div>
                    <span>Должность AD</span>
                    <strong>{selectedAdUser?.adPostName ?? "-"}</strong>
                  </div>
                </div>

                <div className="employees-ad-card" aria-label="Параметры системы">
                  <div>
                    <span>Роль в системе</span>
                    <strong>Исполнитель</strong>
                  </div>
                  <div>
                    <span>Участие в распределении</span>
                    <strong>{form.isActive ? "Принимает заявки" : "Не принимает заявки"}</strong>
                  </div>
                </div>

                <label>
                  Участие в распределении
                  <select
                    value={form.isActive ? "active" : "inactive"}
                    onChange={(event) => setForm((current) => ({ ...current, isActive: event.target.value === "active" }))}
                  >
                    <option value="active">Принимает заявки</option>
                    <option value="inactive">Не принимает заявки</option>
                  </select>
                </label>

                <label>
                  Позиция
                  <select
                    value={form.positionId}
                    onChange={(event) => {
                      setForm((current) => ({ ...current, positionId: event.target.value }));
                      setErrors((current) => ({ ...current, positionId: undefined }));
                    }}
                  >
                    {initialPositions.map((position) => (
                      <option value={position.id} key={position.id}>
                        {position.name}
                      </option>
                    ))}
                  </select>
                  {errors.positionId ? <small>{errors.positionId}</small> : null}
                </label>
              </>
            ) : (
              <div className="employees-table__empty">Все mock-пользователи AD уже добавлены в систему.</div>
            )}

            <footer>
              <button type="button" onClick={() => setIsModalOpen(false)}>
                Отмена
              </button>
              <button type="submit" disabled={availableAdUsers.length === 0}>Добавить</button>
            </footer>
          </form>
        </div>
      ) : null}

    </section>
  );
}

function getDepartmentName(departmentId?: string) {
  return departments.find((department) => department.id === departmentId)?.name ?? "-";
}

function getMockAdPostName(login: string) {
  const adPosts: Record<string, string> = {
    author: "Инженер",
    executor: "Инженер",
    manager: "Руководитель",
    executor2: "Инженер",
  };

  return adPosts[login] ?? "Специалист";
}

function getInitialPositionId(login: string) {
  const positionByLogin: Record<string, string> = {
    author: "grade-junior",
    executor: "grade-lead",
    manager: "grade-chief",
    executor2: "grade-senior",
  };

  return positionByLogin[login] ?? "grade-junior";
}
