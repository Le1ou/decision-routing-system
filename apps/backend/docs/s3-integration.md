# Хранилище вложений (S3) без банковской карты

Документ описывает, как включить хранение вложений заявок в S3-совместимом
хранилище, не имея платёжной карты (Cloudflare R2 и AWS требуют карту при
регистрации). Рекомендуемый вариант — локальный **MinIO**, который уже добавлен
в `docker-compose.local.yml` и не требует ни аккаунта, ни карты.

## Как это работает в коде

- Вложения загружаются на backend (`POST /applications/{id}/attachments`,
  `multipart/form-data`). Backend кладёт файлы в S3 и сохраняет метаданные в
  таблицу `photo`.
- В карточке заявки (`GET /applications/{id}`) поле `attachments[].url` содержит
  **presigned-ссылку** (временную, на 1 час) — по ней файл скачивается напрямую
  из хранилища. Frontend с S3 напрямую не работает.
- Хранилище настраивается переменными окружения:

| Переменная | Назначение |
|------------|------------|
| `S3_BUCKET_NAME` | Имя бакета. **Если пусто — работа с вложениями отключена** (заявки работают, файлы не отдаются). |
| `S3_ENDPOINT_URL` | Адрес S3-совместимого хранилища. |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Ключи доступа. |
| `S3_REGION` | Регион (для MinIO подойдёт `us-east-1`). |
| `S3_FORCE_PATH_STYLE` | `true` для self-hosted S3 (MinIO): адресация вида `http://endpoint/bucket/key`. Для облачных провайдеров обычно не нужно. |

Эти переменные уже проброшены в backend в `docker-compose.local.yml`.

## Быстрый старт: локальный MinIO

MinIO **поднимается автоматически вместе со всем стеком** — отдельных команд и
профилей не требуется. В compose описаны два сервиса:

- `minio` — само хранилище (S3 API на порту `9000`, веб-консоль на `9001`);
- `createbuckets` — одноразовый помощник: создаёт бакет и завершает работу.
  Backend стартует только после него (`service_completed_successfully`), поэтому
  бакет гарантированно существует к моменту сидирования.

### Шаг 1. `.env` (значения по умолчанию уже настроены на MinIO)

`.env.example` уже содержит рабочую конфигурацию MinIO — просто скопируйте его в
`.env` (если ещё не сделали):

```dotenv
S3_BUCKET_NAME=decision-routing
S3_ENDPOINT_URL=http://minio:9000
S3_PUBLIC_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY_ID=minioadmin
S3_SECRET_ACCESS_KEY=minioadmin
S3_REGION=us-east-1
S3_FORCE_PATH_STYLE=true
```

> `minioadmin/minioadmin` — учётные данные по умолчанию. Для не-локальной
> установки задайте свои.

### Шаг 2. Запустите стек

```bash
make up
# или: docker compose -f infra/compose/docker-compose.local.yml up -d
```

Поднимется в т.ч. `minio` и `createbuckets`. В логе `createbuckets` появится
`bucket decision-routing ready`, а в логе backend — `[seed] photo → done`
(демо-вложения залиты в MinIO). Готово: загрузка и выдача вложений работают.
Веб-консоль хранилища: http://localhost:9001 (логин/пароль — ключи из `.env`).

## Доступ к ссылкам из браузера (важный нюанс)

Backend использует два адреса:

- `S3_ENDPOINT_URL=http://minio:9000` — внутренний Docker-адрес для загрузки;
- `S3_PUBLIC_ENDPOINT_URL` — адрес, доступный браузеру, для подписи presigned URL.

Для локального запуска:

```dotenv
S3_PUBLIC_ENDPOINT_URL=http://localhost:9000
```

Для удалённого сервера:

```dotenv
S3_PUBLIC_ENDPOINT_URL=http://<SERVER_IP_OR_DOMAIN>:9000
```

Например: `S3_PUBLIC_ENDPOINT_URL=http://132.243.230.84:9000`.

После изменения `.env` пересоздайте backend-контейнер. Править hosts-файл не
требуется.

## Проверка работоспособности

```bash
# создать заявку
APP=$(curl -s -u orlova_m:Manager!1 -H "Content-Type: application/json" \
  -X POST http://127.0.0.1:3000/applications \
  -d '{"name":"S3 test","departmentId":"1","workTypeId":"1","deadlineAt":"2030-01-01T00:00:00Z","description":"test"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# загрузить файл
curl -s -u orlova_m:Manager!1 -X POST \
  "http://127.0.0.1:3000/applications/$APP/attachments" \
  -F "files=@apps/backend/test_img.jpg"

# в карточке появится attachments[].url с presigned-ссылкой
curl -s -u orlova_m:Manager!1 "http://127.0.0.1:3000/applications/$APP"
```

