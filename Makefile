# --env-file .env: the repo-root .env (Compose otherwise looks next to the
# compose file, in infra/compose/). Run `make` from the repo root.
COMPOSE = docker compose --env-file .env -f infra/compose/docker-compose.local.yml

# Build images (picks up backend/seed code changes) and start the whole stack,
# including MinIO. Reads .env from the repo root.
up:
	$(COMPOSE) up -d --build

# Start without rebuilding (faster; use when no code changed).
up-fast:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

# Re-seed the database (backend wipes + reseeds on every start).
reseed:
	$(COMPOSE) restart backend

logs:
	$(COMPOSE) logs -f

# Just the backend log (handy to see the [seed] summary).
logs-backend:
	$(COMPOSE) logs -f backend
