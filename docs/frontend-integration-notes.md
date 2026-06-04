# Интеграция фронтенда с бэкендом: что нужно знать

Документ для фронтенд-разработчика. Описывает контракт API, расхождения с
текущими mock-данными фронтенда и подводные камни интеграции. Источник истины по
контракту — `docs/openapi.yaml`; фактическая реализация — `apps/backend/src/main.py`.
Бэкенд приведён в соответствие с контрактом.

---

## 1. Базовые вещи

1.1. **Базовый URL**: задаётся через `VITE_API_URL` (по умолчанию
`http://localhost:3000`). В Docker backend слушает порт **3000**. При удалённом
открытии frontend автоматически заменяет `localhost` на hostname страницы.
Для серверного развёртывания всё равно рекомендуется явно задавать публичный
адрес, например `VITE_API_URL=http://192.168.0.25:3000`.

1.2. **Аутентификация — HTTP Basic Auth на КАЖДОМ запросе.** Токенов/сессий нет.
Фронтенд хранит логин/пароль (или готовый заголовок `Authorization: Basic ...`)
и шлёт его со всеми защищёнными запросами. При неверных данных — `401`.

1.3. Режим авторизации на бэкенде (`mock` по умолчанию или `ad`) для фронтенда
**прозрачен**: и там, и там это Basic Auth, различаются только учётные данные.
Подробности: `docs/ad-integration.md`.

1.4. **Все даты** — строки ISO 8601 в UTC (например `2026-05-21T08:30:00Z`).

1.5. **Все идентификаторы — строки.** Но значения это **строковые числа**
(`"1"`, `"2"`, `"12"`), а НЕ слаги (`"it"`, `"user-author"`), как сейчас в mock.
Не хардкодьте id — берите их из ответов API (списки отделов, видов работ,
сотрудников и т.д.). Считайте id непрозрачными.

---

## 2. Вход и текущий пользователь

`GET /auth/me` → 
```json
{
  "user": {
    "id": "1", "login": "orlova_m", "fullName": "Орлова Мария Викторовна",
    "roles": ["author","executor","manager","top-manager"],
    "departmentId": "1", "postName": "Руководитель", "positionId": "3",
    "isActive": true
  },
  "permissions": {
    "canManageEmployees": true, "canManageWorkTypes": true,
    "canManagePrioritySettings": true, "canViewReports": true
  }
}
```

**Важно (расхождение с mock):**

- У пользователя **`roles` — массив**, а не одиночная `role`. Роли кумулятивны:
  `author ⊂ executor ⊂ manager ⊂ top-manager`. «Главную» роль для отображения
  вычисляйте как наивысшую из `roles`.
- **Гейтинг интерфейса делайте по `permissions`**, а не по угадыванию роли.
  Кнопки/разделы «Сотрудники», «Виды работ», «Приоритеты», «Отчёты» показывайте
  по `canManageEmployees` / `canManageWorkTypes` / `canManagePrioritySettings` /
  `canViewReports`.
- Различие `manager` vs `top-manager` — это **область видимости** (свой отдел
  против всех отделов), см. п.9.
- `postName`/`positionId` — должность сотрудника (приходит из AD). Отдельного
  справочника должностей для отображения подтягивать не нужно — имя уже в `postName`.

---

## 3. Главные расхождения с текущим mock фронтенда

Это нужно поправить на стороне фронтенда при переходе на реальный API:

| Сейчас в mock (`domain.ts`) | В контракте/API | Действие |
|------------------------------|------------------|----------|
| `User.role` (одна роль) | `roles[]` + `permissions` | Перейти на массив ролей и права |
| `User.jobTitleId` + справочник `JobTitle` | `positionId` + `postName` на пользователе | Показывать `postName`, отказаться от концепта `JobTitle`/`jobTitles` |
| `Application.title` | `name` | Переименовать поле |
| `Application.attachmentNames: string[]` | `attachments: Attachment[]` в карточке | Рендерить `attachments[].name`, файлы грузить отдельным запросом (п.7) |
| `AdUser.adPostName` | `postName` | Переименовать |
| Локальный расчёт доступных действий | `application.availableActions` с бэкенда | Использовать серверное поле (п.6) |
| Локальная фильтрация заявок по роли | Список уже отфильтрован сервером | Не фильтровать роль локально, маппить имена фильтров (п.5) |

---

## 4. Справочники и термины (важно не перепутать)

- `GET /departments` → отделы: `{id,name,value,delegatedToSameDepartment,
  employeeApplicationDelayMinutes,deadlineNotificationRatio}`. Коэффициенты
  `value`/`deadlineNotificationRatio` — в диапазоне 0..1.
