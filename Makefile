up:
	docker compose -f infra/compose/docker-compose.local.yml up -d

down:
	docker compose -f infra/compose/docker-compose.local.yml down

logs:
	docker compose -f infra/compose/docker-compose.local.yml logs -f