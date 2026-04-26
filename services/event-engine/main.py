"""
Autoflow Event Engine v2
========================
Receives generic events and vendor webhooks (GitHub, Alertmanager),
routes them to the correct AWX job template via a YAML rule engine,
deduplicates repeated events, and fires events on cron schedules.

Endpoints
---------
GET  /health                       Liveness probe
POST /event                        Generic canonical event
POST /webhook/github               GitHub webhook (validates X-Hub-Signature-256)
POST /webhook/alertmanager         Alertmanager webhook
GET  /admin/rules                  List loaded rules
POST /admin/rules/reload           Hot-reload rules from disk
GET  /admin/dedup/stats            Dedup cache info
POST /admin/dedup/clear            Flush dedup cache
GET  /admin/schedules              List cron schedules
POST /admin/schedules/reload       Hot-reload schedules from disk
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from awx_client import AWXClient, AWXError
from dedup import DedupStore
from parsers import parse_alertmanager, parse_github
from rules import RuleEngine
from scheduler import EventScheduler
from settings import settings
from tracing import instrument_app, setup_tracing

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger("event_engine")

setup_tracing("autoflow-event-engine")

# ── Rate limiter ──────────────────────────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit],
)


# ── Admin auth — Bearer token requis sur tous les endpoints /admin/* ──────────

_bearer_scheme = HTTPBearer(auto_error=False)

def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> None:
    """Vérifie le token Bearer pour les endpoints admin.
    Si ADMIN_TOKEN est vide, les endpoints sont bloqués (fail-secure).
    """
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin endpoints disabled — set ADMIN_TOKEN")
    if credentials is None or credentials.credentials != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Core dispatch (shared by HTTP + scheduler) ────────────────────────────────

async def _dispatch_core(
    dedup: DedupStore,
    rules: RuleEngine,
    awx:   AWXClient,
    source: str,
    action: str,
    data:   dict[str, Any],
) -> dict:
    """
    Resolve rule → deduplicate → launch AWX job.
    Returns a dict describing the outcome (used by both HTTP and scheduler paths).
    """
    if settings.dedup_ttl > 0 and dedup.is_duplicate(source, action, data):
        logger.info("Duplicate suppressed (source=%s action=%s)", source, action)
        return {"status": "deduplicated", "source": source, "action": action}

    template_id, extra_vars = rules.resolve(source, action, data)
    job = await awx.launch_job(extra_vars=extra_vars, template_id=template_id)

    return {
        "status":      "accepted",
        "job_id":      job.get("id"),
        "job_url":     job.get("url"),
        "template_id": template_id,
        "source":      source,
        "action":      action,
    }


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    http = httpx.AsyncClient(
        base_url=settings.awx_url,
        headers={"Authorization": f"Bearer {settings.awx_token}"},
        timeout=30.0,
    )
    app.state.awx   = AWXClient(http, settings.awx_job_template_id)
    app.state.rules = RuleEngine(settings.rules_file, settings.awx_job_template_id)
    app.state.dedup = DedupStore(ttl_seconds=settings.dedup_ttl)

    # Scheduler — fires events on cron without external webhooks
    async def _scheduled_dispatch(source: str, action: str, data: dict) -> None:
        try:
            result = await _dispatch_core(
                app.state.dedup, app.state.rules, app.state.awx,
                source, action, data,
            )
            logger.info(
                "Scheduled dispatch: source=%s action=%s status=%s job_id=%s",
                source, action, result.get("status"), result.get("job_id"),
            )
        except AWXError as exc:
            logger.error("Scheduled dispatch AWX error: %s", exc)
        except Exception as exc:
            logger.exception("Scheduled dispatch unexpected error: %s", exc)

    scheduler = EventScheduler(settings.schedules_file, _scheduled_dispatch)
    scheduler.start()
    app.state.scheduler = scheduler

    logger.info(
        "Event engine v2 started — default template=%d  dedup_ttl=%ds  schedules=%d",
        settings.awx_job_template_id,
        settings.dedup_ttl,
        scheduler.loaded_count,
    )
    yield

    scheduler.stop()
    await http.aclose()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Autoflow Event Engine",
    version="2.0.0",
    description="Routes external events and scheduled triggers to AWX job templates.",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Rate limiting ─────────────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── Prometheus HTTP instrumentation ──────────────────────────────────────────
Instrumentator().instrument(app).expose(app)

# ── OpenTelemetry FastAPI instrumentation ─────────────────────────────────────
instrument_app(app, "autoflow-event-engine")


# ── Schemas ───────────────────────────────────────────────────────────────────

class EventPayload(BaseModel):
    action: str
    source: str = "external"
    data:   dict[str, Any] = {}

    @field_validator("action")
    @classmethod
    def action_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("action must not be empty")
        return v


# ── HTTP dispatch wrapper ─────────────────────────────────────────────────────

async def _dispatch(
    request: Request,
    source: str,
    action: str,
    data: dict[str, Any],
) -> JSONResponse:
    """HTTP-facing dispatch — wraps _dispatch_core with proper HTTP responses."""
    try:
        result = await _dispatch_core(
            request.app.state.dedup,
            request.app.state.rules,
            request.app.state.awx,
            source, action, data,
        )
    except AWXError as exc:
        logger.error("AWX error: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error launching job")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))

    http_status = status.HTTP_200_OK if result["status"] == "deduplicated" else status.HTTP_202_ACCEPTED
    return JSONResponse(status_code=http_status, content=result)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "service": "event-engine", "version": "2.0.0"}


# ── Generic event ─────────────────────────────────────────────────────────────

@app.post("/event", tags=["events"], status_code=202)
@limiter.limit(settings.rate_limit_webhooks)
async def receive_event(event: EventPayload, request: Request):
    """
    Accept a generic event in canonical format.

    Example::

        POST /event
        {"action": "deploy", "source": "ci", "data": {"env": "prod", "app": "api"}}
    """
    return await _dispatch(request, event.source, event.action, event.data)


# ── GitHub webhook ────────────────────────────────────────────────────────────

@app.post("/webhook/github", tags=["webhooks"])
@limiter.limit(settings.rate_limit_webhooks)
async def webhook_github(
    request: Request,
    x_github_event: str = Header(default="unknown"),
    x_hub_signature_256: str | None = Header(default=None),
):
    """
    Accept a GitHub webhook payload with optional HMAC-SHA256 signature validation.

    Set GITHUB_WEBHOOK_SECRET in your environment to enable signature checking.
    Configure in GitHub: Settings → Webhooks → Content type: application/json.
    """
    raw_body = await request.body()

    if settings.github_webhook_secret:
        if x_hub_signature_256 is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-Hub-Signature-256 header",
            )
        expected = "sha256=" + hmac.new(
            settings.github_webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid webhook signature",
            )

    try:
        import json
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    source, action, data = parse_github(x_github_event, payload)
    logger.info(
        "GitHub webhook: event=%s action=%s repo=%s",
        x_github_event, action,
        data.get("repository", {}).get("full_name", "?"),
    )
    return await _dispatch(request, source, action, data)


# ── Alertmanager webhook ──────────────────────────────────────────────────────

@app.post("/webhook/alertmanager", tags=["webhooks"])
@limiter.limit(settings.rate_limit_webhooks)
async def webhook_alertmanager(request: Request):
    """
    Accept an Alertmanager webhook notification.

    Configure in alertmanager.yml::

        receivers:
          - name: autoflow
            webhook_configs:
              - url: http://event_engine:8001/webhook/alertmanager
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    source, action, data = parse_alertmanager(payload)
    logger.info(
        "Alertmanager webhook: status=%s alertname=%s severity=%s",
        data.get("status"), data.get("alertname"), data.get("severity"),
    )
    return await _dispatch(request, source, action, data)


