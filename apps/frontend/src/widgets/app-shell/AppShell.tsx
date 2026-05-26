import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
import { notifications } from "@mocks/mockData";

import "./AppShell.css";

export function AppShell({ children }: { children: ReactNode }) {
  const { currentUser, logout } = useAuth();

  if (!currentUser) {
    return <>{children}</>;
  }

  const unreadCount = notifications.filter((notification) => !notification.isRead).length;
  const displayName = currentUser.fullName.split(" ").slice(0, 2).join(" ");

  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <Link className="app-shell__home-link" to="/">
          Маршрутизация заявок
        </Link>
        <button className="app-shell__notifications" type="button" aria-label="Уведомления">
          <span className="app-shell__notification-dot" aria-hidden="true" />
          Уведомления
          {unreadCount > 0 ? <b>{unreadCount}</b> : null}
        </button>
        <div className="app-shell__profile" aria-label="Текущий пользователь">
          <span className="app-shell__avatar" aria-hidden="true" />
          <strong>{displayName}</strong>
        </div>
        <button className="app-shell__logout" type="button" onClick={logout}>
          Выйти
        </button>
      </header>
      <main className="app-shell__content">{children}</main>
    </div>
  );
}
