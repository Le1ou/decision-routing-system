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
CMD ["python", "src/application_module.py"]

Порядок важен
зависимости меняются редко → кешируются
код меняется часто → не ломает кеш pip install

## Как перенести библиотеки из локального environment в Docker

В Dockerfile перенести зависимости → через requirements.txt

Dockerfile:
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

Пример содержимого requirements.txt:
fastapi==0.110.0
uvicorn==0.29.0
psycopg2-binary==2.9.9