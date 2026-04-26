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
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

import scanner
from compliance import REPORTS_DIR, run_report
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


# ── Bearer auth dependency ────────────────────────────────────────────────────

def _require_token(request: Request) -> None:
    expected = settings.compliance_admin_token
    if not expected:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing Bearer token")


# ── Compliance job state ──────────────────────────────────────────────────────

_compliance_running = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}


@app.post("/compliance/report/generate", tags=["Compliance"])
async def compliance_generate(_: None = Depends(_require_token)):
    """
    Trigger an on-demand compliance report.  Returns a streaming SSE response;
    each line is a progress message.  The report (JSON + Markdown) is saved to
    /tmp/compliance/latest.{json,md} inside the container.
    """
    global _compliance_running
    if _compliance_running:
        raise HTTPException(status_code=409, detail="Compliance scan already running")

    async def _stream():
        global _compliance_running
        _compliance_running = True
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _cb(msg: str) -> None:
            queue.put_nowait(msg)

        async def _run():
            try:
                await run_report(progress_cb=_cb)
            except Exception as exc:
                queue.put_nowait(f"ERROR: {exc}")
            finally:
                queue.put_nowait(None)  # sentinel

        task = asyncio.create_task(_run())
        try:
            while True:
                msg = await queue.get()
                if msg is None:
                    yield "data: __done__\n\n"
                    break
                yield f"data: {msg}\n\n"
        finally:
            _compliance_running = False
            task.cancel()

    return StreamingResponse(_stream(), media_type="text/event-stream")


@app.get("/compliance/report/latest", tags=["Compliance"])
async def compliance_latest_json(_: None = Depends(_require_token)):
    """Return the most-recently-generated compliance report as JSON."""
    path = REPORTS_DIR / "latest.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No compliance report found — run generate first")
    return JSONResponse(content=json.loads(path.read_text()))


@app.get("/compliance/report/latest/markdown", tags=["Compliance"])
async def compliance_latest_md(_: None = Depends(_require_token)):
    """Return the most-recently-generated compliance report as Markdown."""
    path = REPORTS_DIR / "latest.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="No compliance report found — run generate first")
    return PlainTextResponse(path.read_text())
