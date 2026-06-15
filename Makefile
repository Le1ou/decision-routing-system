.PHONY: up up-fast build down restart reseed logs logs-backend \
backend-logs frontend-logs ps bash-back bash-front \
seed test clean rebuild pull \
prod-up prod-up-fast prod-build prod-down prod-restart prod-logs \
prod-logs-backend prod-frontend-logs prod-ps prod-clean prod-rebuild

# Repo root .env
COMPOSE = docker compose -f infra/compose/docker-compose.local.yml
PROD_COMPOSE = docker compose --env-file .env -f infra/compose/docker-compose.prod.yml

# Build and start all containers
up:
	$(COMPOSE) up -d --build

# Start without rebuild
up-fast:
	$(COMPOSE) up -d

# Explicit rebuild command
build:
	$(COMPOSE) up -d --build

# Stop containers
down:
	$(COMPOSE) down

# Restart stack
restart:
	$(COMPOSE) down
	$(COMPOSE) up -d

# Recreate backend (reseeds DB)
reseed:
	$(COMPOSE) restart backend

# All logs
logs:
	$(COMPOSE) logs -f

# Backend logs
logs-backend:
	$(COMPOSE) logs -f backend

# Frontend logs
frontend-logs:
	$(COMPOSE) logs -f frontend

# Containers status
ps:
	$(COMPOSE) ps

# Open shell in backend
bash-back:
	$(COMPOSE) exec backend bash

# Open shell in frontend
bash-front:
	$(COMPOSE) exec frontend sh

# Run backend seed manually
seed:
	$(COMPOSE) exec backend python -m src.seed

# Run tests
test:
	$(COMPOSE) exec backend pytest

# Full cleanup
clean:
	$(COMPOSE) down -v --remove-orphans

# Full rebuild
rebuild:
	$(COMPOSE) down
	$(COMPOSE) up -d --build

# Pull latest changes
pull:
	git pull origin develop

# Build and start production stack
prod-up:
	$(PROD_COMPOSE) up -d --build

# Start production stack without rebuild
prod-up-fast:
	$(PROD_COMPOSE) up -d

# Explicit production rebuild command
prod-build:
	$(PROD_COMPOSE) up -d --build

# Stop production stack
prod-down:
	$(PROD_COMPOSE) down

# Restart production stack
prod-restart:
	$(PROD_COMPOSE) down
	$(PROD_COMPOSE) up -d

# Production logs
prod-logs:
	$(PROD_COMPOSE) logs -f

# Production backend logs
prod-logs-backend:
	$(PROD_COMPOSE) logs -f backend

# Production frontend logs
prod-frontend-logs:
	$(PROD_COMPOSE) logs -f frontend

# Production containers status
prod-ps:
	$(PROD_COMPOSE) ps

# Full production cleanup
prod-clean:
	$(PROD_COMPOSE) down -v --remove-orphans

# Full production rebuild
prod-rebuild:
	$(PROD_COMPOSE) down
	$(PROD_COMPOSE) up -d --build
