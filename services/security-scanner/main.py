"""
Security Scanner
================
FastAPI service that runs Trivy CVE scans and image version checks on all
stack images, exposing results as Prometheus metrics at /metrics.

Background tasks
----------------
- scanner.scan_loop  : Trivy CVE scan cycle (SCAN_INTERVAL seconds)
- version_check loop : GitHub/DockerHub version check (VERSION_CHECK_INTERVAL seconds)
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

import scanner
from version_check import VersionCheckLoop
from settings import settings
from tracing import instrument_app, setup_tracing


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)

setup_tracing("autoflow-security-scanner")

# ── Rate limiter ──────────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit],
)

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    version_loop = VersionCheckLoop()

    tasks = [
        asyncio.create_task(scanner.scan_loop(settings.scan_interval)),
        asyncio.create_task(version_loop.run(settings.version_check_interval)),
    ]

    yield

    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Autoflow Security Scanner",
    description="Trivy CVE scanning and image version checking for the Autoflow stack",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Prometheus HTTP instrumentation ───────────────────────────────────────────
Instrumentator().instrument(app).expose(app)

# ── OpenTelemetry FastAPI instrumentation ─────────────────────────────────────
instrument_app(app, "autoflow-security-scanner")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
