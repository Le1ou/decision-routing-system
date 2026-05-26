import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { mockUsers } from "@mocks/mockData";
import type { User } from "@shared/model/domain";

const MOCK_USER_STORAGE_KEY = "decision-routing.mock-user-login";

type AuthContextValue = {
  currentUser: User | null;
  availableUsers: User[];
  login: (login: string) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [currentUser, setCurrentUser] = useState<User | null>(() => {
    const savedLogin = window.localStorage.getItem(MOCK_USER_STORAGE_KEY);

    return mockUsers.find((user) => user.login === savedLogin) ?? null;
  });

  const value = useMemo<AuthContextValue>(
    () => ({
      currentUser,
      availableUsers: mockUsers,
      login: (login) => {
        const user = mockUsers.find((item) => item.login === login) ?? mockUsers[0];
        window.localStorage.setItem(MOCK_USER_STORAGE_KEY, user.login);
        setCurrentUser(user);
      },
      logout: () => {
        window.localStorage.removeItem(MOCK_USER_STORAGE_KEY);
        setCurrentUser(null);
      },
    }),
    [currentUser],
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