- `GET /positions` → **должности** (`Position{id,name}`). Это `postName`/
  `positionId` сотрудника. Приходят из AD, вручную не редактируются.
- `GET /grades` → **позиции** для матрицы видов работ (`Grade{id,name}`).
  В русском UI поле `allowedGradeIds` показывается как «допустимые **позиции**».
- `GET /work-types` → виды работ: `{id,name,departmentId,complexity,allowedGradeIds[]}`.
- `GET /ad/users` → пользователи AD, которых можно добавить в систему:
  `{adUserId,login,fullName,departmentId,postName}`.

> ⚠️ Терминологическая ловушка: «должность» сотрудника = `positionId`/`postName`
> (endpoint `/positions`), а «позиции» в матрице вида работ = `grades`/
> `allowedGradeIds` (endpoint `/grades`). Несмотря на похожие слова в UI — это
> **разные сущности и разные эндпоинты**.

---

## 5. Список заявок: `GET /applications`

Ответ: `{ items: ApplicationListItem[], pagination: {page,pageSize,total} }`,
где `ApplicationListItem = {id,name,status,priority,createdAt,finishedAt?}`.

Query-параметры: `status`, `priority`, `createdByMe`, `assignedToMe`,
`delegatedToMyDepartment`, `executorName`, `applicationId`,
`sortBy` (`priority|status|createdAt|finishedAt`), `sortDirection` (`asc|desc`),
`page`, `pageSize` (1..100).

**Особенности:**

- **Видимость определяется сервером по роли** — не нужно фильтровать локально:
  - автор/исполнитель видит только заявки, где он автор или исполнитель;
  - руководитель — заявки своего отдела + делегированные в его отдел;
  - top-manager — все заявки.
- Архивные (`archivedAt`) и отклонённые старше 7 дней в список не попадают.
- **Маппинг имён фильтров** (mock → API): `executorQuery` → `executorName`,
  `applicationIdQuery` → `applicationId`, `delegatedFromAnotherDepartment` →
  `delegatedToMyDepartment`. `createdByMe`/`assignedToMe` совпадают.
- В элементе списка нет `authorId`/`executorId` — это поля карточки (п.6).

---

## 6. Карточка заявки и действия

`GET /applications/{id}` → `{ application: ApplicationDetail }`. Ключевые поля:
`description, departmentId, workTypeId, authorId, executorId?, previousExecutorId?,
executorComment?, managerComment?, resultText?, archivedAt?, delegationId?,
delegatedFromDepartmentId?, delegatedToDepartmentId?, assignedComplexity?,
assignedAt?, startedAt?, finishedAt?, closedById?, isUnfinished, deadlineAt,
updatedAt, availableActions[], attachments[], delegation?, author?, executor?,
department?, workType?`.

- `author`, `executor`, `department`, `workType` — **вложенные объекты** (можно
  не догружать из справочников). `assignedComplexity` может быть пустым — тогда
  берите `workType.complexity`.
- `resultText` («Результат работы») показывайте только для `completed`.

### 6.1. Действия: `POST /applications/{id}/actions` → 204

Тело: `{ action, executorId?, departmentId?, workTypeId?, comment?, complexity?,
resultText?, description? }`.

**Главное правило:** показывайте и отправляйте **только действия из
`availableActions`** карточки. Не дублируйте логику доступности на фронте —
бэкенд считает её сам (по статусу, роли и вовлечённости пользователя). Любое
действие не из списка вернёт `403 "Action not permitted in current state"` —
это нормальная защита, а не баг.

Кто какие действия получает (для понимания UX):

| Статус | Руководитель/top-manager (в своём отделе) | Назначенный исполнитель | Автор |
|--------|--------------------------------------------|--------------------------|-------|
| new | assignExecutor, delegateExternal, editDescription, changeWorkType, cancel | — | editDescription, cancel |
| assigned | assignExecutor, delegateExternal, reject, returnToNew | startWork, reject, delegateInternal, delegateExternal | — |
| inProgress | assignExecutor, reject, returnToNew | complete, reject, delegateInternal | — |
| delegated | assignExecutor, confirmExternalDelegation, declineExternalDelegation | — | — |
| completed / rejected | archive | — | — |

Обязательные поля по действиям:

- `assignExecutor` → `executorId`
- `delegateExternal` → `departmentId`
- `changeWorkType` → `workTypeId`
- `complete` → `resultText`
- `editDescription` → `description`
- `delegateInternal` → `complexity` (не ниже текущей)
- остальные (`cancel`, `archive`, `startWork`, `reject`, `returnToNew`,
  `confirmExternalDelegation`, `declineExternalDelegation`) — без обязательных
  полей; `comment` опционален.

