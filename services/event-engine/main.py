"""
Autoflow Event Engine v2
========================
Receives generic events and vendor webhooks (GitHub, Alertmanager),
routes them to the correct AWX job template via a YAML rule engine,
deduplicates repeated events, and fires events on cron schedules.

Reliability model
-----------------
When REDIS_URL is configured:
  • Every incoming event is persisted to Redis before processing.
  • If AWX is unreachable the event is retried with exponential backoff
    (30 s → 2 min → 5 min → 10 min → 20 min), then moved to a
    dead-letter queue (DLQ) after 5 failed attempts.
  • Dedup state is stored in Redis and survives container restarts.
  • HTTP endpoints return 202 immediately; the background worker handles
    the actual AWX dispatch asynchronously.

When REDIS_URL is not set (fallback):
  • Legacy fire-and-forget behaviour: events are dispatched synchronously,
    AWX errors surface as HTTP 502/503, and dedup is in-memory only.

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
GET  /admin/queue/stats            Queue depth (pending / retry / DLQ)
GET  /admin/dlq                    List dead-letter queue events
POST /admin/dlq/{event_id}/requeue Re-queue a specific DLQ event
DELETE /admin/dlq                  Clear the entire DLQ
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from awx_client import AWXClient, AWXError
from dedup import DedupStore
from event_store import EventStore
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from parsers import parse_alertmanager, parse_github
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, field_validator
from retry_worker import RetryWorker
from rules import RuleEngine
from scheduler import EventScheduler
from security_headers import SecurityHeadersMiddleware, parse_cors_origins
from settings import settings
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
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

# ── Admin auth — Bearer token required on all /admin/* endpoints ──────────────

_bearer_scheme = HTTPBearer(auto_error=False)


def require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> None:
    if not settings.admin_token:
        raise HTTPException(status_code=503, detail="Admin endpoints disabled — set ADMIN_TOKEN")
    if credentials is None or credentials.credentials != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── Core dispatch (shared by HTTP fallback, worker, and scheduler) ────────────

async def _dispatch_core(
    dedup: DedupStore,
    rules: RuleEngine,
    awx:   AWXClient,
    source: str,
    action: str,
    data:   dict[str, Any],
    event_id: str | None = None,
) -> dict:
    """
    Dedup → rule resolution → AWX launch.
    Returns a result dict describing the outcome.
    """
    if settings.dedup_ttl > 0 and await dedup.is_duplicate(source, action, data):
        logger.info("Duplicate suppressed (source=%s action=%s)", source, action)
        return {"status": "deduplicated", "source": source, "action": action}

    template_id, extra_vars = rules.resolve(source, action, data)
    if event_id:
        extra_vars = {**extra_vars, "_event_id": event_id}

    job = await awx.launch_job(extra_vars=extra_vars, template_id=template_id)

    return {
        "status":      "accepted",
        "job_id":      job.get("id"),
        "job_url":     job.get("url"),
        "template_id": template_id,
        "source":      source,
        "action":      action,
    }


# ── Redis helpers ─────────────────────────────────────────────────────────────

async def _try_connect_redis():
    """
    Attempt to connect to Redis.  Returns the client on success, None on failure.
    A failed connection is not fatal — the engine falls back to fire-and-forget.
    """
    if not settings.redis_url:
        return None
    try:
        import redis.asyncio as aioredis
        client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=False,
            socket_connect_timeout=3,
            socket_timeout=5,
        )
        await client.ping()
        logger.info("Redis connected — event persistence enabled (%s)", settings.redis_url)
        return client
    except Exception as exc:
        logger.warning(
            "Redis unavailable (%s) — falling back to fire-and-forget mode", exc
        )
        return None


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── HTTP client for AWX ──────────────────────────────────────────────────
    http = httpx.AsyncClient(
        base_url=settings.awx_url,
        headers={"Authorization": f"Bearer {settings.awx_token}"},
        timeout=30.0,
    )
    app.state.awx   = AWXClient(http, settings.awx_job_template_id)
    app.state.rules = RuleEngine(settings.rules_file, settings.awx_job_template_id)
    app.state.dedup = DedupStore(ttl_seconds=settings.dedup_ttl)

    # ── Redis — event persistence + retry ────────────────────────────────────
    redis_client = await _try_connect_redis()
    if redis_client is not None:
        app.state.dedup.set_redis(redis_client)
        store = EventStore(redis_client)
        app.state.event_store = store

        # Dispatch function injected into the worker (captures app.state)
        async def _worker_dispatch(
            source: str,
            action: str,
            data: dict,
            event_id: str,
        ) -> dict:
            return await _dispatch_core(
                app.state.dedup,
                app.state.rules,
                app.state.awx,
                source, action, data,
                event_id=event_id,
            )

        worker = RetryWorker(store, _worker_dispatch)
        worker.start()
        app.state.retry_worker = worker
    else:
        app.state.event_store  = None
        app.state.retry_worker = None

    # ── Scheduler — fires cron events ────────────────────────────────────────
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

    mode = "persistent (Redis)" if redis_client else "fire-and-forget (no Redis)"
    logger.info(
        "Event engine v2 started — default_template=%d  dedup_ttl=%ds  "
        "schedules=%d  mode=%s",
        settings.awx_job_template_id,
        settings.dedup_ttl,
        scheduler.loaded_count,
        mode,
    )

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    scheduler.stop()
    if app.state.retry_worker:
        app.state.retry_worker.stop()
    if redis_client:
        await redis_client.aclose()
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

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS ─────────────────────────────────────────────────────────────────────
_cors_origins = parse_cors_origins(
    settings.cors_origins.strip(),
    settings.env,
    logging.getLogger("event_engine.cors"),
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

Instrumentator().instrument(app).expose(app)
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


# ── HTTP dispatch ─────────────────────────────────────────────────────────────

async def _dispatch(
    request: Request,
    source: str,
    action: str,
    data: dict[str, Any],
) -> JSONResponse:
    """
    HTTP-facing dispatch.

    Persistent mode (Redis available):
      Persists the event, enqueues it for the background worker and
      returns 202 immediately with the event_id for tracking.

    Fallback mode (no Redis):
      Calls _dispatch_core synchronously and returns the AWX result.
    """
    store: EventStore | None = request.app.state.event_store

    if store is not None:
        # Quick dedup check before enqueuing to give immediate 200 feedback
        if settings.dedup_ttl > 0 and await request.app.state.dedup.is_duplicate(source, action, data):
            logger.info("Duplicate suppressed at ingestion (source=%s action=%s)", source, action)
            return JSONResponse(
                status_code=200,
                content={"status": "deduplicated", "source": source, "action": action},
            )

        event_id = await store.enqueue(source, action, data)
        logger.info(
            "Event enqueued (source=%s action=%s event_id=%s)",
            source, action, event_id,
        )
        return JSONResponse(
            status_code=202,
            content={
                "status":   "queued",
                "event_id": event_id,
                "source":   source,
                "action":   action,
            },
        )

    # ── Fallback: fire-and-forget ─────────────────────────────────────────────
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
async def health(request: Request):
    store: EventStore | None = request.app.state.event_store
    info: dict[str, Any] = {
        "status":      "ok",
        "service":     "event-engine",
        "version":     "2.0.0",
        "persistence": store is not None,
    }
    if store:
        try:
            info["queue"] = await store.queue_stats()
        except Exception:
            pass
    return info


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
    engine: RuleEngine = request.app.state.rules
    count = engine.reload()
    return {"status": "reloaded", "rules_loaded": count}


# ── Admin — Dedup ─────────────────────────────────────────────────────────────

@app.get("/admin/dedup/stats", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def dedup_stats(request: Request):
    dedup: DedupStore = request.app.state.dedup
    return {
        "enabled":        settings.dedup_ttl > 0,
        "ttl_seconds":    settings.dedup_ttl,
        "backend":        "redis" if dedup._redis is not None else "in-memory",
        "cached_entries": dedup.size(),
    }


@app.post("/admin/dedup/clear", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def dedup_clear(request: Request):
    request.app.state.dedup.clear()
    return {"status": "cleared"}


# ── Admin — Schedules ─────────────────────────────────────────────────────────

@app.get("/admin/schedules", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def list_schedules(request: Request):
    sched: EventScheduler = request.app.state.scheduler
    return {"count": sched.loaded_count, "jobs": sched.jobs()}


@app.post("/admin/schedules/reload", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def reload_schedules(request: Request):
    sched: EventScheduler = request.app.state.scheduler
    count = sched.reload()
    return {"status": "reloaded", "jobs_loaded": count}


# ── Admin — Queue & DLQ (requires Redis) ──────────────────────────────────────

def _require_store(request: Request) -> EventStore:
    store: EventStore | None = request.app.state.event_store
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Event persistence not available — set REDIS_URL to enable",
        )
    return store


@app.get("/admin/queue/stats", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def queue_stats(request: Request):
    """Return the current depth of the pending, retry and dead-letter queues."""
    store = _require_store(request)
    return await store.queue_stats()


@app.get("/admin/dlq", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def dlq_list(request: Request, offset: int = 0, limit: int = 50):
    """
    List events in the dead-letter queue.

    Events land here after ``MAX_ATTEMPTS`` (5) failed dispatch attempts.
    Use ``POST /admin/dlq/{event_id}/requeue`` to re-submit an individual event,
    or ``DELETE /admin/dlq`` to clear all.
    """
    store = _require_store(request)
    events = await store.dlq_list(offset=offset, limit=limit)
    total  = await store.dlq_count()
    return {"total": total, "offset": offset, "limit": limit, "events": events}


@app.post(
    "/admin/dlq/{event_id}/requeue",
    tags=["admin"],
    dependencies=[Depends(require_admin_token)],
)
async def dlq_requeue(event_id: str, request: Request):
    """
    Move a dead event back to the pending queue with a fresh retry budget.
    Returns 404 if the event_id is not found.
    """
    store = _require_store(request)
    ok    = await store.dlq_requeue(event_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return {"status": "requeued", "event_id": event_id}


@app.delete("/admin/dlq", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def dlq_clear(request: Request):
    """Clear all events from the dead-letter queue (irreversible)."""
    store = _require_store(request)
    count = await store.dlq_clear()
    return {"status": "cleared", "removed": count}


@app.get("/admin/events/{event_id}", tags=["admin"], dependencies=[Depends(require_admin_token)])
async def get_event(event_id: str, request: Request):
    """Return the full state of a persisted event by ID."""
    store = _require_store(request)
    event = await store.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return event
