# Разработка и локальный запуск

## Требования

- Docker Desktop
- Docker Compose
- Node.js 20+ для локального запуска frontend вне Docker

Проверка установки:

```bash
docker --version
docker compose version
node -v
```

## Переменные среды

Создайте `.env` на основе шаблона:

```bash
cp .env.example .env
```

Основные переменные:

```env
COMPOSE_FILE=infra/compose/docker-compose.local.yml
DB_HOST=db
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=app_db
BACKEND_PORT=3000
VITE_API_URL=http://localhost:3000
```

`COMPOSE_FILE` позволяет запускать `docker compose ...` из корня репозитория без явных флагов `-f` и `--env-file`. Для backend, запущенного локально вне Docker, используйте `DB_HOST=localhost`.

## Запуск инфраструктуры

Из корня репозитория:

```bash
docker compose up -d --build
```

Foreground-режим:

```bash
docker compose up --build
```

Эквивалентный запуск с явными флагами:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.local.yml up -d --build
```

После запуска:

- Frontend: http://localhost:5173
- Backend: http://localhost:3000
- Swagger: http://localhost:3000/docs#/
- PostgreSQL: localhost:5432
- MinIO console: http://localhost:9001

## Проверка backend

```bash
curl http://localhost:3000/health
```

Ожидаемый ответ:

```json
{"status": "ok"}
```

## Остановка

```bash
docker compose --env-file .env -f infra/compose/docker-compose.local.yml down
```

Удаление вместе с данными:

```bash
docker compose --env-file .env -f infra/compose/docker-compose.local.yml down -v
```

## Frontend вне Docker

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

Параметры подключения:

```text
Host: localhost
Port: 5432
User: postgres
Password: postgres
Database: app_db
```

Через Docker:

```bash
docker exec -it project_db psql -U postgres -d app_db
```

SQL init выполняется только при первом создании volume. Для пересоздания БД используйте `down -v`.

## CI/CD

CI запускается на pull request и push в `develop`.

Проверки:

- установка backend dependencies;
- компиляция backend-кода;
- сборка frontend;
- валидность local и production Docker Compose конфигураций;
- сборка backend image, frontend dev image и frontend production image;
- backend-тесты внутри compose-окружения.

Deploy запускается только после успешного CI для ветки `develop` и применяет `infra/compose/docker-compose.prod.yml` на сервере.