**Важные нюансы:**

- `complete` — действие **исполнителя** (того, кто назначен), и только в
  `inProgress`. Руководитель «завершить» чужую заявку не может (это by design —
  «исполнитель закрывает заявку»). Не рисуйте кнопку «Завершить» руководителю —
  её и не будет в `availableActions`.
- Внутреннее vs внешнее делегирование:
  - `delegateInternal` — исполнитель не справляется/не его компетенция внутри
    своего отдела; задаёт сложность (≥ текущей), опционально меняет вид работ;
    заявка → `new` (или сначала `delegated` на подтверждение руководителя, если у
    отдела включён флаг `delegatedToSameDepartment`).
  - `delegateExternal` — работа не относится к отделу; уходит в другой отдел с
    подтверждением принимающего руководителя; доступно из `assigned`.
- `comment` сохраняется в `managerComment` (если действие выполнил руководитель)
  или `executorComment` (если исполнитель).
- После действия (204) перезапросите карточку `GET /applications/{id}`, чтобы
  получить новый статус и новый `availableActions` (тело у действия не возвращается).

---

## 7. Вложения

- При создании заявки (`POST /applications`) файлы **не** передаются. Сначала
  создаём заявку (получаем `id`), затем грузим файлы отдельным запросом:
  `POST /applications/{id}/attachments`, `multipart/form-data`, поле `files`
  (можно несколько). Ответ: `{ items: [{id}] }`.
- В карточке `attachments[]` = `{id, applicationId, name, type, url?}`.
- **`url` — временная presigned-ссылка (≈1 час).** Не кэшируйте её надолго; для
  показа файла перезапрашивайте карточку. Фронтенд с хранилищем напрямую не
  работает — только по `url` из ответа.
- Для локального MinIO backend загружает файлы через внутренний
  `S3_ENDPOINT_URL=http://minio:9000`, а ссылки для браузера подписывает через
  отдельный `S3_PUBLIC_ENDPOINT_URL`. Локально это `http://localhost:9000`, на
  удалённом сервере — публичный IP/домен MinIO, например
  `http://192.168.0.25:9000`.

---

## 8. Сотрудники, отделы, виды работ, приоритеты

- `GET /employees` (`{id,login,fullName,role,departmentId,postName,positionId,isActive}`),
  query: `departmentId`, `isActive`, `role`. **У сотрудника одиночная `role`**
  (в отличие от `roles[]` у текущего пользователя).
- `POST /employees` (нужно право `canManageEmployees`): тело
  `{adUserId, role, isActive}`. Сотрудник выбирается из `GET /ad/users`; должность
  приходит из AD и **из UI не передаётся**.
- `PATCH /employees/{id}`: `{role?, isActive?}` (хотя бы одно поле).
- `DELETE /employees/{id}`: удаляет участие в системе (не из AD); после этого
  пользователь снова появляется в `GET /ad/users`.
- `PATCH /departments/{id}/delegation-settings`: `{delegatedToSameDepartment}`.
- `POST /work-types` / `PATCH /work-types/{id}` / `DELETE /work-types/{id}`:
  поле допустимых позиций — **`allowedGradeIds`** (не `allowedPositionIds`).
  Удаление вида работ, на который ссылаются заявки → `409`.
- `GET /priority-settings` (право `canManagePrioritySettings`): 
  `{ department: {departmentId: коэф}, managerAuthor: {departmentId: коэф}, deadline: число }`.
  Обычный руководитель видит только свой отдел (чтение), `top-manager` — все.
  `PUT /priority-settings` — **только top-manager**.
  Фактора «должность автора» в приоритете нет. Формула:
  `коэф. отдела * коэф. срока + коэф. руководителя-автора`.
  ⚠️ Предпросмотр приоритета на фронте — иллюстративный: реальный приоритет
  считает бэкенд при создании заявки, отдельного endpoint предпросмотра нет.

---

## 9. Доступ по отделам (важно для UX)

- Обычный `manager` видит и редактирует **только свой отдел** в `/employees`,
  `/work-types`, `/applications`, `/priority-settings`,
  `/departments/{id}/delegation-settings`.
- `top-manager` — все отделы.

Не предполагайте, что обычному руководителю доступны все отделы: API вернёт ему
только его отдел (или `403` при попытке действия вне отдела). Селекторы отделов
для обычного руководителя стоит ограничивать его отделом.

---

## 10. Уведомления и отчёты

- `GET /notifications?unreadOnly=` → `{items:[{id,text,applicationId?,createdAt,isRead}], unreadCount}`.
  `POST /notifications/{id}/read` → 204; `POST /notifications/read-all` → 204.
  Модель pull: периодически опрашивайте или обновляйте после действий.
