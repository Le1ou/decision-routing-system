import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

import { requests } from "@mocks/mockData";
import type { Request } from "@shared/model/domain";

type RequestsContextValue = {
  requestItems: Request[];
  addRequest: (request: Request) => void;
  updateRequest: (requestId: string, updater: (request: Request) => Request) => void;
};

const RequestsContext = createContext<RequestsContextValue | null>(null);

export function RequestsProvider({ children }: { children: ReactNode }) {
  const [requestItems, setRequestItems] = useState<Request[]>(requests);

  const value = useMemo<RequestsContextValue>(
    () => ({
      requestItems,
      addRequest: (request) => setRequestItems((current) => [request, ...current]),
      updateRequest: (requestId, updater) =>
        setRequestItems((current) => current.map((request) => (request.id === requestId ? updater(request) : request))),
    }),
    [requestItems],
  );

  return <RequestsContext.Provider value={value}>{children}</RequestsContext.Provider>;
}

export function useRequestsStore() {
  const context = useContext(RequestsContext);

  if (!context) {
    throw new Error("useRequestsStore must be used inside RequestsProvider");
  }

  return context;
}
