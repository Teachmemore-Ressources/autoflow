"""
Autoflow API
============
Thin FastAPI service that proxies and augments AWX operations.

Features
--------
- JWT + X-API-Key dual authentication  (POST /auth/token for JWT)
- Rate limiting via slowapi             (per client IP, configurable via RATE_LIMIT)
- CORS restricted to CORS_ORIGINS env  (default: "*")
- Prometheus metrics on /metrics        (HTTP stats + AWX job gauges)
- Structured JSON audit log             (every non-health request)
- Job completion notifications          (background watcher + webhook/Slack)
- Enriched job history & stats         (/awx/jobs/history, /awx/jobs/stats)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

import notifications
from awx_metrics import collect_loop as awx_metrics_loop
from limiter import limiter
from routers import awx, health
from routers.auth import router as auth_router
from routers.compliance import _generate_and_cache
from routers.compliance import router as compliance_router
from routers.jobs_history import router as jobs_history_router
from settings import settings


# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
_audit_log = logging.getLogger("audit")


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
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
            asyncio.create_task(
                awx_metrics_loop(app.state.http, settings.awx_metrics_interval)
            )
        )

    # Job completion watcher (notifications)
    if settings.job_watcher_interval > 0:
        background_tasks.append(
            asyncio.create_task(
                notifications.collect_loop(app.state.http, settings.job_watcher_interval)
            )
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
                next_run.isoformat(), wait_seconds / 3600,
            )
            await asyncio.sleep(wait_seconds)
            try:
                await _generate_and_cache(http)
            except Exception as exc:
                logging.getLogger("compliance").error(
                    "Erreur lors de la génération du rapport hebdomadaire : %s", exc
                )

    background_tasks.append(
        asyncio.create_task(_weekly_compliance_loop(app.state.http))
    )

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
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Prometheus HTTP instrumentation ──────────────────────────────────────────
Instrumentator().instrument(app).expose(app)

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(auth_router)                           # /auth/token, /auth/me, ...
# jobs_history MUST be registered before awx so /awx/jobs/history and /awx/jobs/stats
# are matched before the wildcard route /awx/jobs/{job_id}
app.include_router(jobs_history_router)                   # /awx/jobs/history, /stats, /watch
app.include_router(awx.router, prefix="/awx", tags=["AWX"])
app.include_router(compliance_router)                     # /compliance/*


# ── Audit log middleware ──────────────────────────────────────────────────────

_AUDIT_SKIP = frozenset({
    "/metrics", "/health", "/health/ready", "/health/awx",
})

# Paramètres de query string à masquer pour éviter la fuite de secrets dans les logs
_SENSITIVE_PARAMS = frozenset({
    "token", "access_token", "api_key", "apikey", "key",
    "password", "passwd", "secret", "authorization",
})


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
                    "ts":          datetime.now(timezone.utc).isoformat(),
                    "method":      request.method,
                    "path":        request.url.path,
                    "query":       _sanitize_query(str(request.url.query)),
                    "status":      response.status_code,
                    "duration_ms": duration_ms,
                    "client_ip":   request.client.host if request.client else None,
                    "user_agent":  request.headers.get("user-agent"),
                },
                default=str,
            )
        )

    return response
