import type { RequestPriority, RequestStatus, UserRole } from "./domain";

export const roleLabels: Record<UserRole, string> = {
  author: "Автор",
  executor: "Исполнитель",
  manager: "Руководитель",
};

export const statusLabels: Record<RequestStatus, string> = {
  new: "Новый",
  assigned: "Назначен исполнитель",
  delegated: "Делегирована в другой отдел",
  inProgress: "В работе",
  rejected: "Отклонена",
  completed: "Завершена",
};

export const priorityLabels: Record<RequestPriority, string> = {
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
  critical: "Критичный",
};
