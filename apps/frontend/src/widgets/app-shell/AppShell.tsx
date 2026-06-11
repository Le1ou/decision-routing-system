import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "@app/providers/AuthProvider";
import { useApplicationsStore } from "@app/providers/ApplicationsProvider";
import { apiClient, mapNotification } from "@shared/api";
import { env } from "@shared/config/env";
import { usePolling } from "@shared/hooks/usePolling";
import type { Notification } from "@shared/model/domain";

import "./AppShell.css";

export function AppShell({ children }: { children: ReactNode }) {
  const { currentUser, credentials, logout } = useAuth();
  const { applicationItems } = useApplicationsStore();
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);

  const refreshNotifications = useCallback(async () => {
    if (!credentials) {
      setNotifications([]);
      setUnreadCount(0);
      return;
    }

    const response = await apiClient.getNotifications(credentials);

    setNotifications(response.items.map(mapNotification));
    setUnreadCount(response.unreadCount);
  }, [credentials]);

  useEffect(() => {
    void refreshNotifications();
  }, [refreshNotifications]);

  usePolling(refreshNotifications, env.pollIntervalMs, Boolean(credentials));

  if (!currentUser) {
    return <>{children}</>;
  }

  const enrichedNotifications = useMemo(
    () =>
      notifications.map((notification) => ({
        ...notification,
        application: applicationItems.find((application) => application.id === notification.applicationId),
      })),
    [applicationItems, notifications],
  );
  const displayName = currentUser.fullName.split(" ").slice(0, 2).join(" ");

  const markAllAsRead = async () => {
    if (!credentials) {
      return;
    }

    await apiClient.markAllNotificationsRead(credentials);
    await refreshNotifications();
  };

  return (
    <div className="app-shell">
      <header className="app-shell__header">
        <Link className="app-shell__home-link" to="/" aria-label="ДиспетчерЗаявок">
          <img src="/application-dispatcher-mark.svg" alt="" aria-hidden="true" />
          <span>
            <strong>ДиспетчерЗаявок</strong>
            <small>Маршрутизация и контроль</small>
          </span>
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
                      to={notification.application ? `/applications?application=${notification.application.id}` : "/applications"}
                      key={notification.id}
                      onClick={async () => {
                        if (credentials && !notification.isRead) {
                          await apiClient.markNotificationRead(credentials, notification.id);
                          await refreshNotifications();
                        }
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
