import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { apiClient, mapApplication, type ApplicationListItemDto } from "@shared/api";
import { env } from "@shared/config/env";
import { usePolling } from "@shared/hooks/usePolling";
import type { Application, ApplicationAction, Complexity } from "@shared/model/domain";

type ApplicationsContextValue = {
  applicationItems: Application[];
  applicationsTotal: number;
  hasMoreApplications: boolean;
  isLoading: boolean;
  error: string;
  refreshApplications: () => Promise<void>;
  refreshApplicationDetail: (applicationId: string) => Promise<void>;
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
const APPLICATIONS_PAGE_SIZE = 100;

export function ApplicationsProvider({ children }: { children: ReactNode }) {
  const { credentials } = useAuth();
  const [applicationItems, setApplicationItems] = useState<Application[]>([]);
  const [applicationsTotal, setApplicationsTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const refreshApplicationList = useCallback(async (options: { showLoading?: boolean; reportError?: boolean } = {}) => {
    if (!credentials) {
      setApplicationItems([]);
      setApplicationsTotal(0);
      return;
    }

    if (options.showLoading) {
      setIsLoading(true);
      setError("");
    }

    try {
      const response = await apiClient.getApplications(credentials, { pageSize: APPLICATIONS_PAGE_SIZE });
      setApplicationsTotal(response.pagination.total);

      setApplicationItems((current) => {
        const currentById = new Map(current.map((application) => [application.id, application]));

        return response.items.map((application) => mapApplicationListItem(application, currentById.get(application.id)));
      });
    } catch {
      if (options.reportError) {
        setError("Не удалось загрузить заявки backend.");
      } else {
        console.warn("Не удалось обновить список заявок.");
      }
    } finally {
      if (options.showLoading) {
        setIsLoading(false);
      }
    }
  }, [credentials]);

  const refreshApplications = useCallback(
    async () => {
      if (!credentials) {
        setApplicationItems([]);
        setApplicationsTotal(0);
        return;
      }

      setIsLoading(true);
      setError("");

      try {
        const response = await apiClient.getApplications(credentials, { pageSize: APPLICATIONS_PAGE_SIZE });
        setApplicationsTotal(response.pagination.total);
        const details = await Promise.all(
          response.items.map((application) =>
            apiClient
              .getApplication(credentials, application.id)
              .then((detailResponse) => mapApplication(detailResponse.application))
              .catch(() => mapApplicationListItem(application)),
          ),
        );

        setApplicationItems(details);
      } catch {
        setError("Не удалось загрузить заявки backend.");
      } finally {
        setIsLoading(false);
      }
    },
    [credentials],
  );

  const refreshApplicationDetail = useCallback(async (applicationId: string) => {
    if (!credentials) {
      setApplicationItems([]);
      return;
    }

    try {
      const response = await apiClient.getApplication(credentials, applicationId);
      const application = mapApplication(response.application);

      setApplicationItems((current) =>
        current.some((item) => item.id === applicationId)
          ? current.map((item) => (item.id === applicationId ? application : item))
          : [...current, application],
      );
    } catch {
      console.warn("Не удалось обновить карточку заявки.");
    }
  }, [credentials]);

  useEffect(() => {
    void refreshApplications();
  }, [refreshApplications]);

  usePolling(
    () => refreshApplicationList(),
    env.pollIntervalMs,
    Boolean(credentials),
  );

  const value = useMemo<ApplicationsContextValue>(
    () => ({
      applicationItems,
      applicationsTotal,
      hasMoreApplications: applicationsTotal > applicationItems.length,
      isLoading,
      error,
      refreshApplications,
      refreshApplicationDetail,
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
        await refreshApplicationDetail(response.id);

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
        await refreshApplicationDetail(applicationId);
      },
    }),
    [applicationItems, applicationsTotal, credentials, error, isLoading, refreshApplicationDetail, refreshApplications],
  );

  return <ApplicationsContext.Provider value={value}>{children}</ApplicationsContext.Provider>;
}

function mapApplicationListItem(dto: ApplicationListItemDto, previous?: Application): Application {
  return {
    ...previous,
    id: dto.id,
    title: dto.name,
    description: previous?.description ?? "",
    status: dto.status,
    priority: dto.priority,
    departmentId: previous?.departmentId ?? "",
    workTypeId: previous?.workTypeId ?? "",
    authorId: previous?.authorId ?? "",
    isUnfinished: previous?.isUnfinished ?? false,
    createdAt: dto.createdAt,
    deadlineAt: previous?.deadlineAt ?? dto.createdAt,
    updatedAt: previous?.updatedAt ?? dto.createdAt,
    finishedAt: dto.finishedAt ?? undefined,
  };
}

export function useApplicationsStore() {
  const context = useContext(ApplicationsContext);

  if (!context) {
    throw new Error("useApplicationsStore must be used inside ApplicationsProvider");
  }

  return context;
}