# ── Admin — Rules ─────────────────────────────────────────────────────────────

@app.get("/admin/rules", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def list_rules(request: Request):
    """Return the currently loaded routing rules."""
    engine: RuleEngine = request.app.state.rules
    return {
        "count":               len(engine._rules),
        "default_template_id": engine._default_id,
        "rules": [
            {
                "name":                 r.name,
                "match":                r.match,
                "job_template_id":      r.job_template_id,
                "extra_vars":           r.extra_vars,
                "extra_vars_from_data": r.extra_vars_from_data,
            }
            for r in engine._rules
        ],
    }


@app.post("/admin/rules/reload", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def reload_rules(request: Request):
    """Hot-reload the rules file from disk without restarting the service."""
    engine: RuleEngine = request.app.state.rules
    count = engine.reload()
    return {"status": "reloaded", "rules_loaded": count}


# ── Admin — Dedup ─────────────────────────────────────────────────────────────

@app.get("/admin/dedup/stats", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def dedup_stats(request: Request):
    """Return dedup cache size and configuration."""
    dedup: DedupStore = request.app.state.dedup
    return {
        "enabled":        settings.dedup_ttl > 0,
        "ttl_seconds":    settings.dedup_ttl,
        "cached_entries": dedup.size(),
    }


@app.post("/admin/dedup/clear", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def dedup_clear(request: Request):
    """Flush the entire dedup cache."""
    request.app.state.dedup.clear()
    return {"status": "cleared"}


# ── Admin — Schedules ─────────────────────────────────────────────────────────

@app.get("/admin/schedules", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def list_schedules(request: Request):
    """Return the currently active cron schedules and their next fire times."""
    sched: EventScheduler = request.app.state.scheduler
    return {
        "count": sched.loaded_count,
        "jobs":  sched.jobs(),
    }


@app.post("/admin/schedules/reload", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def reload_schedules(request: Request):
    """Hot-reload the schedules file from disk without restarting the service."""
    sched: EventScheduler = request.app.state.scheduler
    count = sched.reload()
    return {"status": "reloaded", "jobs_loaded": count}
