# Production deploy

Production-стек использует `infra/compose/docker-compose.prod.yml`: frontend собирается в статические файлы, отдаётся через nginx внутри Docker-сети, а публичный HTTPS-трафик принимает Caddy.

## Переменные среды

Для сервера укажите в `.env` адреса, доступные из браузера пользователя:

```env
COMPOSE_FILE=infra/compose/docker-compose.prod.yml
APP_DOMAIN=routeflow.ru
API_DOMAIN=api.routeflow.ru
S3_DOMAIN=s3.routeflow.ru
CADDY_EMAIL=admin@routeflow.ru
VITE_API_URL=https://api.routeflow.ru
S3_PUBLIC_ENDPOINT_URL=https://s3.routeflow.ru
```

Перед запуском создайте DNS A-записи на IP сервера:

```text
routeflow.ru      A  <SERVER_IP>
api.routeflow.ru  A  <SERVER_IP>
s3.routeflow.ru   A  <SERVER_IP>
```

На сервере должны быть открыты порты `80/tcp` и `443/tcp`. Caddy сам выпустит и будет обновлять HTTPS-сертификаты. MinIO Console остаётся закрытой снаружи и доступна через SSH tunnel на `127.0.0.1:9001`.

## Запуск

```bash
docker compose --env-file .env -f infra/compose/docker-compose.prod.yml up -d --build
```

После изменения `.env` пересоздайте контейнеры той же командой.

## Make-команды

На сервере удобно использовать production-команды:

```bash
make prod-ps
make prod-frontend-logs
make prod-rebuild
```

## Healthcheck

Backend:

```http
GET /health
```

PostgreSQL проверяется через `pg_isready`.
