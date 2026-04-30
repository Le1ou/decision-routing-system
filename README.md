# Decision Routing System

Автоматизированная информационно-аналитическая система маршрутизации производственных заявок

## Структура проекта

- apps/frontend — frontend (React)
- apps/backend — backend (Python)
- infra — инфраструктура
- docs — документация

## Статус

Инициализация проекта

## Требования

- Docker Desktop
- Docker Compose

Проверка установки:
docker --version
docker compose version

## Переменные среды

Создайте файл `.env` на основе шаблона:
cp.env.example.env

Основные переменные:
DB_HOST=localhost
DB_PORT=5432
DB_USER=postgres
DB_PASSWORD=postgres
DB_NAME=app_db
BACKEND_PORT=3000
VITE_API_URL=http://localhost:3000

## Запуск базы данных

docker compose -f infra/compose/docker-compose.local.yml up -d

База данных доступна по параметрам:
Host: localhost
Port: 5432

## Остановка контейнеров:

docker compose -f infra/compose/docker-compose.local.yml down

## Удаление вместе с данными:

docker compose -f infra/compose/docker-compose.local.yml down -v

## Подключение к базе данных

Host: localhost
Port: 5432
User: postgres
Password: postgres
Database: app_db

Или через Docker:
docker exec -it project_db psql -U postgres -d app_db

## Current status
PostgreSQL через Docker
Docker Compose настроен
Переменные окружения описаны
Backend (в разработке)
Frontend (в разработке)