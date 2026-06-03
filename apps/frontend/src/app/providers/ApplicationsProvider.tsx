import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { apiClient, mapApplication } from "@shared/api";
import type { Application, ApplicationAction, Complexity } from "@shared/model/domain";

type ApplicationsContextValue = {
  applicationItems: Application[];
  isLoading: boolean;
  error: string;
  refreshApplications: () => Promise<void>;
  addApplication: (payload: {
    title: string;
    departmentId: string;
    workTypeId: string;
    deadlineAt: string;
    description: string;
    files: File[];
  }) => Promise<string>;
  updateApplication: (applicationId: string, updater: (application: Application) => Application) => void;
  performAction: (
    applicationId: string,
    payload: {
      action: ApplicationAction;
      executorId?: string;
      departmentId?: string;
      workTypeId?: string;
      comment?: string;
      complexity?: Complexity;
      resultText?: string;
      description?: string;
    },
  ) => Promise<void>;
};

const ApplicationsContext = createContext<ApplicationsContextValue | null>(null);

export function ApplicationsProvider({ children }: { children: ReactNode }) {
  const { credentials } = useAuth();
  const [applicationItems, setApplicationItems] = useState<Application[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const refreshApplications = useCallback(async () => {
    if (!credentials) {
      setApplicationItems([]);
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      const response = await apiClient.getApplications(credentials);
      const details = await Promise.all(
        response.items.map((application) =>
          apiClient
            .getApplication(credentials, application.id)
            .then((detailResponse) => mapApplication(detailResponse.application))
            .catch(() => ({
              id: application.id,
              title: application.name,
              description: "",
              status: application.status,
              priority: application.priority,
              departmentId: "",
              workTypeId: "",
              authorId: "",
              isUnfinished: false,
              createdAt: application.createdAt,
              deadlineAt: application.createdAt,
              updatedAt: application.createdAt,
              finishedAt: application.finishedAt ?? undefined,
            })),
        ),
      );

      setApplicationItems(details);
    } catch {
      setError("Не удалось загрузить заявки backend.");
    } finally {
      setIsLoading(false);
    }
  }, [credentials]);

  useEffect(() => {
    void refreshApplications();
  }, [refreshApplications]);

  const value = useMemo<ApplicationsContextValue>(
    () => ({
      applicationItems,
      isLoading,
      error,
      refreshApplications,
      addApplication: async (payload) => {
        if (!credentials) {
          throw new Error("Нет активной авторизации.");
        }

        const response = await apiClient.createApplication(credentials, {
          name: payload.title,
          departmentId: payload.departmentId,
          workTypeId: payload.workTypeId,
          deadlineAt: payload.deadlineAt,
          description: payload.description,
        });

        if (payload.files.length > 0) {
          await apiClient.uploadAttachments(credentials, response.id, payload.files);
        }

        await refreshApplications();

        return response.id;
      },
      updateApplication: (applicationId, updater) =>
        setApplicationItems((current) => current.map((application) => (application.id === applicationId ? updater(application) : application))),
      performAction: async (applicationId, payload) => {
        if (!credentials) {
          throw new Error("Нет активной авторизации.");
        }

        await apiClient.performApplicationAction(credentials, applicationId, payload);
        await refreshApplications();
      },
    }),
    [applicationItems, credentials, error, isLoading, refreshApplications],
  );

  return <ApplicationsContext.Provider value={value}>{children}</ApplicationsContext.Provider>;
}

export function useApplicationsStore() {
  const context = useContext(ApplicationsContext);

  if (!context) {
    throw new Error("useApplicationsStore must be used inside ApplicationsProvider");
  }

  return context;
}
