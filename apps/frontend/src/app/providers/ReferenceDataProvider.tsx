import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { apiClient, mapAdUser, mapUser } from "@shared/api";
import type { AdUser, Department, Grade, Position, PrioritySettings, User, WorkType } from "@shared/model/domain";

type ReferenceDataContextValue = {
  departments: Department[];
  positions: Position[];
  grades: Grade[];
  workTypes: WorkType[];
  employees: User[];
  adUsers: AdUser[];
  prioritySettings: PrioritySettings | null;
  isLoading: boolean;
  error: string;
  refresh: () => Promise<void>;
};

const ReferenceDataContext = createContext<ReferenceDataContextValue | null>(null);

export function ReferenceDataProvider({ children }: { children: ReactNode }) {
  const { credentials } = useAuth();
  const [departments, setDepartments] = useState<Department[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [grades, setGrades] = useState<Grade[]>([]);
  const [workTypes, setWorkTypes] = useState<WorkType[]>([]);
  const [employees, setEmployees] = useState<User[]>([]);
  const [adUsers, setAdUsers] = useState<AdUser[]>([]);
  const [prioritySettings, setPrioritySettings] = useState<PrioritySettings | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!credentials) {
      setDepartments([]);
      setPositions([]);
      setGrades([]);
      setWorkTypes([]);
      setEmployees([]);
      setAdUsers([]);
      setPrioritySettings(null);
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      const [departmentsResponse, positionsResponse, gradesResponse, workTypesResponse, employeesResponse, adUsersResponse, priorityResponse] =
        await Promise.all([
          apiClient.getDepartments(credentials),
          apiClient.getPositions(credentials),
          apiClient.getGrades(credentials),
          apiClient.getWorkTypes(credentials),
          apiClient.getEmployees(credentials),
          apiClient.getAdUsers(credentials),
          apiClient.getPrioritySettings(credentials),
        ]);

      setDepartments(departmentsResponse.items);
      setPositions(positionsResponse.items);
      setGrades(gradesResponse.items);
      setWorkTypes(workTypesResponse.items);
      setEmployees(employeesResponse.items.map(mapUser));
      setAdUsers(adUsersResponse.items.map(mapAdUser));
      setPrioritySettings(priorityResponse);
    } catch {
      setError("Не удалось загрузить справочники backend.");
    } finally {
      setIsLoading(false);
    }
  }, [credentials]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<ReferenceDataContextValue>(
    () => ({
      departments,
      positions,
      grades,
      workTypes,
      employees,
      adUsers,
      prioritySettings,
      isLoading,
      error,
      refresh,
    }),
    [adUsers, departments, employees, error, grades, isLoading, positions, prioritySettings, refresh, workTypes],
  );

  return <ReferenceDataContext.Provider value={value}>{children}</ReferenceDataContext.Provider>;
}

export function useReferenceData() {
  const context = useContext(ReferenceDataContext);

  if (!context) {
    throw new Error("useReferenceData must be used inside ReferenceDataProvider");
  }

  return context;
}
