# ─────────────────────────────────────────────────────────────
# Autoflow – Makefile
# ─────────────────────────────────────────────────────────────

COMPOSE   = docker compose
SERVICES  =

# ── SOPS / Age secrets ──────────────────────────────────────────────────────
SOPS_AGE_KEY_FILE ?= $(HOME)/.config/sops/age/keys.txt
export SOPS_AGE_KEY_FILE

# ── EE config (override on CLI: make ee-build EE=security VERSION=1.2.0) ──
EE       ?= base
VERSION  ?= latest
REGISTRY ?= localhost:3001
GITEA_USER ?= admin

.PHONY: help start stop restart logs status build pull setup \
        backup restore monitoring-up monitoring-down \
        ee-build ee-push ee-build-push ee-list \
        secrets-encrypt secrets-decrypt secrets-edit secrets-check

help:           ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Lifecycle ────────────────────────────────────────────────

start:          ## Start all services (auto-decrypts .env.enc if .env is missing)
	@if [ ! -f .env ] && [ -f .env.enc ]; then \
		echo "  [sops] .env not found — decrypting .env.enc..."; \
		$(MAKE) secrets-decrypt; \
	fi
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

setup:          ## Bootstrap: decrypt .env.enc → .env  (or copy .env.example if no encrypted file)
	@if [ -f .env.enc ]; then \
		echo "  [sops] Decrypting .env.enc → .env"; \
		$(MAKE) secrets-decrypt; \
		echo "  .env ready."; \
	elif [ ! -f .env ]; then \
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

# ── Deploy Wizard ────────────────────────────────────────────

WIZARD_VENV := .wizard-venv

wizard:         ## Launch the deployment wizard on http://localhost:9000
	@[ -d $(WIZARD_VENV) ] || python3 -m venv $(WIZARD_VENV)
	@$(WIZARD_VENV)/bin/pip install -q -r services/deploy-wizard/requirements.txt
	@echo ""
	@echo "  ╔══════════════════════════════════════════╗"
	@echo "  ║   Autoflow Deploy Wizard                 ║"
	@echo "  ║   http://localhost:9000                  ║"
	@echo "  ║   Press Ctrl+C to stop                   ║"
	@echo "  ╚══════════════════════════════════════════╝"
	@echo ""
	@AUTOFLOW_ROOT=$(PWD) $(WIZARD_VENV)/bin/uvicorn main:app \
		--host 0.0.0.0 --port 9000 \
		--app-dir services/deploy-wizard \
		--log-level info

# ── Secrets (SOPS + Age) ─────────────────────────────────────

secrets-encrypt: ## Encrypt .env → .env.enc  (commit .env.enc, never .env)
	@sops --encrypt --input-type dotenv --output-type dotenv .env > .env.enc
	@echo "  [sops] .env encrypted → .env.enc"
	@echo "  Commit .env.enc to git. Never commit .env."

secrets-decrypt: ## Decrypt .env.enc → .env
	@sops --decrypt --input-type dotenv --output-type dotenv .env.enc > .env
	@chmod 600 .env
	@echo "  [sops] .env.enc decrypted → .env"

secrets-edit:    ## Edit secrets in-place (re-encrypts automatically on save)
	@sops --input-type dotenv --output-type dotenv .env.enc

secrets-check:   ## Verify the Age key is present and .env.enc is decryptable
	@echo "  Checking Age key at $(SOPS_AGE_KEY_FILE)..."
	@test -f "$(SOPS_AGE_KEY_FILE)" || (echo "  ERROR: key not found at $(SOPS_AGE_KEY_FILE)" && exit 1)
	@echo "  Verifying .env.enc decryption..."
	@sops --decrypt --input-type dotenv --output-type dotenv .env.enc > /dev/null
	@echo "  OK — secrets are accessible."
