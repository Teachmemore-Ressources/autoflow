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
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

import notifications
from awx_metrics import collect_loop as awx_metrics_loop
from routers import awx, health
from routers.auth import router as auth_router
from routers.jobs_history import router as jobs_history_router
from settings import settings


# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
_audit_log = logging.getLogger("audit")


# ── Rate limiter ─────────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit],
)


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Shared async HTTP client — reused across all requests
    app.state.http = httpx.AsyncClient(
        base_url=settings.awx_url,
        auth=(settings.awx_admin_user, settings.awx_admin_password),
        timeout=30.0,
        verify=False,  # internal network; adjust if TLS is enabled on AWX
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


# ── Audit log middleware ──────────────────────────────────────────────────────

_AUDIT_SKIP = frozenset({
    "/metrics", "/health", "/health/ready", "/health/awx",
})


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
                    "query":       str(request.url.query) or None,
                    "status":      response.status_code,
                    "duration_ms": duration_ms,
                    "client_ip":   request.client.host if request.client else None,
                    "user_agent":  request.headers.get("user-agent"),
                },
                default=str,
            )
        )

    return response
