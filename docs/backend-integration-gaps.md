# Backend integration gaps

Дата аудита: 2026-06-01

## Контекст

Frontend сейчас почти полностью работает на mock-данных. Основной контракт для интеграции находится в `docs/openapi.yaml`, фактическая реализация backend - в `apps/backend/src/main.py`.

По результатам аудита расхождений много, поэтому backend-логику в этой итерации не меняем. Этот файл фиксирует, что нужно привести к контракту перед полной frontend-интеграцией.

## Граница frontend/backend

Frontend сейчас не должен добавлять совместимость с текущими неправильными backend-именами. Подготовка frontend начинается после того, как backend приведет API к целевому контракту.

- В коде и API используем `grade` / `postGrade` / `allowedGradeIds`.
- В русском UI показываем термин "позиция".
- Frontend не поддерживает `/positions` как целевой endpoint.
- Frontend не поддерживает `allowedPositionIds` как целевое поле.
- Если backend временно оставит `position`-именование, это считается backend gap, а не frontend fallback-задачей.
- Frontend после backend-доработок может добавить тонкий API-client под `docs/openapi.yaml`, но без костылей под старую реализацию.
- Frontend может собирать карточку заявки из ids и справочников, если это будет явно согласовано в контракте. До согласования контракт по-прежнему ожидает detail-данные из `GET /applications/{applicationId}`.

Frontend не должен симулировать серверные правила прав доступа, архива, статусов, `availableActions` и приоритета как источник истины. Mock-мутации остаются временным UI-режимом до готовности backend endpoints.

## Критичные блокеры

### Роли и top-manager

- Endpoint: `GET /auth/me`, все защищенные endpoints.
- Контракт ожидает: роли `author`, `executor`, `manager`, `top-manager`; обычный руководитель ограничен своим отделом, `top-manager` видит и редактирует все.
- Факт backend: `RoleValues = ["author", "executor", "manager"]`; в mock-конфиге нет пользователя с ролью `top-manager`; `_get_user_role` выбирает только `manager > executor > author`.
- Блокер: да.
- Править: backend.
- Ожидаемое поведение: поддержать `top-manager` в auth, permissions и проверках видимости/редактирования.

### Архивирование и отмена заявки

- Endpoint: `POST /applications/{applicationId}/actions`.
- Контракт ожидает actions `cancel` и `archive`; `cancel` переводит новую заявку в `rejected`, отдельного статуса `cancelled` нет; `archive` не меняет статус, а заполняет `archivedAt`.
- Факт backend: `ActionValues` не содержит `cancel` и `archive`; для автора новой заявки доступен только `editDescription`; поля `archivedAt` в response нет.
- Блокер: да.
- Править: backend.
- Ожидаемое поведение: добавить `cancel`, `archive`, поле `archivedAt`, скрытие архивных заявок из основного списка.

### Фильтрация списков заявок

- Endpoint: `GET /applications`.
- Контракт ожидает: видимость заявок по роли, скрытие `archivedAt`, скрытие `rejected` через 7 дней, поддержку `delegatedToMyDepartment`.
- Факт backend: query не учитывает роль пользователя, `delegatedToMyDepartment` объявлен в параметрах route, но не используется в SQL; архивного поля нет; правило 7 дней для rejected не реализовано.
- Блокер: да.
- Править: backend.
- Ожидаемое поведение: backend должен возвращать только видимые текущему пользователю заявки и применять правила архива/скрытия.

### Справочник grade / UI-позиций

- Endpoint: `GET /grades`.
- Контракт ожидает: отдельный endpoint `/grades`, который возвращает `grade`/`post_grade` для матрицы допустимости. В русском UI эти сущности называются "позиции".
- Факт backend: доменная модель и SQL используют `grade`/`post_grade`, но API-слой частично переименовал это в `position`: `/positions`, `PositionOut`, `positionId`, `allowedPositionIds`.
- Блокер: да для экрана видов работ, если следовать контракту буквально.
- Править: backend.
- Ожидаемое поведение: привести API к доменному и контрактному имени `grade`: реализовать `/grades`, вернуть `allowedGradeIds`, использовать `gradeId`/`postGradeId` вместо `positionId` в API payload/response. Слово "позиция" должно оставаться только русской UI-меткой.

