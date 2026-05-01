# ─────────────────────────────────────────────────────────────
# Autoflow – Makefile
# ─────────────────────────────────────────────────────────────

COMPOSE   = docker compose
SERVICES  =

# ── SOPS / Age secrets ──────────────────────────────────────────────────────
SOPS_AGE_KEY_FILE ?= $(HOME)/.config/sops/age/keys.txt
export SOPS_AGE_KEY_FILE

# ── ansible-builder — cherche dans PATH, puis ~/.local/bin (fallback) ────────
ANSIBLE_BUILDER ?= $(shell which ansible-builder 2>/dev/null \
                    || echo ~/.local/bin/ansible-builder)

# ── Container runtime for EE builds ─────────────────────────────────────────
# ansible-builder auto-detects podman > docker. If both are installed, podman
# wins and `docker push` fails because the image is in the wrong store.
# Override: make ee-build CONTAINER_RUNTIME=docker   (or podman)
CONTAINER_RUNTIME ?= $(shell which podman >/dev/null 2>&1 && echo podman || echo docker)

# ── EE config (override sur CLI : make ee-build EE=security VERSION=1.2.0) ──
EE         ?= base
VERSION    ?= latest
GITEA_USER ?= admin

# Domaine lu depuis .env (fallback localhost) → registry = git.<DOMAIN>
_DOMAIN    := $(shell grep '^DOMAIN=' .env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo localhost)
REGISTRY   ?= git.$(_DOMAIN)

# ── AWX image config ─────────────────────────────────────────────────────────
AWX_VERSION  ?= $(shell grep '^AWX_VERSION=' .env 2>/dev/null | cut -d= -f2 || echo "24.6.1")
AWX_IMAGE    ?= autoflow/awx-patched
GITEA_REGISTRY ?= git.$(_DOMAIN)/$(GITEA_USER)

.PHONY: help start stop restart logs status build pull setup \
        backup restore monitoring-up monitoring-down \
        ee-build ee-push ee-build-push ee-list ee-network \
        ee-deps docker-trust-ca gitea-init-network gitea-init-runner \
        secrets-encrypt secrets-decrypt secrets-edit secrets-check \
        awx-build awx-push awx-pull awx-tag images-update \
        test test-unit test-integration test-e2e test-stack-up test-stack-down \
        cli-install cli-check

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
	@echo "Building EE: $(EE) → $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION)  [runtime: $(CONTAINER_RUNTIME)]"
	@test -x "$(ANSIBLE_BUILDER)" || \
		(echo "ERROR: ansible-builder introuvable. Installe-le : pip install --user ansible-builder" && exit 1)
	$(ANSIBLE_BUILDER) build \
		--file execution-environments/$(EE)/execution-environment.yml \
		--tag $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION) \
		--context /tmp/ee-build-$(EE) \
		--container-runtime $(CONTAINER_RUNTIME) \
		--build-arg PYCMD=/usr/bin/python3.12 \
		--verbosity 1

ee-push:        ## Push a built EE image to Gitea registry  (e.g. make ee-push EE=security VERSION=1.0.0)
	@echo "Pushing EE: $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION)  [runtime: $(CONTAINER_RUNTIME)]"
	$(CONTAINER_RUNTIME) push $(REGISTRY)/$(GITEA_USER)/ee-$(EE):$(VERSION)

ee-build-push:  ## Build + push in one step  (e.g. make ee-build-push EE=base VERSION=1.0.0)
	$(MAKE) ee-build EE=$(EE) VERSION=$(VERSION) REGISTRY=$(REGISTRY) GITEA_USER=$(GITEA_USER)
	$(MAKE) ee-push  EE=$(EE) VERSION=$(VERSION) REGISTRY=$(REGISTRY) GITEA_USER=$(GITEA_USER)

ee-list:        ## List available EE definitions
	@echo "Available Execution Environments:"
	@ls execution-environments/ | sed 's/^/  /'

# ── Network Automation ───────────────────────────────────────

gitea-init-network: ## Push network-playbooks repo to Gitea (run after: make start)
	@bash scripts/gitea-init-network.sh

gitea-init-runner:  ## Register act_runner in Gitea Actions (gets token + restarts container)
	@bash scripts/gitea-init-runner.sh

ee-network:     ## Build + push the network EE  (shortcut for EE=network)
	$(MAKE) ee-build-push EE=network VERSION=$(VERSION) REGISTRY=$(REGISTRY) GITEA_USER=$(GITEA_USER)

# ── Prérequis EE Build ───────────────────────────────────────

