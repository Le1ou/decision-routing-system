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
      "idleTimeSeconds": 43200,        // время простоя в окне анализа (window − busy); null если окна нет
      "occupancyRatio": 0.62           // доля занятости [0..1] = busy / window; null если окна нет
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
      "idleTimeSeconds": 259200,       // суммарный простой активных исполнителей отдела (Σ window − Σ busy)
      "occupancyRatio": 0.41,          // занятость отдела = Σ busy / Σ window по его исполнителям; null если нет
      "delegations": { "sent": 8, "received": 2 }
    }
  ]
}
```

---

## Простой и занятость (`idleTimeSeconds` / `occupancyRatio`)

Считаются по таймлайну удержания заявок исполнителем:

- **Занятость (busy)** исполнителя — сумма интервалов `[executor_at, конец]` по его
  заявкам, где `конец` = `finished_at` (завершено) / `now()` (в работе/назначено) /
  `COALESCE(finished_at, updated_at)` (отклонено). При модели «одна заявка на исполнителя»
  интервалы не пересекаются.
- **Окно анализа (window)** — общее для выборки: `[createdFrom | самое раннее executor_at
  в scope, createdTo | now()]`. Интервалы занятости обрезаются по окну.
- `occupancyRatio = busy / window` (в диапазоне `[0..1]`), `idleTimeSeconds = window − busy`.
- **Отдел:** `occupancyRatio = Σ busy / Σ window`, `idleTimeSeconds = Σ window − Σ busy`
  по активным исполнителям отдела.
- Если окно пустое (нет данных по исполнителям в scope) — оба поля `null` (показывать «—»).

Если по ходу вёрстки нужны доп. поля или другая форма — скажите, согласуем и добавим.
