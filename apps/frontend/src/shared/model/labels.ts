import type { ApplicationAction, ApplicationPriority, ApplicationStatus, UserRole } from "./domain";

export const roleLabels: Record<UserRole, string> = {
  author: "Автор",
  executor: "Исполнитель",
  manager: "Руководитель",
  "top-manager": "Топ-менеджер",
};

export const statusLabels: Record<ApplicationStatus, string> = {
  new: "Новый",
  assigned: "Назначен исполнитель",
  delegated: "Делегирована в другой отдел",
  inProgress: "В работе",
  rejected: "Отклонена",
  completed: "Завершена",
};

export const priorityLabels: Record<ApplicationPriority, string> = {
  low: "Низкий",
  medium: "Средний",
  high: "Высокий",
  critical: "Критичный",
};

export const actionLabels: Record<ApplicationAction, string> = {
  editDescription: "Редактировать описание",
  assignExecutor: "Назначить исполнителя",
  startWork: "Взять в работу",
  reject: "Отклонить",
  complete: "Завершить",
  delegateInternal: "Делегировать внутри отдела",
  delegateExternal: "Делегировать в другой отдел",
  returnToNew: "Вернуть в Новый",
  cancel: "Отменить заявку",
  archive: "В архив",
  confirmExternalDelegation: "Подтвердить делегирование",
  declineExternalDelegation: "Отклонить делегирование",
  changeWorkType: "Изменить вид работ",
};
