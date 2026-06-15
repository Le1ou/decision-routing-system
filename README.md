# Decision Routing System

Автоматизированная информационно-аналитическая система маршрутизации производственных заявок.

## Статус

Система включает frontend на React/Vite, backend на FastAPI, PostgreSQL, S3-совместимое хранилище MinIO и Docker Compose окружения для локального запуска и production.

## Структура проекта

- `apps/frontend` — frontend-приложение.
- `apps/backend` — backend API и серверная логика.
- `infra` — Docker Compose, Caddy и инфраструктурная конфигурация.
- `docs` — бизнес-документация, макеты, OpenAPI и инструкции по разработке.

## Быстрый запуск

```bash
cp .env.example .env
docker compose up -d --build
```

После запуска:

- Frontend: http://localhost:5173
- Backend: http://localhost:3000
- Swagger: http://localhost:3000/docs#/
- MinIO console: http://localhost:9001

Тестовые роли:

| Роль | Логин | Пароль |
|------|-------|--------|
| Автор | `novikova_e` | `Novikova!5` |
| Исполнитель | `ivanov_i` | `SecretPassword!1` |
| Руководитель отдела | `kuznetsov_m` | `Kuznetsov!7` |
| Топ-менеджер | `orlova_m` | `Manager!1` |

## Документация

- [Разработка и локальный запуск](docs/readme/development.md)
- [Production deploy](docs/readme/deployment.md)
- [Бизнес-требования](docs/requirements.md)
- [Роли и права](docs/roles-and-permissions.md)
- [Статусная модель](docs/status-model.md)
- [Виды работ](docs/type-of-work.md)
- [OpenAPI](docs/openapi.yaml)
- [Backend docs](apps/backend/docs)

## Git flow

- `main` — стабильное состояние.
- `develop` — основная ветка разработки.
- `feature/*` — фичи.
- `fix/*` — исправления.

Правила:

- напрямую в `main` не коммитим;
- рабочие ветки создаем от `develop`;
- `feature/*` и `fix/*` сливаем обратно в `develop` через PR;
- в `main` сливаем только рабочие версии.
