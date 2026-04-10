# Autoflow

An automation platform built on [AWX](https://github.com/ansible/awx) — the open-source version of Ansible Tower — with a FastAPI service layer and Nginx reverse proxy.

## Architecture

```
                        ┌─────────────────────────────┐
                        │         Nginx :80            │
                        │      Reverse Proxy           │
                        └────────┬──────────┬──────────┘
                                 │          │
                    /api/autoflow/          / (everything else)
                                 │          │
                    ┌────────────▼─┐   ┌────▼────────────┐
                    │  Autoflow API│   │   AWX Web :8052  │
                    │  FastAPI     │   │   (Django/DRF)   │
                    └──────────────┘   └──────┬───────────┘
                                              │
                          ┌───────────────────┤
                          │                   │
                   ┌──────▼──────┐   ┌────────▼────────┐
                   │  PostgreSQL  │   │  Redis :6379    │
                   │  :5432      │   │  (cache/queue)  │
                   └─────────────┘   └─────────────────┘
```

| Service          | Container             | Internal Port |
|------------------|-----------------------|---------------|
| Nginx            | `autoflow_nginx`      | 80, 443       |
| AWX Web          | `autoflow_awx_web`    | 8052          |
| AWX Task worker  | `autoflow_awx_task`   | —             |
| Autoflow API     | `autoflow_api`        | 8000          |
| PostgreSQL       | `autoflow_postgres`   | 5432          |
| Redis            | `autoflow_redis`      | 6379          |

## Prerequisites

- Docker 24+ and Docker Compose v2 (`docker compose`)
- 4 GB RAM minimum (AWX is memory-hungry; 8 GB recommended)
- Linux host (tested on Ubuntu 22.04 / 24.04)

## Quick Start

```bash
# 1. Clone and enter the project
git clone <repo-url> autoflow && cd autoflow

# 2. Bootstrap the environment file
make setup

# 3. Edit .env — fill in ALL required secrets
#    (POSTGRES_PASSWORD, REDIS_PASSWORD, AWX_ADMIN_PASSWORD, AWX_SECRET_KEY, API_SECRET_KEY)
$EDITOR .env

# 4. Start the stack
docker compose up -d

# 5. Wait for AWX to initialise (first boot takes 2–5 minutes)
make logs SERVICES=awx_web

# 6. Open the UI
#    AWX:          http://localhost/
#    API docs:     http://localhost/api/autoflow/docs
#    AWX REST API: http://localhost/api/v2/
```

## Generating secrets

```bash
# AWX_SECRET_KEY and API_SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Make commands

| Command                      | Description                              |
|------------------------------|------------------------------------------|
| `make start`                 | Start all services (`docker compose up -d`) |
| `make stop`                  | Stop all services                        |
| `make restart`               | Restart all services                     |
| `make restart SERVICES=api`  | Restart a single service                 |
| `make logs`                  | Tail logs for all services               |
| `make logs SERVICES=awx_web` | Tail logs for a specific service         |
| `make status`                | Show container status and health         |
| `make build`                 | Rebuild images without cache             |
| `make pull`                  | Pull latest upstream images              |
| `make shell SERVICES=api`    | Open a shell in a container              |
| `make setup`                 | Copy `.env.example` → `.env`             |

## Autoflow API endpoints

Base path: `http://localhost/api/autoflow/`

| Method | Path                                    | Description              |
|--------|-----------------------------------------|--------------------------|
| GET    | `/health`                               | API liveness             |
| GET    | `/health/awx`                           | AWX connectivity         |
| GET    | `/docs`                                 | Swagger UI               |
| GET    | `/awx/job-templates`                    | List job templates       |
| GET    | `/awx/job-templates/{id}`               | Get a job template       |
| POST   | `/awx/job-templates/{id}/launch`        | Launch a job             |
| GET    | `/awx/jobs`                             | List jobs                |
| GET    | `/awx/jobs/{id}`                        | Get job status           |
| GET    | `/awx/inventories`                      | List inventories         |
| GET    | `/awx/projects`                         | List projects            |

## Persistent volumes

| Volume           | Purpose                         |
|------------------|---------------------------------|
| `postgres_data`  | PostgreSQL data directory       |
| `redis_data`     | Redis AOF persistence           |
| `awx_projects`   | AWX playbook projects           |
| `awx_receptor`   | AWX receptor working directory  |

## Upgrading AWX

Edit `AWX_VERSION` in `.env`, then:

```bash
make pull && make restart SERVICES="awx_web awx_task"
```

## Project structure

```
autoflow/
├── docker-compose.yml       # Main Compose file
├── .env.example             # Template — copy to .env
├── .gitignore
├── Makefile
├── README.md
├── nginx/
│   └── nginx.conf           # Reverse proxy config
└── services/
    └── api/                 # Autoflow FastAPI service
        ├── Dockerfile
        ├── main.py
        ├── settings.py
        ├── requirements.txt
        └── routers/
            ├── health.py    # Liveness + AWX connectivity
            └── awx.py       # AWX proxy endpoints
```

## Notes

- AWX first-boot migrations can take several minutes — the `awx_task` container waits for `awx_web` to be healthy before starting.
- All services use `restart: unless-stopped` — they will come back after host reboots.
- Secrets are passed exclusively via environment variables; nothing is hard-coded.
- The Autoflow API does **not** modify AWX — it proxies the AWX REST API and adds a thin application layer on top.