### Виды работ: allowedGradeIds и редактирование

- Endpoints: `GET /work-types`, `POST /work-types`, `PATCH /work-types/{workTypeId}`, `DELETE /work-types/{workTypeId}`.
- Контракт ожидает поле `allowedGradeIds`, создание и обновление с `allowedGradeIds`, `PATCH` для изменения вида работ.
- Факт backend: response возвращает `allowedPositionIds`; `CreateWorkTypePayload` не принимает `allowedGradeIds`; `PATCH /work-types/{workTypeId}` отсутствует.
- Блокер: да.
- Править: backend.
- Ожидаемое поведение: унифицировать имя поля с контрактом как `allowedGradeIds`, сохранять матрицу допустимых grade/post_grade, добавить PATCH. В UI отображать это как "допустимые позиции".

### Сотрудники: роль, удаление и AD-поля

- Endpoints: `GET /employees`, `POST /employees`, `PATCH /employees/{employeeId}`, `DELETE /employees/{employeeId}`.
- Контракт ожидает: у сотрудника одиночная `role`; при добавлении frontend передает `role`; должность приходит из AD и не задается вручную из UI; удаление из системы не удаляет из AD.
- Факт backend: `UserOut` возвращает `roles` массив; `CreateEmployeePayload` требует `positionId`, но не принимает `role`; `UpdateEmployeePayload` меняет `positionId`, но не `role`; `DELETE /employees/{employeeId}` отсутствует.
- Блокер: да для экрана сотрудников.
- Править: backend или контракт, но нужно одно решение.
- Ожидаемое поведение: привести payload/response к контракту, заменить `positionId` на согласованное grade-именование, добавить удаление из системы.

### Настройки делегирования отдела

- Endpoint: `PATCH /departments/{departmentId}/delegation-settings`.
- Контракт ожидает изменение `delegatedToSameDepartment`.
- Факт backend: endpoint отсутствует.
- Блокер: да для управления сотрудниками/отделом.
- Править: backend.

### Приоритеты

- Endpoint: `GET /priority-settings`, `PUT /priority-settings`.
- Контракт ожидает: `department` как map коэффициентов по departmentId, `deadline`, `managerAuthor`; фактора "должность автора" нет.
- Факт backend: in-memory модель содержит `department`, `position`, `workType`, `deadline`, `managerAuthor`, все как числа; доступ требует `canManagePrioritySettings`, из-за чего обычный руководитель не сможет просматривать свои коэффициенты.
- Блокер: да для экрана приоритетов.
- Править: backend.
- Ожидаемое поведение: убрать лишние факторы из API либо согласовать контракт; хранить настройки не только в памяти; разрешить read обычному руководителю по правилам контракта.

## Значимые расхождения карточки заявки

### Обогащение связанных сущностей

- Endpoint: `GET /applications/{applicationId}`.
- Контракт ожидает в detail: `author`, `executor`, `workType`, `department`, `attachments`, `delegation`.
- Факт backend: возвращает ids, `attachments`, `delegation`, но не возвращает вложенные `author`, `executor`, `workType`, `department`.
- Блокер: частичный. Frontend может догружать справочники и собирать карточку по id только после явного согласования контракта.
- Править: backend или контракт, но не молчаливый frontend fallback.

### Делегирование: кто делегировал

- Endpoint: `GET /applications/{applicationId}`.
- Контракт ожидает `delegation.delegatedByEmployeeId`, чтобы UI показал ФИО, отдел и должность сотрудника.
- Факт backend: `DelegationOut` содержит department ids, но не `delegatedByEmployeeId`.
- Блокер: да для блока "Кто делегировал".
- Править: backend.