ee-deps:        ## Installer ansible-builder (pipx en priorité, compatible Debian/Ubuntu 22+)
	@echo "Installation d'ansible-builder pour $(USER)..."
	@if command -v ansible-builder >/dev/null 2>&1; then \
		echo "  ansible-builder déjà installé : $$(ansible-builder --version)"; \
	elif command -v pipx >/dev/null 2>&1; then \
		pipx install ansible-builder && echo "  Installé via pipx"; \
	elif pip3 install --user --break-system-packages ansible-builder 2>/dev/null; then \
		echo "  Installé via pip --break-system-packages"; \
	else \
		echo "  Installation de pipx puis ansible-builder..."; \
		python3 -m pip install --user --break-system-packages pipx 2>/dev/null || true; \
		python3 -m pipx install ansible-builder; \
	fi
	@echo ""
	@echo "  Ajoute ~/.local/bin à ton PATH si besoin :"
	@echo '  echo '"'"'export PATH="$$HOME/.local/bin:$$PATH"'"'"' >> ~/.bashrc && source ~/.bashrc'

docker-trust-ca: ## Faire confiance au CA Autoflow pour le registry Docker (git.$(DOMAIN))
	@bash scripts/docker-trust-ca.sh

# ── Utilities ────────────────────────────────────────────────

shell:          ## Open a shell in a running container  (e.g. make shell SERVICES=api)
	$(COMPOSE) exec $(or $(SERVICES),api) /bin/sh

ps:             ## Alias for status
	$(COMPOSE) ps

# ── Deploy Wizard ────────────────────────────────────────────

WIZARD_VENV := .wizard-venv

wizard:         ## Launch the deployment wizard on http://localhost:9000  (WIZARD_TOKEN required)
	@[ -d $(WIZARD_VENV) ] || python3 -m venv $(WIZARD_VENV)
	@$(WIZARD_VENV)/bin/pip install -q -r services/deploy-wizard/requirements.txt
	@[ -n "$(WIZARD_TOKEN)" ] || { \
		echo ""; \
		echo "  ERROR: WIZARD_TOKEN is not set."; \
		echo "  Generate and export a token before running the wizard:"; \
		echo ""; \
		echo "    export WIZARD_TOKEN=\$$(python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")"; \
		echo ""; \
		exit 1; \
	}
	@echo ""
	@echo "  ╔══════════════════════════════════════════╗"
	@echo "  ║   Autoflow Deploy Wizard                 ║"
	@echo "  ║   URL      : http://localhost:9000       ║"
	@echo "  ║   Username : wizard                      ║"
	@echo "  ║   Password : $$WIZARD_TOKEN              ║"
	@echo "  ║   Audit    : wizard-audit.log            ║"
	@echo "  ║   Press Ctrl+C to stop                   ║"
	@echo "  ╚══════════════════════════════════════════╝"
	@echo ""
	@AUTOFLOW_ROOT=$(PWD) WIZARD_TOKEN=$(WIZARD_TOKEN) $(WIZARD_VENV)/bin/uvicorn main:app \
		--host 127.0.0.1 --port 9000 \
		--app-dir services/deploy-wizard \
		--log-level warning

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

images-update:   ## Show pinned public images with available upstream versions
	@echo "Checking upstream versions for pinned public images…"
	@echo ""
	@for img in \
	    "traefik:v2.11" \
	    "postgres:15.17-alpine" \
	    "redis:7.4.8-alpine" \
	    "prom/prometheus:v3.11.1" \
	    "grafana/grafana:12.4.2" \
	    "prom/alertmanager:v0.32.0" \
	    "oliver006/redis_exporter:v1.82.0" \
	    "prometheuscommunity/postgres-exporter:v0.19.1" \
	    "gitea/gitea:1.23-rootless" \
	    "quay.io/ansible/receptor:v1.6.4"; do \
	    echo "  $$img"; \
	done
	@echo ""
	@echo "Update versions in docker-compose.yml, then: make pull && make restart"

# ── AWX custom image ─────────────────────────────────────────

awx-build:       ## Build the patched AWX image  (e.g. make awx-build AWX_VERSION=24.7.0)
	@echo "Building $(AWX_IMAGE):$(AWX_VERSION) from awx/Dockerfile.patched…"
	docker build \
		-f awx/Dockerfile.patched \
		--build-arg AWX_VERSION=$(AWX_VERSION) \
		-t $(AWX_IMAGE):$(AWX_VERSION) \
		awx/
	@echo ""
	@echo "  Built: $(AWX_IMAGE):$(AWX_VERSION)"
	@echo "  To push to Gitea: make awx-push"

awx-tag:         ## Tag AWX image for the Gitea registry  (GITEA_REGISTRY=git.domain/admin)
	docker tag $(AWX_IMAGE):$(AWX_VERSION) $(GITEA_REGISTRY)/awx-patched:$(AWX_VERSION)
	docker tag $(AWX_IMAGE):$(AWX_VERSION) $(GITEA_REGISTRY)/awx-patched:latest
	@echo "  Tagged: $(GITEA_REGISTRY)/awx-patched:$(AWX_VERSION)"

