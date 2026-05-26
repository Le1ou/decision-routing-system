import { Link } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";

import "./HomePage.css";

const sections = [
  { title: "Создание заявки", to: "/requests/new" },
  { title: "Просмотр заявок", to: "/requests" },
  { title: "Отчетность", to: "/reports", managerOnly: true },
  { title: "Управление\nсотрудниками", to: "/employees", managerOnly: true },
  { title: "Виды работ", to: "/work-types", managerOnly: true },
  { title: "Изменение\nприоритетности\nзаявки", to: "/priority-settings", managerOnly: true },
];

export function HomePage() {
  const { currentUser } = useAuth();
  const visibleSections = sections.filter((section) => !section.managerOnly || currentUser?.role === "manager");

  return (
    <section className="home-page" aria-label="Стартовая страница">
      <div className="home-menu">
        {visibleSections.map((section) => (
          <Link className="home-menu__button" to={section.to} key={section.to}>
            {section.title.split("\n").map((line) => (
              <span key={line}>{line}</span>
            ))}
          </Link>
        ))}
      </div>
    </section>
  );
}
