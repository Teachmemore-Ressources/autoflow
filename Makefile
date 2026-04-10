# ─────────────────────────────────────────────────────────
# Autoflow – Makefile
# ─────────────────────────────────────────────────────────

COMPOSE   = docker compose
SERVICES  =

.PHONY: help start stop restart logs status build pull setup

help:           ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ── Lifecycle ────────────────────────────────────────────

start:          ## Start all services in detached mode
	$(COMPOSE) up -d $(SERVICES)

stop:           ## Stop all services
	$(COMPOSE) down

restart:        ## Restart all (or specific) services  (e.g. make restart SERVICES=api)
	$(COMPOSE) restart $(SERVICES)

# ── Build / Update ───────────────────────────────────────

build:          ## Rebuild images (without cache)
	$(COMPOSE) build --no-cache $(SERVICES)

pull:           ## Pull latest upstream images
	$(COMPOSE) pull

# ── Observability ────────────────────────────────────────

logs:           ## Tail logs for all (or specific) services  (e.g. make logs SERVICES=api)
	$(COMPOSE) logs -f --tail=100 $(SERVICES)

status:         ## Show running containers and health
	$(COMPOSE) ps

# ── Setup ────────────────────────────────────────────────

setup:          ## Bootstrap: copy .env.example → .env (skips if .env already exists)
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo ""; \
		echo "  .env created from .env.example"; \
		echo "  Edit .env and set all required secrets, then run: make start"; \
		echo ""; \
	else \
		echo "  .env already exists – skipping."; \
	fi

# ── Utilities ────────────────────────────────────────────

shell:          ## Open a shell in a running container  (e.g. make shell SERVICES=api)
	$(COMPOSE) exec $(or $(SERVICES),api) /bin/sh

ps:             ## Alias for status
	$(COMPOSE) ps