awx-push:        ## Build, tag and push AWX image to Gitea registry
	$(MAKE) awx-build
	$(MAKE) awx-tag
	docker push $(GITEA_REGISTRY)/awx-patched:$(AWX_VERSION)
	docker push $(GITEA_REGISTRY)/awx-patched:latest
	@echo "  Pushed to $(GITEA_REGISTRY)"

awx-pull:        ## Pull AWX image from Gitea registry (faster than rebuilding)
	docker pull $(GITEA_REGISTRY)/awx-patched:$(AWX_VERSION)
	docker tag  $(GITEA_REGISTRY)/awx-patched:$(AWX_VERSION) $(AWX_IMAGE):$(AWX_VERSION)
	@echo "  Pulled and tagged as $(AWX_IMAGE):$(AWX_VERSION)"

# ── Tests ────────────────────────────────────────────────────

# Virtual-env for the test suite (created on first use)
TEST_VENV := .test-venv
_PYTEST   := $(TEST_VENV)/bin/pytest

$(TEST_VENV):
	python3 -m venv $(TEST_VENV)
	$(TEST_VENV)/bin/pip install --quiet -r tests/requirements.txt

test-deps: $(TEST_VENV)  ## Install Python test dependencies into .test-venv

test-unit: $(TEST_VENV)  ## Run unit tests (parsers, rules, dedup)
	$(TEST_VENV)/bin/pip install --quiet \
		-r services/event-engine/requirements.txt 2>/dev/null || true
	cd $(shell pwd) && $(_PYTEST) -m "not integration and not e2e" \
		tests/unit/ -v --tb=short

test-integration: $(TEST_VENV)  ## Run integration tests (in-process stubs, no Docker)
	$(TEST_VENV)/bin/pip install --quiet \
		-r services/event-engine/requirements.txt \
		-r services/api/requirements.txt 2>/dev/null || true
	cd $(shell pwd) && $(_PYTEST) -m "integration" \
		tests/integration/ -v --tb=short

test: test-unit test-integration  ## Run unit + integration tests (default CI target)

test-stack-up:  ## Start the test stack (AWX stub + callback stub + services)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.test.yml \
		up -d event_engine api awx_stub callback_stub
	@echo "  Waiting for stubs to be healthy..."
	@$(COMPOSE) -f docker-compose.yml -f docker-compose.test.yml \
		exec awx_stub python -c "import time; time.sleep(2)" 2>/dev/null || true

test-stack-down:  ## Stop and remove the test stack
	$(COMPOSE) -f docker-compose.yml -f docker-compose.test.yml \
		down awx_stub callback_stub event_engine api

test-e2e: $(TEST_VENV)  ## Run E2E tests against a live test stack (requires test-stack-up)
	EE_URL=http://localhost:8001 \
	AWX_STUB_URL=http://localhost:8052 \
	CALLBACK_URL=http://localhost:9999 \
	CALLBACK_RECEIVE_URL=http://callback_stub:9999/callback \
	API_URL=http://localhost:8000 \
	API_SECRET_KEY=test-api-secret-32chars-long-xxxxxxxx \
		$(_PYTEST) -m "e2e" tests/e2e/ -v --tb=short

secrets-check:   ## Verify the Age key is present and .env.enc is decryptable
	@echo "  Checking Age key at $(SOPS_AGE_KEY_FILE)..."
	@test -f "$(SOPS_AGE_KEY_FILE)" || (echo "  ERROR: key not found at $(SOPS_AGE_KEY_FILE)" && exit 1)
	@echo "  Verifying .env.enc decryption..."
	@sops --decrypt --input-type dotenv --output-type dotenv .env.enc > /dev/null
	@echo "  OK — secrets are accessible."

# ── CLI autoflow ─────────────────────────────────────────────────────────────

CLI_VENV = .cli-venv

$(CLI_VENV):
	@python3 -m venv $(CLI_VENV)
	@$(CLI_VENV)/bin/pip install -q --upgrade pip
	@$(CLI_VENV)/bin/pip install -q -r scripts/requirements-cli.txt

cli-install: $(CLI_VENV)  ## Installe le CLI autoflow dans .cli-venv
	@chmod +x scripts/autoflow
	@echo ""
	@echo "  CLI installé. Utilisez :"
	@echo "    ./scripts/autoflow --help"
	@echo ""
	@echo "  Pour une utilisation globale :"
	@echo "    sudo ln -sf \$$PWD/scripts/autoflow /usr/local/bin/autoflow"
	@echo "    # puis : autoflow --help"

cli-check: $(CLI_VENV)  ## Vérifie la syntaxe du CLI et affiche l'aide
	@$(CLI_VENV)/bin/python scripts/autoflow --help
