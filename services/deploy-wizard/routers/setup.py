"""
routers/setup.py — Post-deploy setup: Gitea admin, AWX token, runner registration, Grafana export.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

from core.auth import _audit
from core.env import ENV_FILE, ROOT, _load_env
from core.shell import _sse
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── Grafana helpers ───────────────────────────────────────────────────────────

GRAFANA_DASHBOARDS_DIR = ROOT / "monitoring" / "grafana" / "dashboards"


def _grafana_api_url() -> str:
    domain = _load_env().get("DOMAIN", "localhost")
    return f"https://grafana.{domain}/api"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/init-gitea")
async def init_gitea(request: Request):
    """SSE: create the Gitea admin user via 'gitea admin user create'."""
    _audit(request, "gitea.init_admin")

    async def stream():
        config   = _load_env()
        username = config.get("GITEA_ADMIN_USER", "admin")
        password = config.get("GITEA_ADMIN_PASSWORD", "")
        email    = config.get("GITEA_ADMIN_EMAIL", f"{username}@localhost")

        if not password:
            yield _sse("[ERROR] GITEA_ADMIN_PASSWORD is not set. Fill in the Gitea section first.")
            yield _sse("[DONE]")
            return

        # Check the container is running
        check = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_gitea"],
            capture_output=True, text=True,
        )
        if check.stdout.strip() != "true":
            yield _sse("[ERROR] Container 'autoflow_gitea' is not running. Deploy the stack first.")
            yield _sse("[DONE]")
            return

        yield _sse(f"Creating Gitea admin user '{username}'…")
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "autoflow_gitea",
            "gitea", "admin", "user", "create",
            "--username", username,
            "--password", password,
            "--email",    email,
            "--admin",
            "--must-change-password=false",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()

        if proc.returncode == 0:
            yield _sse(f"[SUCCESS] Admin user '{username}' created — you can now log in.")
        else:
            yield _sse("[ERROR] User creation failed (may already exist — try logging in).")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/init-gitea-status")
def init_gitea_status():
    """Check whether the Gitea admin user already exists."""
    config   = _load_env()
    username = config.get("GITEA_ADMIN_USER", "admin")
    result   = subprocess.run(
        ["docker", "exec", "autoflow_gitea",
         "gitea", "admin", "user", "list", "--admin"],
        capture_output=True, text=True,
    )
    exists = username in result.stdout if result.returncode == 0 else False
    running = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_gitea"],
        capture_output=True, text=True,
    ).stdout.strip() == "true"
    return {"username": username, "exists": exists, "running": running}


@router.get("/api/init-awx-token")
async def init_awx_token(request: Request):
    """SSE: create an AWX API token and save it to .env + restart event_engine."""
    _audit(request, "awx.init_token")

    async def stream():
        config       = _load_env()
        awx_user     = config.get("AWX_ADMIN_USER", "admin")
        awx_password = config.get("AWX_ADMIN_PASSWORD", "")

        if not awx_password:
            yield _sse("[ERROR] AWX_ADMIN_PASSWORD is not set.")
            yield _sse("[DONE]")
            return

        import json as _json

        # ── Step 1: verify container is running ───────────────────────────
        chk = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", "autoflow_awx_web"],
            capture_output=True, text=True,
        )
        container_status = chk.stdout.strip()
        yield _sse(f"Container autoflow_awx_web status: {container_status or '(not found)'}")
        if container_status != "running":
            yield _sse("[ERROR] Container is not running. Start the stack first: make start")
            yield _sse("[DONE]")
            return

        # ── Step 2: detect AWX internal port (nginx proxy → uwsgi) ───────
        # Try 8052 first (default), fallback to 80
        for port in ("8052", "80"):
            probe = subprocess.run(
                ["docker", "exec", "autoflow_awx_web",
                 "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                 f"http://localhost:{port}/api/v2/ping/"],
                capture_output=True, text=True,
            )
            if probe.stdout.strip() in ("200", "401"):
                awx_port = port
                yield _sse(f"AWX API reachable on port {awx_port} (HTTP {probe.stdout.strip()}).")
                break
        else:
            yield _sse("[ERROR] AWX API not reachable on port 8052 or 80 — AWX may still be starting.")
            yield _sse("[DONE]")
            return

        # ── Step 3: create token ──────────────────────────────────────────
        yield _sse("Creating API token…")
        payload = _json.dumps({
            "description": "Autoflow Event Engine",
            "application": None,
            "scope":       "write",
        })
        result = subprocess.run(
            [
                "docker", "exec", "autoflow_awx_web",
                "curl", "-s", "-X", "POST",
                "-u", f"{awx_user}:{awx_password}",
                "-H", "Content-Type: application/json",
                "-d", payload,
                f"http://localhost:{awx_port}/api/v2/tokens/",
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            yield _sse(f"[ERROR] docker exec failed: {result.stderr.strip()}")
            yield _sse("[DONE]")
            return

        yield _sse(f"AWX response: {result.stdout[:300]}")

        try:
            r_data = _json.loads(result.stdout)
        except Exception:
            yield _sse("[ERROR] Could not parse AWX response (see above).")
            yield _sse("[DONE]")
            return

        if "token" not in r_data:
            detail = r_data.get("detail", r_data.get("non_field_errors", result.stdout[:200]))
            yield _sse(f"[ERROR] AWX returned no token — {detail}")
            yield _sse("[DONE]")
            return

        token = r_data["token"]
        yield _sse(f"Token created (id={r_data.get('id')}). Writing to .env…")
        current = _load_env()
        current["AWX_TOKEN"] = token
        from core.env import _write_env
        _write_env(current)
        yield _sse("AWX_TOKEN written to .env.")

        yield _sse("Restarting event_engine to pick up new token…")
        proc = await asyncio.create_subprocess_exec(
            "docker", "compose", "--env-file", str(ENV_FILE), "restart", "event_engine",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, cwd=str(ROOT),
        )
        async for line in proc.stdout:
            yield _sse(line.decode().rstrip())
        await proc.wait()
        yield _sse("[SUCCESS] AWX token configured and event_engine restarted.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Gitea Actions Runner ──────────────────────────────────────────────────────

@router.get("/api/runner/status")
def runner_status():
    """Check runner registration status.

    Gitea 1.21+ does not expose GET /api/v1/admin/runners.
    We derive status from two reliable signals:
      1. GITEA_RUNNER_TOKEN is set in .env  (token was obtained)
      2. act_runner container is running and not spamming "token is empty"
    """
    env   = _load_env()
    token = env.get("GITEA_RUNNER_TOKEN", "").strip()
    has_token = bool(token)

    # Check container state via docker inspect (direct, no compose overhead)
    container_up   = False
    token_error    = False
    registered_log = False
    try:
        inspect = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", "autoflow_act_runner"],
            capture_output=True, text=True, timeout=5,
        )
        container_up = inspect.stdout.strip() == "running"

        # docker logs writes to stderr; capture both streams
        logs = subprocess.run(
            ["docker", "logs", "--tail", "30", "autoflow_act_runner"],
            capture_output=True, text=True, timeout=5,
        )
        combined = (logs.stdout + logs.stderr).lower()
        token_error    = "token is empty" in combined
        registered_log = "runner registered successfully" in combined
    except Exception:
        pass

    registered = has_token and container_up and registered_log and not token_error
    return {
        "registered":    registered,
        "runner_count":  1 if registered else 0,
        "has_token":     has_token,
        "container_up":  container_up,
        "registered_log": registered_log,
        "token_error":   token_error,
    }


@router.get("/api/runner/register")
async def runner_register():
    async def stream():
        script = ROOT / "scripts" / "gitea-init-runner.sh"
        if not script.exists():
            yield _sse(f"[ERROR] Script not found: {script}")
            yield _sse("[DONE]")
            return

        env = _load_env()
        env_vars = {**os.environ, **env}
        yield _sse("Lancement de gitea-init-runner.sh…")

        proc = await asyncio.create_subprocess_exec(
            "bash", str(script),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=env_vars, cwd=str(ROOT),
        )
        async for raw in proc.stdout:
            line = raw.decode().rstrip()
            if not line:
                continue
            cls = "[ERROR]" if "✖" in line or "ERROR" in line else \
                  "[SUCCESS]" if "✔" in line or "Runner" in line and "registered" in line else ""
            yield _sse(f"{cls} {line}".strip() if cls else line)
        rc = await proc.wait()
        if rc == 0:
            yield _sse("[SUCCESS] Runner registered in Gitea Actions ✔")
        else:
            yield _sse(f"[ERROR] gitea-init-runner.sh failed (code {rc})")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Grafana dashboard export ──────────────────────────────────────────────────

@router.get("/api/grafana/export-status")
def grafana_export_status():
    """Return the mtime of the newest dashboard file and whether Grafana is running."""
    running = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_grafana"],
        capture_output=True, text=True,
    ).stdout.strip() == "true"

    files = list(GRAFANA_DASHBOARDS_DIR.glob("*.json")) if GRAFANA_DASHBOARDS_DIR.exists() else []
    last_export: str | None = None
    if files:
        newest = max(files, key=lambda p: p.stat().st_mtime)
        last_export = newest.stat().st_mtime.__class__  # just need the value
        import datetime
        ts = datetime.datetime.fromtimestamp(newest.stat().st_mtime)
        last_export = ts.strftime("%Y-%m-%d %H:%M")

    return {
        "running":     running,
        "file_count":  len(files),
        "last_export": last_export,
    }


@router.get("/api/grafana/export")
async def grafana_export():
    """SSE: export all Grafana dashboards from the live instance to JSON files."""

    async def stream():
        import json as _j
        import re

        import httpx as _httpx

        config   = _load_env()
        user     = config.get("GRAFANA_ADMIN_USER", "admin")
        password = config.get("GRAFANA_ADMIN_PASSWORD", "")

        if not password:
            yield _sse("[ERROR] GRAFANA_ADMIN_PASSWORD not set — fill in the Monitoring section.")
            yield _sse("[DONE]")
            return

        # Verify container is running
        chk = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", "autoflow_grafana"],
            capture_output=True, text=True,
        )
        if chk.stdout.strip() != "true":
            yield _sse("[ERROR] Container 'autoflow_grafana' is not running — deploy the stack first.")
            yield _sse("[DONE]")
            return

        grafana_url = _grafana_api_url().rstrip("/api")
        yield _sse(f"Connecting to Grafana ({grafana_url})…")

        GRAFANA_DASHBOARDS_DIR.mkdir(parents=True, exist_ok=True)

        async with _httpx.AsyncClient(verify=False, auth=(user, password), timeout=20) as c:

            # ── List all dashboards ───────────────────────────────────────────
            try:
                r = await c.get(f"{grafana_url}/api/search", params={"type": "dash-db", "limit": 500})
            except Exception as exc:
                yield _sse(f"[ERROR] Cannot reach Grafana: {exc}")
                yield _sse(f"Make sure the stack is running and {grafana_url} resolves.")
                yield _sse("[DONE]")
                return

            if r.status_code == 401:
                yield _sse("[ERROR] Auth failed — check GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD.")
                yield _sse("[DONE]")
                return
            if r.status_code != 200:
                yield _sse(f"[ERROR] Grafana API returned HTTP {r.status_code}: {r.text[:200]}")
                yield _sse("[DONE]")
                return

            items = [i for i in r.json() if i.get("uid")]
            yield _sse(f"Found {len(items)} dashboard(s) — exporting…")

            saved, skipped = 0, 0
            for item in items:
                uid   = item["uid"]
                title = item.get("title", uid)

                dr = await c.get(f"{grafana_url}/api/dashboards/uid/{uid}")
                if dr.status_code != 200:
                    yield _sse(f"[WARN] Could not fetch '{title}' (HTTP {dr.status_code}) — skipping.")
                    skipped += 1
                    continue

                dashboard = dr.json().get("dashboard", {})
                dashboard.pop("id", None)   # strip DB-internal ID; UID is preserved

                slug = re.sub(r"[^\w\-]", "_", title.lower()).strip("_")
                slug = re.sub(r"_+", "_", slug)[:60]
                out  = GRAFANA_DASHBOARDS_DIR / f"{slug}.json"

                out.write_text(_j.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
                yield _sse(f"  [{saved + 1}/{len(items)}] {title}  →  {out.name}")
                saved += 1

        yield _sse("")
        yield _sse(f"[SUCCESS] {saved} dashboard(s) saved to monitoring/grafana/dashboards/")
        if skipped:
            yield _sse(f"[WARN] {skipped} dashboard(s) could not be fetched.")
        yield _sse("Commit these files to preserve dashboard changes across fresh deploys.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
