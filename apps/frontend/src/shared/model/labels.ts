import type { RequestAction, RequestPriority, RequestStatus, UserRole } from "./domain";

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

export const actionLabels: Record<RequestAction, string> = {
  editDescription: "Редактировать описание",
  assignExecutor: "Назначить исполнителя",
  startWork: "Взять в работу",
  reject: "Отклонить",
  complete: "Завершить",
  delegateInternal: "Делегировать внутри отдела",
  delegateExternal: "Делегировать в другой отдел",
  returnToNew: "Вернуть в Новый",
  confirmExternalDelegation: "Подтвердить делегирование",
  declineExternalDelegation: "Отклонить делегирование",
};
