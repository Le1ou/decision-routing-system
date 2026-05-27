import { Link } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
import { applications } from "@mocks/mockData";
import { filterApplicationsByRole } from "@shared/model/applicationRules";

import "./HomePage.css";

const sections = [
  { title: "Отчетность", description: "Фильтры, период и выгрузка .xls", to: "/reports", managerOnly: true },
  { title: "Сотрудники", description: "Состав отдела, должности и активность", to: "/employees", managerOnly: true },
  { title: "Виды работ", description: "Справочник работ и сложности", to: "/work-types", managerOnly: true },
  { title: "Приоритеты", description: "Коэффициенты расчета заявки", to: "/priority-settings", managerOnly: true },
];

export function HomePage() {
  const { currentUser } = useAuth();
  const visibleSections = sections.filter((section) => !section.managerOnly || currentUser?.role === "manager");
  const visibleApplications = currentUser ? filterApplicationsByRole(applications, currentUser) : [];
  const activeApplications = visibleApplications.filter((application) => application.status !== "completed" && application.status !== "rejected");
  const criticalApplications = visibleApplications.filter((application) => application.priority === "critical");
  const inProgressApplications = visibleApplications.filter((application) => application.status === "inProgress");

  return (
    <section className="home-page" aria-label="Стартовая страница">
      <div className="home-page__inner">
        <div className="home-hero">
          <div>
            <p className="home-hero__eyebrow">Рабочая область</p>
            <h1>Добрый день, {currentUser?.fullName.split(" ")[1] ?? "пользователь"}</h1>
            <p>
              Быстрый доступ к заявкам, справочникам и отчетности. Данные пока mock, но сценарии уже
              собраны под роли.
            </p>
          </div>

          <div className="home-hero__summary" aria-label="Краткая сводка">
            <span>Видимых заявок</span>
            <strong>{visibleApplications.length}</strong>
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

        {visibleSections.length > 0 ? (
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
