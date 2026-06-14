import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { useAuth } from "@app/providers/AuthProvider";
import { apiClient, mapAdUser, mapUser } from "@shared/api";
import type { AdUser, Department, Grade, Position, PrioritySettings, User, WorkType } from "@shared/model/domain";
import { getGradeLabel } from "@shared/model/labels";

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
  const { credentials, currentUser, permissions } = useAuth();
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
      const [departmentsResponse, positionsResponse, gradesResponse, workTypesResponse, employeesResponse] = await Promise.all([
        apiClient.getDepartments(credentials),
        apiClient.getPositions(credentials),
        apiClient.getGrades(credentials),
        apiClient.getWorkTypes(credentials),
        apiClient.getEmployees(credentials),
      ]);

      const [adUsersResponse, priorityResponse] = await Promise.all([
        permissions?.canManageEmployees ? apiClient.getAdUsers(credentials) : Promise.resolve({ items: [] }),
        currentUser?.role === "manager" || currentUser?.role === "top-manager" || permissions?.canManagePrioritySettings
          ? apiClient.getPrioritySettings(credentials)
          : Promise.resolve(null),
      ]);

      setDepartments(departmentsResponse.items);
      setPositions(positionsResponse.items.map((position) => ({ ...position, gradeIds: position.gradeIds ?? [] })));
      setGrades(gradesResponse.items.map((grade) => ({ ...grade, name: getGradeLabel(grade) })));
      setWorkTypes(workTypesResponse.items.map((workType) => ({ ...workType, allowedPositionIds: workType.allowedPositionIds ?? [] })));
      setEmployees(employeesResponse.items.map(mapUser));
      setAdUsers(adUsersResponse.items.map(mapAdUser));
      setPrioritySettings(priorityResponse);
    } catch {
      setError("Не удалось загрузить справочники.");
    } finally {
      setIsLoading(false);
    }
  }, [credentials, currentUser, permissions]);

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
