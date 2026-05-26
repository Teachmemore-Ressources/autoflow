"""
EE Builder — Gitea webhook listener
====================================
Receives Gitea push webhooks, detects changes under execution-environments/,
and rebuilds + pushes the affected EE images to the Gitea container registry.

Every successful build pushes TWO tags:
  • {version}  (e.g. "latest") — the floating tag AWX uses
  • {YYYYMMDD-HHmmss}          — an immutable dated tag for rollback

Build history is persisted to HISTORY_FILE (JSON) so it survives restarts.

Endpoints
---------
POST /webhook/gitea          — Gitea push event (validates X-Gitea-Signature)
GET  /builds                 — Persistent build history (newest first)
GET  /versions/{ee}          — List available tags in the registry for an EE
POST /rollback/{ee}/{version} — Re-tag a dated version as "latest" and push (SSE)
GET  /healthz                — Health check
GET  /metrics                — Prometheus metrics
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

# ── Config ────────────────────────────────────────────────────────────────────

WEBHOOK_SECRET = os.getenv("GITEA_WEBHOOK_SECRET", "")
EE_BASE_DIR = os.getenv("EE_BASE_DIR", "/execution-environments")
REGISTRY = os.getenv("REGISTRY", "gitea:3001")  # intra-stack DNS
GITEA_USER = os.getenv("GITEA_USER", "admin")
GITEA_TOKEN = os.getenv("GITEA_REGISTRY_TOKEN", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info").upper()
BUILD_PYCMD = os.getenv("BUILD_PYCMD", "/usr/bin/python3.12")
DEFAULT_VERSION = os.getenv("EE_DEFAULT_VERSION", "latest")
HISTORY_FILE = Path(os.getenv("EE_HISTORY_FILE", "/var/lib/ee-builder/history.json"))
HISTORY_MAX = 200  # records kept on disk

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
log = logging.getLogger("ee-builder")

from tracing import instrument_app, setup_tracing  # noqa: E402

setup_tracing("autoflow-ee-builder")

# ── Prometheus metrics ────────────────────────────────────────────────────────

builds_total = Counter("ee_builder_builds_total", "Total EE build attempts", ["ee", "status"])
builds_running = Gauge("ee_builder_builds_running", "Currently running builds")

# ── Persistent history ────────────────────────────────────────────────────────


def _load_history() -> deque[dict]:
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text())
            if isinstance(data, list):
                return deque(data[-HISTORY_MAX:], maxlen=HISTORY_MAX)
        except Exception as exc:
            log.warning("Could not load build history from %s: %s", HISTORY_FILE, exc)
    return deque(maxlen=HISTORY_MAX)


def _save_history() -> None:
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(list(build_history), indent=2))
    except Exception as exc:
        log.warning("Could not save build history to %s: %s", HISTORY_FILE, exc)


# ── State ─────────────────────────────────────────────────────────────────────

build_history: deque[dict] = _load_history()
_build_lock = asyncio.Semaphore(2)  # max 2 concurrent builds

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="EE Builder", version="1.0.0")
instrument_app(app, "autoflow-ee-builder")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _validate_signature(payload: bytes, header: str) -> bool:
    """Validate X-Gitea-Signature (HMAC-SHA256)."""
    if not WEBHOOK_SECRET:
        return True
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def _detect_changed_ees(commits: list[dict]) -> set[str]:
    """Return set of EE names (subdirs of execution-environments/) that changed."""
    changed: set[str] = set()
    for commit in commits:
        all_files = commit.get("added", []) + commit.get("modified", []) + commit.get("removed", [])
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


def _dated_tag() -> str:
    """Return a sortable immutable tag based on current UTC time."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