Файлы также видны в веб-консоли MinIO (http://localhost:9001) в бакете
`decision-routing`, папка `applications/<id>/`.

## Облачные S3-хранилища без карты (альтернативы MinIO)

Если нужен внешний (публичный) endpoint без self-hosting, существуют
S3-совместимые провайдеры с бесплатным тарифом, обычно не требующие карту при
регистрации (условия меняются — уточняйте на момент регистрации):

- **Storj DCS** — S3-совместимый шлюз, бесплатный объём.
- **Supabase Storage** — S3-совместимый доступ (endpoint + ключи) на бесплатном тарифе.
- **Backblaze B2** — S3-совместимый API, бесплатные 10 ГБ.

Для любого из них заполните `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`,
`S3_SECRET_ACCESS_KEY`, `S3_BUCKET_NAME`, `S3_REGION` из панели провайдера.
`S3_FORCE_PATH_STYLE` для облачных провайдеров обычно не нужен (оставьте пустым);
включайте только если ссылки/загрузка не работают из-за схемы адресации.

## Облачный Cloudflare R2 / AWS S3

Код полностью совместим и с ними — отличается только заполнение `.env`
(см. закомментированный «Вариант 2» в `.env.example`). Эти провайдеры требуют
банковскую карту при регистрации, поэтому здесь не основной путь.

## Отключение хранилища

Чтобы отключить работу с вложениями, задайте в `.env` пустое значение
`S3_BUCKET_NAME=`. Тогда:

- backend не будет использовать S3 (загрузка вложений не работает,
  `attachments[].url` = `null`);
- остальная функциональность не затрагивается;
- backend не упадёт, даже если S3 настроен неверно (сидирование демо-вложений
  просто пропускается с сообщением в логе).

> Контейнеры `minio`/`createbuckets` при этом всё равно стартуют вместе со
> стеком (они часть compose) — просто не используются. Если они не нужны совсем,
> удалите эти сервисы из `docker-compose.local.yml` либо останавливайте их
> вручную (`docker compose ... stop minio createbuckets`).

## Резервное копирование и релизный режим (S3)

Помимо вложений, S3 используется для подготовки к релизу (см. `config.json →
"startup"` и `src/backup_module.py`; ENV-переопределения `SEED_ON_START` /
`BACKUP_ON_SHUTDOWN` уже проброшены в compose):

- **Дамп БД при выключении** (`backup_on_shutdown`, по умолчанию включено): при
  аккуратной остановке backend снимает `pg_dump -Fc` и кладёт его в бакет под
  ключами `backups/db/<имя БД>-<UTC timestamp>.dump` и `backups/db/latest.dump`.
- **Восстановление из бэкапа** (`restore_from_backup` / `RESTORE_FROM_BACKUP`,
  по умолчанию выключено): однократный режим — на старте БД восстанавливается из
  `backups/db/latest.dump` (`pg_restore --clean --if-exists`), сидирование при
  успехе пропускается. После восстановления верните флаг в `false`. Вручную то же
  самое: `pg_restore -h <host> -U <user> -d <db> --clean --if-exists latest.dump`.
- **Снимок каталога пользователей** (`backups/state/user_directory.json`):
  onboarding-состояние по логинам (`inSystem`/`employee_id`/`role`, без паролей).
  Пишется при каждом изменении каталога (добавление/удаление/смена роли
  сотрудника) и при выключении.
- **Запуск без сидирования** (`seed_on_start=false` или `SEED_ON_START=false`) —
  релизный режим: БД не пересевается демо-данными и переживает рестарт; снимок
  каталога подгружается на старте, чтобы добавленные через API сотрудники не
  теряли привязку логин ↔ `employee_id`. При включённом сидировании (по
  умолчанию) снимок игнорируется — источник истины `config.json` + сид.

Если S3 не настроен (`S3_BUCKET_NAME=`) — бэкап и снимок просто пропускаются с
сообщением в логе, работе backend это не мешает.

## Диагностика

| Симптом | Причина | Решение |
|---------|---------|---------|
| В логе `[seed] photo → skipped (S3 upload failed: ...)` | Бакет не создан / неверные ключи / endpoint недоступен | Запустить `createbuckets`, проверить ключи и `S3_ENDPOINT_URL` |
| Загрузка 500 / ошибка `NoSuchBucket` | Бакет не существует | Создать бакет (`createbuckets` или вручную в консоли MinIO) |
| Ссылка `attachments[].url` не открывается в браузере | Хост endpoint недоступен из браузера | Добавить `127.0.0.1 minio` в hosts, либо использовать публичный адрес (см. раздел выше) |
| Ссылка вида `http://bucket.endpoint/...` не работает (MinIO) | Не включён path-style | Установить `S3_FORCE_PATH_STYLE=true` и перезапустить backend |
| backend не может залить файл | `S3_ENDPOINT_URL=http://localhost:9000` в Docker | Использовать `http://minio:9000` (или публичный адрес) |
