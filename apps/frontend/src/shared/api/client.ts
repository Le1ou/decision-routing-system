import { env } from "@shared/config/env";
import type { UserPermissions, UserRole } from "@shared/model/domain";

export type ApiCredentials = {
  login: string;
  password: string;
};

export type CurrentUserDto = {
  user: {
    id: string;
    login: string;
    fullName: string;
    roles: UserRole[];
    departmentId: string;
    postName: string;
    positionId: string;
    isActive: boolean;
  };
  permissions: UserPermissions;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function authHeader(credentials: ApiCredentials) {
  return `Basic ${window.btoa(`${credentials.login}:${credentials.password}`)}`;
}

export async function apiRequest<T>(path: string, credentials: ApiCredentials, options: RequestInit = {}) {
  const response = await fetch(`${env.apiUrl}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      Authorization: authHeader(credentials),
      ...options.headers,
    },
  });

  if (!response.ok) {
    throw new ApiError(response.statusText || "API request failed", response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export const apiClient = {
  getCurrentUser: (credentials: ApiCredentials) => apiRequest<CurrentUserDto>("/auth/me", credentials),
};
