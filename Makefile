.PHONY: up up-fast build down restart reseed logs logs-backend \
backend-logs frontend-logs ps bash-back bash-front \
seed test clean rebuild pull

# Repo root .env
COMPOSE = docker compose -f infra/compose/docker-compose.local.yml

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