- `GET /reports/applications` (право `canViewReports`), фильтры: `createdFrom`,
  `createdTo`, `finishedFrom`, `finishedTo`, `status`, `executorId` → 
  `{items:[...], summary:{total,completed,inProgressOrAssigned}}`.
- `GET /reports/applications.xls` — готовый XLS-файл (теми же фильтрами),
  генерируется на бэкенде.

---

## 11. Коды ошибок

| Код | Значение | Реакция UI |
|-----|----------|------------|
| 400 | Не передано обязательное поле действия / неизвестный `action` | Показать валидацию |
| 401 | Не передан/неверный Basic Auth | На экран входа |
| 403 | Нет прав, действие недоступно в текущем статусе, или объект вне отдела | Скрывать недоступное заранее (по `permissions`/`availableActions`); сообщение |
| 404 | Объект не найден | Сообщение |
| 409 | Конфликт (напр., удаление используемого вида работ; дубликат) | Сообщение |
| 422 | Ошибка валидации полей (pydantic). Может вернуть `{code,message,fields[]}` | Подсветить поля |

---

## 12. Известные ограничения / возможные проблемы

1. **Блок «Кто делегировал».** В карточке для делегирования приходит только
   `delegation.delegatedByEmployeeId` (id), без вложенного объекта сотрудника.
   Аналогично `previousExecutorId` — только id. Если этот сотрудник из **другого
   отдела**, его ФИО/должность нельзя получить через `GET /employees` (он
   ограничен отделом текущего руководителя). Сейчас полноценно показать ФИО/отдел/
   должность делегировавшего из другого отдела **нельзя**. Это согласованный
   технический разрыв — при необходимости бэкенд добавит вложенные объекты
   `delegatedByEmployee`/`previousExecutor`. Обсудите, нужно ли это для UI.
2. **`complete` только для назначенного исполнителя** (см. п.6) — кнопки
   «Завершить»/«Взять в работу» руководителю не показывать.
3. **Presigned-ссылки вложений временные** и в локальном MinIO требуют записи в
   hosts (п.7).
4. **Нет предпросмотра приоритета** на сервере (п.8).
5. **Basic Auth на каждом запросе** — нет refresh/logout на сервере; «выход» =
   забыть учётные данные на клиенте.
6. **Видимость/действия — источник истины на сервере.** Не воспроизводите матрицу
   прав на фронте как источник истины: используйте `permissions` и
   `availableActions`. Локальные копии правил быстро разойдутся с бэкендом.

### Решения после frontend-ревью

1. **Блок «Кто делегировал» нужен в UI.** До расширения контракта frontend
   показывает доступные данные или `-`. Для полного отображения сотрудника из
   другого отдела backend должен добавить в карточку вложенные объекты
   `delegatedByEmployee` и `previousExecutor`.
2. **Действия заявки** отображаются строго из `availableActions`. Frontend не
   показывает `complete`/`startWork`, если backend их не вернул.
3. **Комментарии действий опциональны**, как указано в контракте. Frontend
   валидирует только действительно обязательные поля.
4. **Предпросмотр приоритета остаётся иллюстративным.** Источник итогового
   приоритета — backend; отдельный endpoint предпросмотра пока не требуется.
5. **Basic Auth принимается как текущая схема интеграции.** Выход очищает
   сохранённые frontend credentials.

### Требуется решение backend

1. **Виды работ при создании заявки.** `GET /departments` возвращает автору все
   отделы, но текущая реализация `GET /work-types` ограничивает любого
   non-top-manager только собственным отделом. Поэтому автор может выбрать
   другой отдел, но не может выбрать его вид работ и создать корректную заявку.
   Для формы создания заявки frontend должен получать виды работ всех доступных
   для маршрутизации отделов. Ограничение на свой отдел следует применять к
   управлению справочником, но не к чтению справочника при создании заявки.

---

## 13. Рекомендуемый порядок подключения

1. Логин + `GET /auth/me` → сохранить `user`/`permissions`, построить навигацию.
2. Справочники: `/departments`, `/work-types`, `/grades`, `/positions`,
   `/ad/users` (под доступные права).
3. Список и карточка заявок: `/applications`, `/applications/{id}`; рендер
   действий строго по `availableActions`.
4. Действия по заявке + перезапрос карточки.
5. Вложения (загрузка + показ по `url`).
6. Экраны управления (сотрудники, виды работ, приоритеты, отчёты) — по правам.
7. Уведомления.

Источник контракта: `docs/openapi.yaml`. Связанные документы:
`docs/ad-integration.md` (аутентификация AD), `docs/s3-integration.md` (вложения).
