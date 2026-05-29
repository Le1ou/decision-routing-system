import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { applications } from "@mocks/mockData";
import type { Application } from "@shared/model/domain";

type ApplicationsContextValue = {
  applicationItems: Application[];
  addApplication: (application: Application) => void;
  updateApplication: (applicationId: string, updater: (application: Application) => Application) => void;
};

const ApplicationsContext = createContext<ApplicationsContextValue | null>(null);

export function ApplicationsProvider({ children }: { children: ReactNode }) {
  const [applicationItems, setApplicationItems] = useState<Application[]>(applications);

  const value = useMemo<ApplicationsContextValue>(
    () => ({
      applicationItems,
      addApplication: (application) => setApplicationItems((current) => [application, ...current]),
      updateApplication: (applicationId, updater) =>
        setApplicationItems((current) => current.map((application) => (application.id === applicationId ? updater(application) : application))),
    }),
    [applicationItems],
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
