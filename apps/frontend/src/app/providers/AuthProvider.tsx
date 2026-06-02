import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { mockUsers } from "@mocks/mockData";
import { apiClient, ApiError, type ApiCredentials, mapCurrentUser } from "@shared/api";
import type { User, UserPermissions } from "@shared/model/domain";

const AUTH_STORAGE_KEY = "decision-routing.basic-auth";

type StoredAuth = {
  credentials: ApiCredentials;
  user: User;
  permissions: UserPermissions;
};

type AuthContextValue = {
  currentUser: User | null;
  permissions: UserPermissions | null;
  credentials: ApiCredentials | null;
  availableUsers: User[];
  login: (credentials: ApiCredentials) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [storedAuth, setStoredAuth] = useState<StoredAuth | null>(() => {
    const rawValue = window.localStorage.getItem(AUTH_STORAGE_KEY);

    if (!rawValue) {
      return null;
    }

    try {
      return JSON.parse(rawValue) as StoredAuth;
    } catch {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
      return null;
    }
  });

  const value = useMemo<AuthContextValue>(
    () => ({
      currentUser: storedAuth?.user ?? null,
      permissions: storedAuth?.permissions ?? null,
      credentials: storedAuth?.credentials ?? null,
      availableUsers: mockUsers,
      login: async (credentials) => {
        try {
          const response = await apiClient.getCurrentUser(credentials);
          const nextAuth: StoredAuth = {
            credentials,
            user: mapCurrentUser(response),
            permissions: response.permissions,
          };

          window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(nextAuth));
          setStoredAuth(nextAuth);
        } catch (error) {
          if (error instanceof ApiError && error.status === 401) {
            throw new Error("Неверный логин или пароль.");
          }

          throw new Error("Не удалось подключиться к backend.");
        }
      },
      logout: () => {
        window.localStorage.removeItem(AUTH_STORAGE_KEY);
        setStoredAuth(null);
      },
    }),
    [storedAuth],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }

  return context;
}