### Комментарии по заявке

- Endpoint: `POST /applications/{applicationId}/actions`, `GET /applications/{applicationId}`.
- Контракт и UI ожидают `executorComment`, `managerComment`, `resultText`.
- Факт backend: модель response содержит эти поля, но action-логика почти не сохраняет комментарии; `complete` сохраняет только `resultText`.
- Блокер: частичный.
- Править: backend.

### Доступные действия

- Endpoint: `GET /applications/{applicationId}`.
- Контракт ожидает, что UI показывает только `availableActions`.
- Факт backend: `availableActions` считается упрощенно и не включает `cancel`, `archive`, `delegateInternal`, `returnToNew`; автор новой заявки не получает `cancel`.
- Блокер: да для корректной бизнес-логики UI.
- Править: backend.

## Справочники и ограничения доступа

### Отделы

- Endpoint: `GET /departments`.
- Контракт ожидает ограничения видимости: обычный руководитель видит свой отдел, `top-manager` все.
- Факт backend: возвращает все отделы любому авторизованному пользователю.
- Блокер: частичный, но важно для безопасности и UX.
- Править: backend.

### Сотрудники

- Endpoint: `GET /employees`.
- Контракт ожидает: обычный руководитель видит свой отдел, `top-manager` все.
- Факт backend: строит список из mock-конфига и не ограничивает по роли, если явно не передан `departmentId`.
- Блокер: частичный.
- Править: backend.

### Виды работ

- Endpoint: `GET /work-types`.
- Контракт ожидает: обычный руководитель видит/редактирует свой отдел, `top-manager` все.
- Факт backend: без `departmentId` возвращает все виды работ.
- Блокер: частичный.
- Править: backend.

## Окружение и проверки

- `python3 -m compileall apps/backend/src` проходит.
- `npm run build` в `apps/frontend` проходит.
- `docker compose --env-file .env.example -f infra/compose/docker-compose.local.yml config` проходит.
- Полный `docker compose up` и runtime `/health` в рамках аудита не запускались: статическая сверка уже выявила блокирующие контрактные расхождения.

## Что можно интегрировать во frontend после backend-решений

- `GET /auth/me` можно использовать как базу после добавления/согласования `top-manager`.
- `GET /applications`, `POST /applications`, `GET /applications/{id}` и часть actions можно подключать после исправления видимости, action enum и недостающих полей.
- `GET /departments`, `GET /ad/users`, `GET /notifications`, reports выглядят ближе к рабочим, но требуют проверки ограничений доступа и соответствия имен полей контракту.
- `GET /positions` не использовать как frontend fallback; целевой backend endpoint должен быть `/grades`.
- Экраны сотрудников, видов работ и приоритетов пока лучше не переводить на real API полностью: payload/response расходятся с контрактом.

## Что нельзя закрывать frontend-костылями

- `cancel`, `archive`, `archivedAt` и скрытие архивных/старых rejected заявок.
- Серверный расчет `availableActions`.
- Поддержка `top-manager` как серверной роли и правила видимости данных.
- Создание/редактирование сотрудников с role и удаление сотрудника из системы.
- Создание/редактирование видов работ с `allowedGradeIds`.
- Формула и хранение priority settings.
- Сохранение комментариев действий заявки.

## Рекомендуемый порядок backend-правок

1. Зафиксировать терминологию: в backend/API использовать `grade`/`postGrade`/`allowedGradeIds`, в русском UI показывать "позиция".
2. Добавить `top-manager` и правила видимости по ролям.
3. Довести actions заявок до контракта: `cancel`, `archive`, `availableActions`, комментарии, `archivedAt`.
4. Привести виды работ к `allowedGradeIds` и добавить `PATCH`.
5. Привести сотрудников к контракту: role в create/update, delete из системы, AD-должность без ручного ввода.
6. Переделать priority settings под контрактную формулу.
7. После этого повторить Swagger/OpenAPI diff и подключать frontend сценариями.
