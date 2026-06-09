# Контракт API аналитики (для фронтенда)

Предварительный, но стабильный контракт 4 эндпоинтов аналитики. Реализация —
`apps/backend/src/analytics_module.py`. По нему можно начинать вёрстку страниц аналитики.

## Общее

- **Доступ:** право `canViewReports` (руководитель/топ-менеджер). Без него — `403`.
- **Scope:** обычный руководитель видит **только свой отдел**; top-manager — все отделы.
- **Период (общие query-параметры для всех эндпоинтов):**
  `createdFrom`, `createdTo` — ISO-8601 (UTC). Фильтр по дате **создания** заявки.
  Отсутствие обоих = «за всё время».
- **Все длительности — в секундах** (число) либо `null`, если данных нет.
  Блоки `{min, avg, max}` — каждое поле число или `null`.
- В каждом ответе есть мета: `scope` (`"all"`|`"department"`), `departmentId`
  (`string`|`null`), `period` (`{from,to}`|`null`).

---

## GET /analytics/applications

```jsonc
{
  "scope": "all", "departmentId": null, "period": null,
  "total": 58,
  "byStatus":     { "new": 26, "assigned": 15, "inProgress": 5, "delegated": 5, "completed": 5, "rejected": 3 },
  "byPriority":   { "low": 45, "medium": 6, "high": 6, "critical": 2 },
  "byComplexity": { "easy": 43, "medium": 9, "hard": 4, "critical": 3 },
  "completionTimeSeconds":      { "min": 0, "avg": 328320, "max": 604800 }, // завершённые: finished − created
  "timeToAssignSeconds":        { "min": 0, "avg": 7200, "max": 172800 },   // создание → первое назначение
  "timeWithoutExecutorSeconds": { "min": 0, "avg": 5400, "max": 86400 },    // суммарное время в статусе `new` (распределение+перераспределение)
  "timePerStatusSeconds": {            // среднее/мин/макс время в каждом статусе (по журналу)
    "new":        { "min": 0, "avg": 1200, "max": 8000 },
    "assigned":   { "min": 0, "avg": 3000, "max": 20000 },
    "inProgress": { "min": 0, "avg": 50000, "max": 200000 }
    // ключи присутствуют только для статусов, которые встречались
  },
  "delegations": {
    "total": 2, "confirmed": 0, "declined": 1, "pending": 1,
    "decisionTimeSeconds": { "min": null, "avg": null, "max": null } // created → decided делегирования
  }
}
```

## GET /analytics/executors

```jsonc
{
  "scope": "all", "departmentId": null, "period": null,
  "executors": [
    {
      "employeeId": "2", "fullName": "Иванов Иван Иванович", "departmentId": "1",
      "assignedCount": 28,      // заявок, где сотрудник — исполнитель (в scope/период)
      "completedCount": 3,
      "inProgressCount": 0,
      "takenInWorkCount": 4,    // сколько раз брал в работу (журнал)
      "rejectedCount": 1,       // сколько раз отклонял (журнал)
      "delegatedCount": 7,      // делегирований (внутр.+внеш., журнал)
      "byPriority": { "low": 25, "medium": 2, "high": 1, "critical": 0 },
      "avgReactionTimeSeconds": 28800, // назначен → взял в работу (work_at − executor_at)
      "avgHandlingTimeSeconds": 57600, // взял в работу → завершил
      "totalWorkSeconds": 172800,      // суммарное время в работе по завершённым
      "idleTimeSeconds": null,         // ⏳ планируется (см. ниже)
      "occupancyRatio": null           // ⏳ планируется (см. ниже)
    }
  ]
}
```

## GET /analytics/work-types

```jsonc
{
  "scope": "all", "departmentId": null, "period": null,
  "workTypes": [
    {
      "workTypeId": "1", "name": "Замена оборудования", "departmentId": "1",
      "createdCount": 10, "completedCount": 4, "delegatedCount": 1,
      "byPriority": { "low": 6, "medium": 2, "high": 1, "critical": 1 },
      "avgCompletionTimeSeconds": 86400,
      "topExecutorId": "2",                 // чаще всего назначаемый исполнитель (null если нет)
      "topExecutorName": "Иванов Иван Иванович"
    }
  ]
}
```

## GET /analytics/departments

```jsonc
{
  "scope": "all", "departmentId": null, "period": null,
  "departments": [
    {
      "departmentId": "1", "name": "ИТ-отдел",
      "employeeCount": 6,            // активных сотрудников
      "applicationCount": 58,
      "completedCount": 3,
      "avgReactionTimeSeconds": 31050, // среднее «назначен → взял в работу» по отделу
      "idleTimeSeconds": null,         // ⏳ планируется
      "occupancyRatio": null,          // ⏳ планируется
      "delegations": { "sent": 8, "received": 2 }
    }
  ]
}
```

---

## Что пока возвращается как `null` (планируется)

`idleTimeSeconds` (время простоя) и `occupancyRatio` (доля занятости) у исполнителей и
отделов **пока всегда `null`**. Корректный расчёт требует помодельного таймлайна занятости
сотрудника с обрезкой по окну периода — добавим отдельно. Поля уже присутствуют в ответе
с типом «число|null», поэтому верстать под них можно сразу (показывать «—» при `null`).

Если по ходу вёрстки нужны доп. поля или другая форма — скажите, согласуем и добавим.
