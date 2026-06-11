import { Link } from "react-router-dom";

import { useApplicationsStore } from "@app/providers/ApplicationsProvider";
import { useAuth } from "@app/providers/AuthProvider";
import type { UserPermissions, UserRole } from "@shared/model/domain";
import { canAccessManagement, hasAnyRole } from "@shared/model/roles";

import "./HomePage.css";

const sections = [
  { title: "Отчетность", description: "Фильтры, период и выгрузка .xlsx", to: "/reports", permission: "canViewReports" },
  { title: "Сотрудники", description: "Состав отдела, должности и активность", to: "/employees", permission: "canManageEmployees" },
  { title: "Виды работ", description: "Справочник работ и сложности", to: "/work-types", permission: "canManageWorkTypes" },
  { title: "Приоритеты", description: "Коэффициенты и настройки отдела", to: "/priority-settings", roles: ["manager", "top-manager"] },
] satisfies Array<{ title: string; description: string; to: string; permission?: keyof UserPermissions; roles?: UserRole[] }>;

export function HomePage() {
  const { currentUser, permissions } = useAuth();
  const { applicationItems } = useApplicationsStore();
  const hasManagementAccess = currentUser ? canAccessManagement(currentUser, permissions) : false;
  const visibleSections = sections.filter((section) =>
    section.permission ? permissions?.[section.permission] : currentUser && section.roles ? hasAnyRole(currentUser, section.roles) : false,
  );
  const activeApplications = applicationItems.filter((application) => application.status !== "completed" && application.status !== "rejected");
  const criticalApplications = applicationItems.filter((application) => application.priority === "critical");
  const inProgressApplications = applicationItems.filter((application) => application.status === "inProgress");

  return (
    <section className="home-page" aria-label="Стартовая страница">
      <div className="home-page__inner">
        <div className="home-hero">
          <div>
            <p className="home-hero__eyebrow">Рабочая область</p>
            <h1>Добрый день, {currentUser?.fullName.split(" ")[1] ?? "пользователь"}</h1>
            <p>
              Быстрый доступ к заявкам, справочникам и отчетности. Разделы управления показываются по правам backend.
            </p>
          </div>

          <div className="home-hero__summary" aria-label="Краткая сводка">
            <span>Видимых заявок</span>
            <strong>{applicationItems.length}</strong>
          </div>
        </div>

        <div className="home-actions" aria-label="Основные действия">
          <Link className="home-action home-action--primary" to="/applications/new">
            <span className="home-action__icon" aria-hidden="true">+</span>
            <span>
              <strong>Создать заявку</strong>
              <small>Описать проблему, выбрать отдел и вид работ</small>
            </span>
          </Link>

          <Link className="home-action" to="/applications">
            <span className="home-action__icon" aria-hidden="true">#</span>
            <span>
              <strong>Просмотреть заявки</strong>
              <small>Фильтры, статусы, карточка и действия</small>
            </span>
          </Link>
        </div>

        <div className="home-overview" aria-label="Сводка по заявкам">
          <article>
            <span>Активные</span>
            <strong>{activeApplications.length}</strong>
          </article>
          <article>
            <span>Критичные</span>
            <strong>{criticalApplications.length}</strong>
          </article>
          <article>
            <span>В работе</span>
            <strong>{inProgressApplications.length}</strong>
          </article>
        </div>

        {hasManagementAccess && visibleSections.length > 0 ? (
          <div className="home-tools" aria-label="Разделы руководителя">
            <div className="home-tools__header">
              <h2>Управление</h2>
              <span>Разделы доступны для руководителя</span>
            </div>

            <div className="home-tools__grid">
              {visibleSections.map((section) => (
                <Link className="home-tool" to={section.to} key={section.to}>
                  <strong>{section.title}</strong>
                  <span>{section.description}</span>
                </Link>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
