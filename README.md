# Autoflow

An automation platform built on [AWX](https://github.com/ansible/awx) (Ansible Tower open-source) with a branded React UI, a FastAPI service layer, and an event-driven job engine — all wired together with plain docker-compose.

## Architecture

```
  Browser
     │
     ▼  :8085
┌──────────────────────────────────────┐
│  Autoflow UI  (React + nginx)        │  ← user-facing, no AWX branding
│                                      │
│  /api/v2/*  ──proxy──►  awx_web:80  │  ← nginx proxies AWX REST API
└──────────────────────────────────────┘
                   │
     ┌─────────────┴─────────────┐
     ▼  :80                      ▼  :8000 / :8001
 AWX Web                   Autoflow API / Event Engine
 (Django + nginx)          (FastAPI — internal use)
     │
     ├── AWX Task (Celery)
     ├── Receptor  (job executor)
     │
  PostgreSQL + Redis
     │
  Prometheus + Grafana  :9090 / :3000
```

| Service          | Container               | Host port (default)  | Purpose                          |
|------------------|-------------------------|----------------------|----------------------------------|
| **Autoflow UI**  | `autoflow_ui`           | **8085**             | User-facing branded React SPA    |
| AWX Web          | `autoflow_awx_web`      | 80                   | AWX backend (admin / debug only) |
| Autoflow API     | `autoflow_api`          | 8000                 | FastAPI proxy over AWX REST API  |
| Event Engine     | `autoflow_event_engine` | 8001                 | Event-driven job trigger         |
| Prometheus       | `autoflow_prometheus`   | 9090                 | Metrics                          |
| Grafana          | `autoflow_grafana`      | 3000                 | Dashboards                       |
| PostgreSQL       | `autoflow_postgres`     | —                    | AWX database                     |
| Redis            | `autoflow_redis`        | —                    | AWX cache / queue                |
| AWX Task         | `autoflow_awx_task`     | —                    | Celery workers                   |
| Receptor         | `autoflow_receptor`     | —                    | Job execution mesh               |

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url> autoflow && cd autoflow

# 2. Bootstrap the environment file
make setup

# 3. Edit .env — fill in ALL required secrets
$EDITOR .env

# 4. Start the full stack
docker compose up -d

# 5. Wait for AWX to initialise (first boot: 2–5 minutes)
make logs SERVICES=awx_web

# 6. Open Autoflow UI
open http://localhost:8085
#    Sign in with the same AWX_ADMIN_USER / AWX_ADMIN_PASSWORD
```

> AWX raw UI is still reachable at `http://localhost:80` for admin/debug.
> Users should only ever interact with `http://localhost:8085`.

## Prerequisites

- Docker 24+ and Docker Compose v2 (`docker compose`)
- 4 GB RAM minimum (8 GB recommended — AWX is memory-hungry)
- Linux host (tested on Ubuntu 22.04 / 24.04)

## Generating secrets

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Environment variables

| Variable               | Required | Description                                        |
|------------------------|----------|----------------------------------------------------|
| `POSTGRES_PASSWORD`    | Yes      | PostgreSQL password                                |
| `REDIS_PASSWORD`       | Yes      | Redis password                                     |
| `AWX_ADMIN_PASSWORD`   | Yes      | AWX admin password (also used to log into the UI)  |
| `AWX_SECRET_KEY`       | Yes      | AWX Django secret key (≥ 50 random chars)          |
| `API_SECRET_KEY`       | Yes      | Autoflow API secret key                            |
| `AWX_TOKEN`            | Yes      | AWX bearer token for the Event Engine              |
| `AWX_JOB_TEMPLATE_ID`  | Yes      | Job template ID triggered by incoming events       |
| `GRAFANA_ADMIN_PASSWORD` | Yes    | Grafana admin password                             |
| `AUTOFLOW_UI_PORT`     | No       | UI host port (default: `8085`)                     |
| `AWX_HTTP_PORT`        | No       | AWX host port (default: `80`)                      |
| `PROMETHEUS_RETENTION` | No       | Metrics retention (default: `15d`)                 |

## Make commands

| Command                         | Description                                  |
|---------------------------------|----------------------------------------------|
| `make start`                    | Start all services (`docker compose up -d`)  |
| `make stop`                     | Stop all services                            |
| `make restart`                  | Restart all services                         |
| `make restart SERVICES=autoflow_ui` | Rebuild & restart just the UI            |
| `make logs`                     | Tail logs for all services                   |
| `make logs SERVICES=autoflow_ui`| Tail UI logs                                 |
| `make build`                    | Rebuild all images without cache             |
| `make status`                   | Show container status and health             |
| `make shell SERVICES=api`       | Open a shell in a container                  |
| `make setup`                    | Copy `.env.example` → `.env`                 |
| `make monitoring-up`            | Start only the monitoring stack              |
| `make monitoring-down`          | Stop the monitoring stack                    |

## Autoflow UI

The UI is a React single-page application served by an nginx container on port **8085**.

### How it works

- nginx serves the static React build from `/usr/share/nginx/html`
- All requests to `/api/v2/*` and `/api/o/*` are **proxied server-side** to `awx_web:80` — the browser never talks directly to AWX, eliminating all CORS complexity
- Authentication uses AWX bearer tokens: the login form calls `POST /api/v2/tokens/` with Basic Auth, stores the resulting token in `localStorage`, and attaches it as `Authorization: Bearer <token>` on every subsequent request
- On logout the token is revoked via `DELETE /api/v2/tokens/{id}/`

### Features

| Screen      | Description                                                        |
|-------------|--------------------------------------------------------------------|
| Login       | AWX credential form — no AWX branding, Autoflow identity only      |
| Dashboard   | Stats bar · job template cards with Launch button · recent jobs    |
| Jobs        | Paginated job list with status filter and auto-refresh             |
| Job Detail  | Metadata, live-streaming stdout output, auto-polling while running |

### Rebuilding after a code change

```bash
docker compose build autoflow_ui
docker compose up -d autoflow_ui
```

## Autoflow API endpoints

Base URL: `http://localhost:8000/`

| Method | Path                             | Description            |
|--------|----------------------------------|------------------------|
| GET    | `/health`                        | API liveness           |
| GET    | `/health/awx`                    | AWX connectivity check |
| GET    | `/docs`                          | Swagger UI             |
| GET    | `/awx/job-templates`             | List job templates     |
| POST   | `/awx/job-templates/{id}/launch` | Launch a job           |
| GET    | `/awx/jobs`                      | List jobs              |
| GET    | `/awx/jobs/{id}`                 | Get job detail         |

## Event Engine endpoints

Base URL: `http://localhost:8001/`

| Method | Path      | Description                                    |
|--------|-----------|------------------------------------------------|
| GET    | `/health` | Service liveness                               |
| POST   | `/event`  | Trigger an AWX job with a JSON event payload   |

## Persistent volumes

| Volume            | Purpose                         |
|-------------------|---------------------------------|
| `postgres_data`   | PostgreSQL data directory       |
| `redis_data`      | Redis AOF persistence           |
| `awx_projects`    | AWX playbook projects           |
| `awx_receptor`    | AWX receptor working directory  |
| `prometheus_data` | Prometheus time-series database |
| `grafana_data`    | Grafana configuration           |

## Upgrading AWX

```bash
# Edit AWX_VERSION in .env, then:
make pull && make restart SERVICES="awx_web awx_task"
```

## Project structure

```
autoflow/
├── docker-compose.yml
├── .env.example
├── Makefile
├── README.md
├── monitoring/
│   ├── prometheus/prometheus.yml
│   └── grafana/provisioning/
├── services/
│   ├── autoflow-ui/              # Branded React SPA
│   │   ├── Dockerfile            # Node build → nginx:alpine
│   │   ├── nginx.conf            # Serves SPA + proxies /api/* to AWX
│   │   ├── package.json
│   │   ├── vite.config.js
│   │   ├── index.html
│   │   └── src/
│   │       ├── main.jsx
│   │       ├── App.jsx           # Router + auth context
│   │       ├── index.css         # Design system
│   │       ├── api/
│   │       │   └── awx.js        # AWX REST client + helpers
│   │       ├── components/
│   │       │   ├── Layout.jsx    # Sidebar shell
│   │       │   ├── StatusBadge.jsx
│   │       │   └── LaunchModal.jsx
│   │       └── pages/
│   │           ├── Login.jsx
│   │           ├── Dashboard.jsx
│   │           ├── Jobs.jsx
│   │           └── JobDetail.jsx
│   ├── api/                      # Autoflow FastAPI proxy
│   └── event-engine/             # Event-driven job trigger
└── awx/                          # AWX settings + init scripts
```

## Notes

- **AWX is the backend only.** End users log in at `:8085`. The AWX UI at `:80` is for admins only and can be firewalled off.
- AWX first-boot migrations take 2–5 minutes. `autoflow_ui` waits for `awx_web` to be healthy before starting.
- All services use `restart: unless-stopped` and survive host reboots.
- No TLS, no reverse proxy required — plain docker-compose on any Linux host.
