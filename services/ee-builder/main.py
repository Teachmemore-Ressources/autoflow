"""
EE Builder — Gitea webhook listener
====================================
Receives Gitea push webhooks, detects changes under execution-environments/,
and rebuilds + pushes the affected EE images to the Gitea container registry.

Endpoints
---------
POST /webhook/gitea  — Gitea push event (validates X-Gitea-Signature)
GET  /builds         — Recent build history (last 20)
GET  /healthz        — Health check
GET  /metrics        — Prometheus metrics
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import subprocess
from collections import deque
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ── Config ────────────────────────────────────────────────────────────────────

WEBHOOK_SECRET  = os.getenv("GITEA_WEBHOOK_SECRET", "")
EE_BASE_DIR     = os.getenv("EE_BASE_DIR", "/execution-environments")
REGISTRY        = os.getenv("REGISTRY", "gitea:3001")           # intra-stack DNS
GITEA_USER      = os.getenv("GITEA_USER", "admin")
GITEA_TOKEN     = os.getenv("GITEA_REGISTRY_TOKEN", "")
LOG_LEVEL       = os.getenv("LOG_LEVEL", "info").upper()
BUILD_PYCMD     = os.getenv("BUILD_PYCMD", "/usr/bin/python3.12")
DEFAULT_VERSION = os.getenv("EE_DEFAULT_VERSION", "latest")

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("ee-builder")

from tracing import instrument_app, setup_tracing  # noqa: E402
setup_tracing("autoflow-ee-builder")

# ── Prometheus metrics ────────────────────────────────────────────────────────

builds_total   = Counter("ee_builder_builds_total",   "Total EE build attempts", ["ee", "status"])
builds_running = Gauge("ee_builder_builds_running",   "Currently running builds")

# ── State ─────────────────────────────────────────────────────────────────────

build_history: deque[dict] = deque(maxlen=20)
_build_lock = asyncio.Semaphore(2)   # max 2 concurrent builds

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="EE Builder", version="1.0.0")
instrument_app(app, "autoflow-ee-builder")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_signature(payload: bytes, header: str) -> bool:
    """Validate X-Gitea-Signature (HMAC-SHA256)."""
    if not WEBHOOK_SECRET:
        return True
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)


def _detect_changed_ees(commits: list[dict]) -> set[str]:
    """Return set of EE names (subdirs of execution-environments/) that changed."""
    changed: set[str] = set()
    for commit in commits:
        all_files = (
            commit.get("added", [])
            + commit.get("modified", [])
            + commit.get("removed", [])
        )
        for path in all_files:
            parts = path.split("/")
            # execution-environments/<ee-name>/...
            if parts[0] == "execution-environments" and len(parts) >= 3:
                ee_name = parts[1]
                ee_dir = f"{EE_BASE_DIR}/{ee_name}"
                if os.path.isdir(ee_dir):
                    changed.add(ee_name)
                else:
                    log.warning("EE dir not found (removed?): %s", ee_dir)
    return changed


def _docker_login() -> bool:
    """Authenticate to the Gitea container registry."""
    if not GITEA_TOKEN:
        log.warning("GITEA_REGISTRY_TOKEN not set — skipping docker login")
        return True
    result = subprocess.run(
        ["docker", "login", REGISTRY, "-u", GITEA_USER, "--password-stdin"],
        input=GITEA_TOKEN,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("docker login failed: %s", result.stderr)
    return result.returncode == 0


# ── Build task ────────────────────────────────────────────────────────────────

async def _build_ee(ee_name: str, version: str = DEFAULT_VERSION) -> None:
    """Build and push a single EE image (runs in background)."""
    image      = f"{REGISTRY}/{GITEA_USER}/ee-{ee_name}:{version}"
    ee_file    = f"{EE_BASE_DIR}/{ee_name}/execution-environment.yml"
    ctx_dir    = f"/tmp/ee-build-{ee_name}"
    record: dict = {
        "ee":      ee_name,
        "image":   image,
        "version": version,
        "started": datetime.now(timezone.utc).isoformat(),
        "status":  "running",
        "log":     "",
    }
    build_history.appendleft(record)
    builds_running.inc()

    async with _build_lock:
        try:
            log.info("Building EE %s → %s", ee_name, image)

            # ansible-builder build
            build_cmd = [
                "ansible-builder", "build",
                "--file",      ee_file,
                "--tag",       image,
                "--context",   ctx_dir,
                "--build-arg", f"PYCMD={BUILD_PYCMD}",
                "--verbosity", "1",
            ]
            proc = await asyncio.create_subprocess_exec(
                *build_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            build_log = stdout.decode(errors="replace")

            if proc.returncode != 0:
                raise RuntimeError(f"ansible-builder exited {proc.returncode}")

            log.info("Build succeeded for %s, pushing…", ee_name)

            # docker push
            push_proc = await asyncio.create_subprocess_exec(
                "docker", "push", image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            push_out, _ = await push_proc.communicate()
            build_log += push_out.decode(errors="replace")

            if push_proc.returncode != 0:
                raise RuntimeError(f"docker push exited {push_proc.returncode}")

            record["status"] = "success"
            builds_total.labels(ee=ee_name, status="success").inc()
            log.info("EE %s pushed successfully → %s", ee_name, image)

        except Exception as exc:
            record["status"] = "failed"
            record["error"]  = str(exc)
            builds_total.labels(ee=ee_name, status="failed").inc()
            log.error("EE build failed for %s: %s", ee_name, exc)
            build_log = record.get("log", "") + f"\n\nERROR: {exc}"

        finally:
            record["finished"] = datetime.now(timezone.utc).isoformat()
            record["log"]      = build_log[-4000:]   # keep last 4KB
            builds_running.dec()


# ── Startup: docker login ─────────────────────────────────────────────────────

@app.on_event("startup")
def _startup() -> None:
    _docker_login()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/webhook/gitea", status_code=202)
async def gitea_webhook(request: Request, bg: BackgroundTasks) -> dict:
    payload = await request.body()
    sig     = request.headers.get("X-Gitea-Signature", "")

    if not _validate_signature(payload, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    import json
    try:
        data = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    commits      = data.get("commits", [])
    changed_ees  = _detect_changed_ees(commits)

    if not changed_ees:
        log.debug("Gitea push — no EE changes detected")
        return {"status": "no_ee_changes", "commits": len(commits)}

    log.info("Gitea push — triggering rebuild for EEs: %s", changed_ees)
    for ee_name in changed_ees:
        bg.add_task(_build_ee, ee_name)

    return {"status": "triggered", "ees": sorted(changed_ees)}


@app.get("/builds")
def list_builds() -> list:
    """Return recent build history."""
    return list(build_history)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
