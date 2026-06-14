import { FormEvent, useMemo, useState } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { useReferenceData } from "@app/providers/ReferenceDataProvider";
import { apiClient } from "@shared/api";
import type { User, UserRole } from "@shared/model/domain";
import { roleLabels } from "@shared/model/labels";
import { Button } from "@shared/ui";

import "./EmployeesPage.css";

type EmployeeForm = {
  adUserId: string;
  role: UserRole;
  isActive: boolean;
};

type EmployeeErrors = Partial<Record<keyof EmployeeForm, string>>;
type ActivityFilter = "all" | "active" | "inactive";

const assignableRoles: UserRole[] = ["author", "executor", "manager", "top-manager"];

export function EmployeesPage() {
  const { currentUser, credentials } = useAuth();
  const { departments, employees, adUsers, refresh } = useReferenceData();
  const availableDepartments = currentUser?.role === "manager"
    ? departments.filter((department) => department.id === currentUser.departmentId)
    : departments;
  const initialDepartmentId = availableDepartments[0]?.id ?? "";
  const [departmentId, setDepartmentId] = useState(initialDepartmentId);
  const [activityFilter, setActivityFilter] = useState<ActivityFilter>("all");
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<EmployeeForm>({
    adUserId: "",
    role: "executor",
    isActive: true,
  });
  const [errors, setErrors] = useState<EmployeeErrors>({});

  const activeDepartmentId = departmentId || initialDepartmentId;
  const visibleEmployees = useMemo(
    () =>
      employees.filter((employee) => {
        const matchesDepartment = employee.departmentId === activeDepartmentId;
        const matchesActivity =
          activityFilter === "all" ||
          (activityFilter === "active" && employee.isActive) ||
          (activityFilter === "inactive" && !employee.isActive);

        return matchesDepartment && matchesActivity;
      }),
    [activityFilter, activeDepartmentId, employees],
  );

  const employeeLogins = new Set(employees.map((employee) => employee.login));
  const availableAdUsers = adUsers.filter(
    (user) =>
      !employeeLogins.has(user.login) &&
      availableDepartments.some((departmentItem) => departmentItem.id === user.departmentId),
  );
  const selectedAdUser = adUsers.find((user) => user.id === form.adUserId);
  const department = departments.find((item) => item.id === activeDepartmentId);
  const totalInDepartment = employees.filter((employee) => employee.departmentId === activeDepartmentId).length;
  const activeInDepartment = employees.filter((employee) => employee.departmentId === activeDepartmentId && employee.isActive).length;

  const openCreateModal = () => {
    const firstAvailableAdUser = availableAdUsers[0];

    setForm({
      adUserId: firstAvailableAdUser?.id ?? "",
      role: "executor",
      isActive: true,
    });
    setErrors({});
    setIsModalOpen(true);
  };

  const validate = () => {
    const nextErrors: EmployeeErrors = {};

    if (!form.adUserId || !selectedAdUser) {
      nextErrors.adUserId = "Выберите пользователя из корпоративного каталога.";
    }

    if (selectedAdUser && employees.some((employee) => employee.login === selectedAdUser.login)) {
      nextErrors.adUserId = "Этот пользователь уже добавлен в систему.";
    }

    if (!form.role) {
      nextErrors.role = "Выберите роль.";
    }

    setErrors(nextErrors);

    return Object.keys(nextErrors).length === 0;
  };

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    if (!validate() || !selectedAdUser || !credentials) {
      return;
    }

    try {
      await apiClient.createEmployee(credentials, form);
      await refresh();
      setDepartmentId(selectedAdUser.departmentId);
      setActivityFilter("all");
      setNotice(`Сотрудник «${selectedAdUser.fullName}» добавлен в систему.`);
      setIsModalOpen(false);
    } catch {
      setNotice("Не удалось добавить сотрудника.");
    }
  };

  const toggleActivity = async (employee: User) => {
    if (!credentials) {
      return;
    }

    try {
      await apiClient.updateEmployee(credentials, employee.id, { isActive: !employee.isActive });
      await refresh();
      setNotice(
        `Сотрудник «${employee.fullName}» ${employee.isActive ? "исключен из распределения заявок" : "доступен для распределения заявок"}.`,
      );
    } catch {
      setNotice("Не удалось изменить активность сотрудника.");
    }
  };

  const deleteEmployee = async (employee: User) => {
    if (!credentials) {
      return;
    }

    try {
      await apiClient.deleteEmployee(credentials, employee.id);
      await refresh();
      setNotice(`Сотрудник «${employee.fullName}» удален из системы.`);
    } catch {
      setNotice("Не удалось удалить сотрудника.");
    }
  };

  const toggleDelegationConfirmation = async () => {
    if (!credentials || !department) {
      return;
    }

    const nextValue = !department.delegatedToSameDepartment;

    try {
      await apiClient.updateDepartmentDelegationSettings(credentials, department.id, { delegatedToSameDepartment: nextValue });
      await refresh();
      setNotice(
        nextValue
          ? `Для отдела «${department.name}» включено подтверждение делегирования внутри отдела.`
          : `Для отдела «${department.name}» подтверждение делегирования внутри отдела отключено.`,
      );
    } catch {
      setNotice("Не удалось обновить настройку делегирования.");
    }
  };

  return (
    <section className="employees-page">
      <header className="employees-page__header">
        <div>
          <h1>Управление сотрудниками</h1>
          <p>Руководитель добавляет сотрудников из корпоративного каталога и настраивает их роль и участие в распределении заявок.</p>
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
            Отдел
            <select value={activeDepartmentId} onChange={(event) => setDepartmentId(event.target.value)}>
              {availableDepartments.map((departmentItem) => (
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

        <section className="employees-delegation-setting" aria-label="Настройки делегирования отдела">
          <div>
            <strong>Делегирование внутри отдела</strong>
            <span>{department?.name ?? "-"}</span>
          </div>
          <label>
            <input
              type="checkbox"
              checked={Boolean(department?.delegatedToSameDepartment)}
              onChange={() => void toggleDelegationConfirmation()}
            />
            Подтверждать руководителем
          </label>
        </section>

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
            visibleEmployees.map((employee) => (
              <div className="employees-table__row" role="row" key={employee.id}>
                <span role="cell">{employee.fullName}</span>
                <span role="cell">{employee.login}</span>
                <span role="cell">{roleLabels[employee.role]}</span>
                <span role="cell">{employee.postName}</span>
                <span role="cell">
                  <span className={employee.isActive ? "employees-status employees-status--active" : "employees-status"}>
                    {employee.isActive ? "Принимает заявки" : "Не принимает"}
                  </span>
                </span>
                <span role="cell">
                  <div className="employees-actions">
                    <button type="button" onClick={() => void toggleActivity(employee)}>
                      {employee.isActive ? "Отключить" : "Включить"}
                    </button>
                    <button type="button" onClick={() => void deleteEmployee(employee)}>
                      Удалить
                    </button>
                  </div>
                </span>
              </div>
            ))
          ) : (
            <div className="employees-table__empty">Сотрудники по выбранным фильтрам не найдены.</div>
          )}
        </div>
      </article>

      {isModalOpen ? (
        <div className="employees-modal" role="dialog" aria-modal="true" aria-label="Добавление сотрудника">
          <form className="employees-modal__panel" onSubmit={handleCreate} noValidate>
            <header>
              <h2>Добавить сотрудника</h2>
              <button type="button" onClick={() => setIsModalOpen(false)} aria-label="Закрыть">
                ×
              </button>
            </header>

            {availableAdUsers.length > 0 ? (
              <>
                <label>
                  Пользователь
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

                <div className="employees-ad-card" aria-label="Данные сотрудника">
                  <div>
                    <span>Отдел</span>
                    <strong>{getDepartmentName(departments, selectedAdUser?.departmentId)}</strong>
                  </div>
                  <div>
                    <span>Должность</span>
                    <strong>{selectedAdUser?.postName ?? "-"}</strong>
                  </div>
                </div>

                <label>
                  Роль в системе
                  <select
                    value={form.role}
                    onChange={(event) => {
                      setForm((current) => ({ ...current, role: event.target.value as UserRole }));
                      setErrors((current) => ({ ...current, role: undefined }));
                    }}
                  >
                    {assignableRoles.map((role) => (
                      <option value={role} key={role}>
                        {roleLabels[role]}
                      </option>
                    ))}
                  </select>
                  {errors.role ? <small>{errors.role}</small> : null}
                </label>

                <div className="employees-ad-card" aria-label="Параметры системы">
                  <div>
                    <span>Роль в системе</span>
                    <strong>{roleLabels[form.role]}</strong>
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
              </>
            ) : (
              <div className="employees-table__empty">Все доступные пользователи уже добавлены в систему.</div>
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

function getDepartmentName(departments: Array<{ id: string; name: string }>, departmentId?: string) {
  return departments.find((department) => department.id === departmentId)?.name ?? "-";
}
