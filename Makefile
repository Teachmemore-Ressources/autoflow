# ─────────────────────────────────────────────────────────────
# Autoflow – Makefile
# ─────────────────────────────────────────────────────────────

COMPOSE   = docker compose
SERVICES  =

# ── EE config (override on CLI: make ee-build EE=security VERSION=1.2.0) ──
EE       ?= base
VERSION  ?= latest
REGISTRY ?= localhost:3001
GITEA_USER ?= admin

.PHONY: help start stop restart logs status build pull setup \
        backup restore monitoring-up monitoring-down \
        ee-build ee-push ee-build-push ee-list

help:           ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Lifecycle ────────────────────────────────────────────────

start:          ## Start all services in detached mode
	$(COMPOSE) up -d $(SERVICES)

stop:           ## Stop all services
	$(COMPOSE) down

restart:        ## Restart all (or specific) services  (e.g. make restart SERVICES=api)
	$(COMPOSE) restart $(SERVICES)

# ── Build / Update ───────────────────────────────────────────

build:          ## Rebuild images (without cache)
	$(COMPOSE) build --no-cache $(SERVICES)

pull:           ## Pull latest upstream images
	$(COMPOSE) pull

# ── Observability ────────────────────────────────────────────

logs:           ## Tail logs for all (or specific) services  (e.g. make logs SERVICES=api)
	$(COMPOSE) logs -f --tail=100 $(SERVICES)

status:         ## Show running containers and health
	$(COMPOSE) ps

monitoring-up:  ## Start only the monitoring stack (Prometheus + Grafana + Alertmanager + exporters)
	$(COMPOSE) up -d prometheus grafana alertmanager postgres_exporter redis_exporter

monitoring-down: ## Stop the monitoring stack
	$(COMPOSE) stop prometheus grafana alertmanager postgres_exporter redis_exporter

# ── Backup / Restore ─────────────────────────────────────────

backup:         ## Backup PostgreSQL and Redis data  (output: ./backups/<timestamp>/)
	@bash scripts/backup.sh

restore:        ## Restore from a backup  (e.g. make restore BACKUP=./backups/2024-01-01_12-00-00)
	@if [ -z "$(BACKUP)" ]; then \
		echo "Usage: make restore BACKUP=./backups/<timestamp>"; \
		echo ""; \
		echo "Available backups:"; \
		ls -1t backups/ 2>/dev/null | head -20 | sed 's/^/  /' || echo "  (none)"; \
	else \
		bash scripts/restore.sh "$(BACKUP)"; \
	fi

# ── Setup ────────────────────────────────────────────────────

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

# ── Execution Environments ───────────────────────────────────

ee-build:       ## Build an EE image  (e.g. make ee-build EE=security VERSION=1.0.0)
	@echo "Building EE: $(EE) → $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION)"
	~/.local/bin/ansible-builder build \
		--file execution-environments/$(EE)/execution-environment.yml \
		--tag $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION) \
		--context /tmp/ee-build-$(EE) \
		--build-arg PYCMD=/usr/bin/python3.12 \
		--verbosity 1

ee-push:        ## Push a built EE image to Gitea registry  (e.g. make ee-push EE=security VERSION=1.0.0)
	@echo "Pushing EE: $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION)"
	docker push $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION)

ee-build-push:  ## Build + push in one step  (e.g. make ee-build-push EE=base VERSION=1.0.0)
	$(MAKE) ee-build EE=$(EE) VERSION=$(VERSION) REGISTRY=$(REGISTRY) GITEA_USER=$(GITEA_USER)
	$(MAKE) ee-push  EE=$(EE) VERSION=$(VERSION) REGISTRY=$(REGISTRY) GITEA_USER=$(GITEA_USER)

ee-list:        ## List available EE definitions
	@echo "Available Execution Environments:"
	@ls execution-environments/ | sed 's/^/  /'

# ── Utilities ────────────────────────────────────────────────

shell:          ## Open a shell in a running container  (e.g. make shell SERVICES=api)
	$(COMPOSE) exec $(or $(SERVICES),api) /bin/sh

ps:             ## Alias for status
	$(COMPOSE) ps
