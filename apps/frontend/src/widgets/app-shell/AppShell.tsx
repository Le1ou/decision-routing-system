import { useMemo, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
import { notifications, requests } from "@mocks/mockData";
import { filterRequestsByRole } from "@shared/model/requestRules";

import "./AppShell.css";

export function AppShell({ children }: { children: ReactNode }) {
  const { currentUser, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  const [readNotificationIds, setReadNotificationIds] = useState(
    () => new Set(notifications.filter((notification) => notification.isRead).map((notification) => notification.id)),
  );

  if (!currentUser) {
    return <>{children}</>;
  }

  const visibleRequestIds = new Set(filterRequestsByRole(requests, currentUser).map((request) => request.id));
  const visibleNotifications = notifications.filter(
    (notification) => !notification.requestId || visibleRequestIds.has(notification.requestId),
  );
  const enrichedNotifications = useMemo(
    () =>
      visibleNotifications.map((notification) => ({
        ...notification,
        isRead: readNotificationIds.has(notification.id),
        request: requests.find((request) => request.id === notification.requestId),
      })),
    [readNotificationIds, visibleNotifications],
  );
  const unreadCount = enrichedNotifications.filter((notification) => !notification.isRead).length;
  const displayName = currentUser.fullName.split(" ").slice(0, 2).join(" ");

  const markAllAsRead = () => {
    setReadNotificationIds((current) => {
      const next = new Set(current);

      enrichedNotifications.forEach((notification) => next.add(notification.id));

      return next;
    });
  };

  const goBack = () => {
    if (location.pathname === "/") {
      navigate("/login");
      return;
    }

    navigate(-1);
  };

  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <button
          className="app-shell__back"
          type="button"
          onClick={goBack}
        >
          Назад
        </button>
        <Link className="app-shell__home-link" to="/">
          Маршрутизация заявок
        </Link>
        <div className="app-shell__notifications-wrap">
          <button
            className="app-shell__notifications"
            type="button"
            aria-label="Уведомления"
            aria-expanded={isNotificationsOpen}
            onClick={() => setIsNotificationsOpen((value) => !value)}
          >
            <span className="app-shell__notification-dot" aria-hidden="true" />
            Уведомления
            {unreadCount > 0 ? <b>{unreadCount}</b> : null}
          </button>

          {isNotificationsOpen ? (
            <section className="app-shell__notifications-panel" aria-label="Список уведомлений">
              <header>
                <div>
                  <h2>Уведомления</h2>
                  <span>{unreadCount > 0 ? `${unreadCount} новых` : "Новых нет"}</span>
                </div>
                <button type="button" onClick={markAllAsRead} disabled={unreadCount === 0}>
                  Прочитано
                </button>
              </header>

              <div className="app-shell__notifications-list">
                {enrichedNotifications.length > 0 ? (
                  enrichedNotifications.map((notification) => (
                    <Link
                      className={
                        notification.isRead
                          ? "app-shell__notification-item"
                          : "app-shell__notification-item app-shell__notification-item--unread"
                      }
                      to={notification.request ? `/requests?request=${notification.request.id}` : "/requests"}
                      key={notification.id}
                      onClick={() => {
                        setReadNotificationIds((current) => new Set(current).add(notification.id));
                        setIsNotificationsOpen(false);
                      }}
                    >
                      <span>{notification.text}</span>
                      <time>{formatDateTime(notification.createdAt)}</time>
                    </Link>
                  ))
                ) : (
                  <div className="app-shell__notifications-empty">Для текущей роли уведомлений нет.</div>
                )}
              </div>
            </section>
          ) : null}
        </div>
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

function formatDateTime(value: string) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