async def _push_tag(src_image: str, dst_image: str) -> str:
    """Tag *src_image* as *dst_image* and push it. Returns combined output."""
    tag_r = subprocess.run(
        ["docker", "tag", src_image, dst_image],
        capture_output=True,
        text=True,
    )
    if tag_r.returncode != 0:
        raise RuntimeError(f"docker tag failed: {tag_r.stderr.strip()}")

    push_proc = await asyncio.create_subprocess_exec(
        "docker",
        "push",
        dst_image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    push_out, _ = await push_proc.communicate()
    if push_proc.returncode != 0:
        raise RuntimeError(f"docker push exited {push_proc.returncode}")
    return push_out.decode(errors="replace")


# ── Build task ────────────────────────────────────────────────────────────────


async def _build_ee(ee_name: str, version: str = DEFAULT_VERSION) -> None:
    """Build and push a single EE image (runs in background).

    Pushes two tags:
      • {version}  — the floating tag (e.g. "latest") used by AWX
      • {dated}    — an immutable dated tag for rollback
    """
    dated = _dated_tag()
    image = f"{REGISTRY}/{GITEA_USER}/ee-{ee_name}:{version}"
    image_dt = f"{REGISTRY}/{GITEA_USER}/ee-{ee_name}:{dated}"
    ee_file = f"{EE_BASE_DIR}/{ee_name}/execution-environment.yml"
    ctx_dir = f"/tmp/ee-build-{ee_name}"
    record: dict = {
        "ee": ee_name,
        "image": image,
        "image_dated": image_dt,
        "version": version,
        "dated_tag": dated,
        "started": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "log": "",
        "trigger": "webhook",
    }
    build_history.appendleft(record)
    builds_running.inc()

    async with _build_lock:
        build_log = ""
        try:
            log.info("Building EE %s → %s + %s", ee_name, image, image_dt)

            # ansible-builder build (produces the floating tag)
            build_cmd = [
                "ansible-builder",
                "build",
                "--file",
                ee_file,
                "--tag",
                image,
                "--context",
                ctx_dir,
                "--build-arg",
                f"PYCMD={BUILD_PYCMD}",
                "--verbosity",
                "1",
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

            log.info("Build succeeded for %s, pushing both tags…", ee_name)

            # Push floating tag (e.g. latest)
            push_proc = await asyncio.create_subprocess_exec(
                "docker",
                "push",
                image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            push_out, _ = await push_proc.communicate()
            build_log += push_out.decode(errors="replace")
            if push_proc.returncode != 0:
                raise RuntimeError(f"docker push exited {push_proc.returncode}")

            # Push immutable dated tag for rollback
            build_log += await _push_tag(image, image_dt)

            record["status"] = "success"
            builds_total.labels(ee=ee_name, status="success").inc()
            log.info("EE %s pushed → %s  %s", ee_name, image, image_dt)

        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            builds_total.labels(ee=ee_name, status="failed").inc()
            log.error("EE build failed for %s: %s", ee_name, exc)
            build_log += f"\n\nERROR: {exc}"

        finally:
            record["finished"] = datetime.now(timezone.utc).isoformat()
            record["log"] = build_log[-4096:]  # keep last 4 KB
            builds_running.dec()
            _save_history()


# ── Registry helpers ──────────────────────────────────────────────────────────


def _registry_tags(ee_name: str) -> list[str]:
    """
    Query the Gitea container registry (Docker v2 API) for available tags.
    Returns sorted list (newest dated tags first, then others).
    """
    import base64
    import urllib.error
    import urllib.request

    repo = f"{GITEA_USER}/ee-{ee_name}"
    url = f"http://{REGISTRY}/v2/{repo}/tags/list"
    creds = base64.b64encode(f"{GITEA_USER}:{GITEA_TOKEN}".encode()).decode()

    req = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            tags = data.get("tags") or []
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise RuntimeError(f"Registry API {exc.code}: {exc.reason}")
    except Exception as exc:
        raise RuntimeError(f"Registry unreachable: {exc}")

    # Sort: dated tags (YYYYMMDD-HHmmss format) descending, then others
    dated = sorted([t for t in tags if len(t) == 15 and t[8] == "-"], reverse=True)
    other = [t for t in tags if t not in dated]
    return dated + other


# ── Startup: docker login ─────────────────────────────────────────────────────


@app.on_event("startup")
def _startup() -> None:
    _docker_login()
    log.info("Loaded %d build records from %s", len(build_history), HISTORY_FILE)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.post("/webhook/gitea", status_code=202)
async def gitea_webhook(request: Request, bg: BackgroundTasks) -> dict:
    payload = await request.body()
    sig = request.headers.get("X-Gitea-Signature", "")

    if not _validate_signature(payload, sig):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        data = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    commits = data.get("commits", [])
    changed_ees = _detect_changed_ees(commits)

    if not changed_ees:
        log.debug("Gitea push — no EE changes detected")
        return {"status": "no_ee_changes", "commits": len(commits)}

    log.info("Gitea push — triggering rebuild for EEs: %s", changed_ees)
    for ee_name in changed_ees:
        bg.add_task(_build_ee, ee_name)

    return {"status": "triggered", "ees": sorted(changed_ees)}


@app.get("/builds")
def list_builds() -> list:
    """Return full persistent build history (newest first)."""
    return list(build_history)


@app.get("/versions/{ee_name}")
def list_versions(ee_name: str) -> dict:
    """
    List available tags in the Gitea container registry for *ee_name*.
    Returns dated tags (rollback candidates) and other tags separately.
    """
    try:
        tags = _registry_tags(ee_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    dated = [t for t in tags if len(t) == 15 and t[8] == "-"]
    other = [t for t in tags if t not in dated]
    return {
        "ee": ee_name,
        "image_base": f"{REGISTRY}/{GITEA_USER}/ee-{ee_name}",
        "dated_tags": dated,
        "other_tags": other,
        "total": len(tags),
    }


@app.post("/rollback/{ee_name}/{version}")
async def rollback_ee(ee_name: str, version: str) -> StreamingResponse:
    """
    SSE: re-tag an existing dated version as DEFAULT_VERSION (usually "latest")
    and push it to the registry.  Creates a build history record tagged "rollback".
    """

    async def _stream():
        target = f"{REGISTRY}/{GITEA_USER}/ee-{ee_name}:{DEFAULT_VERSION}"
        source = f"{REGISTRY}/{GITEA_USER}/ee-{ee_name}:{version}"
        dated = _dated_tag()
        record: dict = {
            "ee": ee_name,
            "image": target,
            "image_dated": f"{REGISTRY}/{GITEA_USER}/ee-{ee_name}:{dated}",
            "version": DEFAULT_VERSION,
            "dated_tag": dated,
            "started": datetime.now(timezone.utc).isoformat(),
            "status": "running",
            "log": "",
            "trigger": f"rollback:{version}",
        }
        build_history.appendleft(record)
        rollback_log = ""

        def _emit(msg: str) -> str:
            return f"data: {msg}\n\n"

        yield _emit(f"Rolling back ee-{ee_name} to {version}…")
        yield _emit(f"Source : {source}")
        yield _emit(f"Target : {target}")
        yield _emit("")

        try:
            # Step 1: pull the dated image
            yield _emit(f"[1/3] docker pull {source}…")
            pull_proc = await asyncio.create_subprocess_exec(
                "docker",
                "pull",
                source,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            pull_out, _ = await pull_proc.communicate()
            pull_log = pull_out.decode(errors="replace")
            rollback_log += pull_log
            for ln in pull_log.splitlines():
                if ln.strip():
                    yield _emit(f"  {ln}")
            if pull_proc.returncode != 0:
                raise RuntimeError(f"docker pull failed (exit {pull_proc.returncode})")
            yield _emit("[1/3] Pull OK")

            # Step 2: re-tag as floating version (latest)
            yield _emit(f"[2/3] docker tag → {target}…")
            tag_r = subprocess.run(
                ["docker", "tag", source, target],
                capture_output=True,
                text=True,
            )
            rollback_log += tag_r.stdout + tag_r.stderr
            if tag_r.returncode != 0:
                raise RuntimeError(f"docker tag failed: {tag_r.stderr.strip()}")
            yield _emit("[2/3] Tag OK")

            # Step 3: push as floating tag
            yield _emit(f"[3/3] docker push {target}…")
            push_proc = await asyncio.create_subprocess_exec(
                "docker",
                "push",
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            push_out, _ = await push_proc.communicate()
            push_log = push_out.decode(errors="replace")
            rollback_log += push_log
            for ln in push_log.splitlines():
                if ln.strip():
                    yield _emit(f"  {ln}")
            if push_proc.returncode != 0:
                raise RuntimeError(f"docker push failed (exit {push_proc.returncode})")
            yield _emit("[3/3] Push OK")

            record["status"] = "success"
            builds_total.labels(ee=ee_name, status="success").inc()
            log.info("Rollback succeeded: ee-%s ← %s", ee_name, version)
            yield _emit(f"[SUCCESS] ee-{ee_name} rolled back to {version} → {DEFAULT_VERSION} ✔")

        except Exception as exc:
            record["status"] = "failed"
            record["error"] = str(exc)
            builds_total.labels(ee=ee_name, status="failed").inc()
            log.error("Rollback failed for %s ← %s: %s", ee_name, version, exc)
            rollback_log += f"\n\nERROR: {exc}"
            yield _emit(f"[ERROR] {exc}")

        finally:
            record["finished"] = datetime.now(timezone.utc).isoformat()
            record["log"] = rollback_log[-4096:]
            _save_history()
            yield _emit("[DONE]")

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
