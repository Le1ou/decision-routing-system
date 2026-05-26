import type { Department, Notification, Position, Request, User, WorkType } from "@shared/model/domain";

export const departments: Department[] = [
  { id: "it", name: "IT-отдел", value: 0.75 },
  { id: "oge", name: "Отдел главного энергетика", value: 0.82 },
  { id: "production", name: "Производственный отдел", value: 0.9 },
  { id: "okk", name: "Отдел контроля качества", value: 0.7 },
  { id: "ogm", name: "Отдел главного механика", value: 0.86 },
  { id: "warehouse", name: "Складской отдел", value: 0.58 },
  { id: "supply", name: "Отдел снабжения", value: 0.64 },
];

export const positions: Position[] = [
  { id: "engineer", name: "Инженер", isTop: false },
  { id: "lead-engineer", name: "Ведущий инженер", isTop: false },
  { id: "department-head", name: "Руководитель отдела", isTop: true },
];

export const mockUsers: User[] = [
  {
    id: "user-author",
    login: "author",
    fullName: "Кузнецова Анна Сергеевна",
    role: "author",
    departmentId: "production",
    positionId: "engineer",
  },
  {
    id: "user-executor",
    login: "executor",
    fullName: "Смирнов Павел Олегович",
    role: "executor",
    departmentId: "it",
    positionId: "lead-engineer",
  },
  {
    id: "user-manager",
    login: "manager",
    fullName: "Орлова Мария Викторовна",
    role: "manager",
    departmentId: "it",
    positionId: "department-head",
  },
];

export const workTypes: WorkType[] = [
  { id: "it-hardware-replace", name: "Замена оборудования", departmentId: "it", complexity: "medium" },
  { id: "it-server-setup", name: "Настройка сервера", departmentId: "it", complexity: "hard" },
  { id: "oge-wiring", name: "Ремонт проводки", departmentId: "oge", complexity: "hard" },
  { id: "production-repair", name: "Заявка на ремонт оборудования", departmentId: "production", complexity: "medium" },
  { id: "warehouse-inventory", name: "Инвентаризация", departmentId: "warehouse", complexity: "easy" },
];

export const requests: Request[] = [
  {
    id: "request-1",
    number: "DRS-1024",
    title: "Не запускается станция оператора",
    description: "После перезапуска рабочая станция не проходит загрузку и не подключается к сети цеха.",
    status: "new",
    priority: "high",
    departmentId: "it",
    workTypeId: "it-hardware-replace",
    authorId: "user-author",
    isUnfinished: false,
    createdAt: "2026-05-21T08:30:00.000Z",
    deadlineAt: "2026-05-22T15:00:00.000Z",
  },
  {
    id: "request-2",
    number: "DRS-1025",
    title: "Настроить доступ к серверу отчетности",
    description: "Нужен доступ для сменного инженера к папке с производственными отчетами.",
    status: "assigned",
    priority: "medium",
    departmentId: "it",
    workTypeId: "it-server-setup",
    authorId: "user-author",
    executorId: "user-executor",
    isUnfinished: false,
    createdAt: "2026-05-22T11:05:00.000Z",
    deadlineAt: "2026-05-24T12:00:00.000Z",
  },
  {
    id: "request-3",
    number: "DRS-1026",
    title: "Проверка партии готовой продукции",
    description: "Требуется внеплановая проверка партии после замены комплектующих.",
    status: "inProgress",
    priority: "critical",
    departmentId: "okk",
    workTypeId: "production-repair",
    authorId: "user-manager",
    executorId: "user-executor",
    isUnfinished: true,
    createdAt: "2026-05-23T07:15:00.000Z",
    deadlineAt: "2026-05-23T18:00:00.000Z",
    startedAt: "2026-05-23T08:10:00.000Z",
  },
];

export const notifications: Notification[] = [
  {
    id: "notification-1",
    text: "Заявка DRS-1024 ожидает назначения исполнителя",
    requestId: "request-1",
    createdAt: "2026-05-21T09:00:00.000Z",
    isRead: false,
  },
  {
    id: "notification-2",
    text: "По заявке DRS-1025 назначен исполнитель",
    requestId: "request-2",
    createdAt: "2026-05-22T11:40:00.000Z",
    isRead: true,
  },
];
