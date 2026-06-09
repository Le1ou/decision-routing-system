# Decision Routing System

Автоматизированная информационно-аналитическая система маршрутизации производственных заявок

## Структура проекта

- apps/frontend — frontend (React)
- apps/backend — backend (Python)
- infra — инфраструктура
- docs — документация

## Статус

Frontend интегрирован с локальным backend API. Docker Compose поднимает PostgreSQL, MinIO, backend и frontend для локальной разработки и проверки сценариев.

## Требования

- Docker Desktop
- Docker Compose

Проверка установки:
```bash
docker --version
docker compose version
```

## Переменные среды

Создайте файл `.env` на основе шаблона:
```bash
cp .env.example .env
```

Основные переменные:
```env
DB_HOST=db
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=app_db
BACKEND_PORT=3000
VITE_API_URL=http://localhost:3000
```

Для backend, запущенного локально вне Docker, используйте `DB_HOST=localhost`.

### Запуск на удалённом сервере

Для сервера укажите в `.env` адреса, доступные из браузера пользователя:

```env
VITE_API_URL=http://<SERVER_IP_OR_DOMAIN>:3000
S3_PUBLIC_ENDPOINT_URL=http://<SERVER_IP_OR_DOMAIN>:9000
```

Например:

```env
VITE_API_URL=http://132.243.230.84:3000
S3_PUBLIC_ENDPOINT_URL=http://132.243.230.84:9000
```

`localhost` в браузере означает компьютер пользователя, а не сервер. Frontend
также автоматически заменяет `localhost` из `VITE_API_URL` на hostname страницы
при удалённом открытии, но явная серверная конфигурация предпочтительнее.

После изменения `.env` пересоздайте контейнеры:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.prod.yml up -d --build
```

Production-стек использует `infra/compose/docker-compose.prod.yml`: frontend
собирается в статические файлы и отдаётся через nginx на `FRONTEND_PORT` (по
умолчанию `80`). Локальный стек `docker-compose.local.yml` оставляет Vite dev
server на порту `5173`.

## Запустить инфраструктуру (DB + Backend + Frontend)

```bash
docker compose --env-file .env -f infra/compose/docker-compose.local.yml up -d --build
```

Для запуска в foreground:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.local.yml up --build
```

## После запуска:

- Frontend: http://localhost:5173
- Backend: http://localhost:3000
- Swagger: http://localhost:3000/docs#/
- PostgreSQL: localhost:5432
- MinIO console: http://localhost:9001

## Тестовый вход

```text
Логин: orlova_m
Пароль: Manager!1
```

## Проверить backend

```bash
curl http://localhost:3000/health
```

Ожидаемый ответ: 
```json
{"status": "ok"}
```

## Остановка контейнеров:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.local.yml down
```

## Удаление вместе с данными:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.local.yml down -v
```

## Frontend (локальный запуск вне Docker)

Если frontend нужно запустить без Docker:

```bash
cd apps/frontend
npm install
npm run dev
```

URL backend для frontend:

```env
VITE_API_URL=http://localhost:3000
```

## Подключение к базе данных

Host: localhost
Port: 5432
User: postgres
Password: postgres
Database: app_db

Или через Docker:
docker exec -it project_db psql -U postgres -d app_db

## Как “запаковать” свои файлы в контейнер

Указать путь к .sql в разделе volumes.

Пример:
volumes:
      - postgres_data:/var/lib/postgresql/data
      - ../../apps/backend/sql_decision-routing.sql:/docker-entrypoint-initdb.d/001_init.sql

Важно:
Postgres выполнит SQL только при первом создании volume.
Для корекктной работы не использовать serial, а использовать integer

Было:
application_id serial NOT NULL GENERATED ALWAYS AS IDENTITY

Стало:
application_id integer NOT NULL GENERATED ALWAYS AS IDENTITY

И это нужно сделать ВО ВСЕХ таблицах

## Что нужно писать в Dockerfile (и порядок)

Dockerfile описывает, КАК собрать приложение в контейнере.

Правильный порядок шагов:
FROM python:3.11-slim

WORKDIR /app
1. Сначала зависимости (важно для кеша)
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
2. Потом код
COPY . .
3. Открываем порт
EXPOSE 3000
4. Запуск приложения
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "3000"]

Порядок важен
зависимости меняются редко → кешируются
код меняется часто → не ломает кеш pip install

## Как перенести библиотеки из локального environment в Docker

В Dockerfile перенести зависимости → через requirements.txt

Dockerfile:
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

Пример содержимого requirements.txt:
fastapi
uvicorn[standard]
psycopg[binary]
psycopg-pool
ldap3
pydantic

## CI/CD

CI запускается:

на pull request
на push в develop

Проверяет:

установку backend dependencies
компиляцию backend кода
сборку frontend
валидность local и production docker-compose конфигураций
сборку backend, frontend dev image и frontend production image

Deploy запускается только после успешного CI для ветки `develop` и применяет
`infra/compose/docker-compose.prod.yml` на сервере.

## Healthchecks

Backend:

GET /health

PostgreSQL:
managed via pg_isready

## Compose поднимает:

- db — PostgreSQL
- backend — FastAPI application
- frontend — React/Vite frontend

## Важные особенности
SQL init выполняется только при первом создании volume
Для пересоздания БД используйте down -v
Backend использует переменные окружения из .env
DB_HOST должен быть db внутри Docker

## Требования
Docker Desktop
Docker Compose
Node.js 20+ (для frontend dev)

## Проверка установки 
docker --version
docker compose version
node -v
