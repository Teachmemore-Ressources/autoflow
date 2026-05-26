"""
Autoflow API
============
Thin FastAPI service that proxies and augments AWX operations.

Features
--------
- JWT + X-API-Key dual authentication  (POST /api/v1/auth/token for JWT)
- Rate limiting via slowapi             (per client IP, configurable via RATE_LIMIT)
- CORS restricted to CORS_ORIGINS env  (default: "" — empty, must be set explicitly)
- Prometheus metrics on /metrics        (HTTP stats + AWX job gauges)
- Structured JSON audit log             (every non-health request)
- Job completion notifications          (background watcher + webhook/Slack)
- Enriched job history & stats         (/api/v1/awx/jobs/history, /api/v1/awx/jobs/stats)

Routing
-------
All business endpoints are versioned under /api/v1/:
  /api/v1/auth/*         — authentication (token, refresh, me, logout)
  /api/v1/awx/*          — AWX proxy (job templates, jobs, inventories, projects)
  /api/v1/compliance/*   — compliance reporting (CSV, score, HTML report)
  /api/v1/users/*        — user management (admin only)

Unversioned (no prefix change for infrastructure compatibility):
  /health  /health/ready  /health/awx  — liveness & readiness probes
  /metrics                              — Prometheus scrape endpoint
  /docs  /redoc  /openapi.json          — API documentation
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import notifications
import user_store
from awx_metrics import collect_loop as awx_metrics_loop
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from limiter import limiter
from prometheus_fastapi_instrumentator import Instrumentator
from routers import awx, health
from routers.auth import router as auth_router
from routers.compliance import _generate_and_cache
from routers.compliance import router as compliance_router
from routers.jobs_history import router as jobs_history_router
from routers.users import router as users_router
from security_headers import SecurityHeadersMiddleware, parse_cors_origins
from settings import settings
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from tracing import instrument_app, setup_tracing

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)

setup_tracing("autoflow-api")
_audit_log = logging.getLogger("audit")


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialise user store (bootstrap default admin from API_SECRET_KEY if empty)
    user_store._ensure_loaded(admin_fallback_password=settings.api_secret_key)

    # Shared async HTTP client — reused across all requests
    app.state.http = httpx.AsyncClient(
        base_url=settings.awx_url,
        auth=(settings.awx_admin_user, settings.awx_admin_password),
        timeout=30.0,
        # verify=True (défaut) — AWX_URL est http:// en interne donc TLS non applicable.
        # Si AWX_URL passe en https://, fournir le chemin CA via httpx verify='/path/ca.crt'.
    )

    background_tasks: list[asyncio.Task] = []

    # AWX job metrics background collector
    if settings.awx_metrics_interval > 0:
        background_tasks.append(
            asyncio.create_task(awx_metrics_loop(app.state.http, settings.awx_metrics_interval))
        )

    # Job completion watcher (notifications)
    if settings.job_watcher_interval > 0:
        background_tasks.append(
            asyncio.create_task(notifications.collect_loop(app.state.http, settings.job_watcher_interval))
        )

    # Rapport de conformité hebdomadaire — génération automatique chaque lundi à 06h00 UTC
    async def _weekly_compliance_loop(http) -> None:
        """Génère le rapport HTML de conformité chaque semaine."""
        while True:
            now = datetime.now(timezone.utc)
            # Prochain lundi à 06:00 UTC
            days_until_monday = (7 - now.weekday()) % 7 or 7
            next_run = (now + timedelta(days=days_until_monday)).replace(
                hour=6, minute=0, second=0, microsecond=0
            )
            wait_seconds = (next_run - now).total_seconds()
            logging.getLogger("compliance").info(
                "Prochain rapport de conformité : %s (dans %.0fh)",
                next_run.isoformat(),
                wait_seconds / 3600,
            )
            await asyncio.sleep(wait_seconds)
            try:
                await _generate_and_cache(http)
            except Exception as exc:
                logging.getLogger("compliance").error(
                    "Erreur lors de la génération du rapport hebdomadaire : %s", exc
                )

    background_tasks.append(asyncio.create_task(_weekly_compliance_loop(app.state.http)))

    yield

    # Graceful shutdown
    for task in background_tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await app.state.http.aclose()


# ── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Autoflow API",
    description="Automation platform API powered by AWX",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS ─────────────────────────────────────────────────────────────────────
_cors_origins = parse_cors_origins(
    settings.cors_origins.strip(),
    settings.env,
    logging.getLogger("autoflow_api.cors"),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# ── Security headers ──────────────────────────────────────────────────────────
if settings.security_headers_enabled:
    app.add_middleware(SecurityHeadersMiddleware)

# ── Prometheus HTTP instrumentation ──────────────────────────────────────────
Instrumentator().instrument(app).expose(app)

# ── OpenTelemetry FastAPI instrumentation ─────────────────────────────────────
instrument_app(app, "autoflow-api")

# ── Routers ──────────────────────────────────────────────────────────────────
#
# Unversioned infrastructure routes (no /api/v1 prefix — used by Docker
# healthchecks, Prometheus, and Traefik probes; must never change).
app.include_router(health.router)  # /health  /health/ready  /health/awx
# /metrics is exposed by Instrumentator above (also unversioned)

# All business routes live under /api/v1/.
# jobs_history MUST be registered before awx so /api/v1/awx/jobs/history and
# /api/v1/awx/jobs/stats are matched before the wildcard /api/v1/awx/jobs/{job_id}.
_v1 = APIRouter(prefix="/api/v1")
_v1.include_router(auth_router)  # /api/v1/auth/*
_v1.include_router(jobs_history_router)  # /api/v1/awx/jobs/history, /stats, /watch
_v1.include_router(awx.router, prefix="/awx", tags=["AWX"])  # /api/v1/awx/*
_v1.include_router(compliance_router)  # /api/v1/compliance/*
_v1.include_router(users_router)  # /api/v1/users/*
app.include_router(_v1)


# ── Audit log middleware ──────────────────────────────────────────────────────

_AUDIT_SKIP = frozenset(
    {
        "/metrics",
        "/health",
        "/health/ready",
        "/health/awx",
    }
)

# Paramètres de query string à masquer pour éviter la fuite de secrets dans les logs
_SENSITIVE_PARAMS = frozenset(
    {
        "token",
        "access_token",
        "api_key",
        "apikey",
        "key",
        "password",
        "passwd",
        "secret",
        "authorization",
    }
)


def _sanitize_query(query: str) -> str | None:
    """Remplace la valeur des paramètres sensibles par [REDACTED] dans la query string."""
    if not query:
        return None
    parts = []
    for param in query.split("&"):
        if "=" in param:
            name, _, value = param.partition("=")
            if name.lower() in _SENSITIVE_PARAMS:
                parts.append(f"{name}=[REDACTED]")
            else:
                parts.append(param)
        else:
            parts.append(param)
    return "&".join(parts) or None


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)

    if request.url.path not in _AUDIT_SKIP:
        _audit_log.info(
            json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "method": request.method,
                    "path": request.url.path,
                    "query": _sanitize_query(str(request.url.query)),
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "client_ip": request.client.host if request.client else None,
                    "user_agent": request.headers.get("user-agent"),
                },
                default=str,
            )
        )

    return response
