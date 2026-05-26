"""
routers/deploy.py — Docker Compose deployment, service restart and stack status.
"""

from __future__ import annotations

import asyncio
import os
import subprocess

from core.auth import _audit
from core.env import ENV_ENC_FILE, ENV_FILE, ROOT, SOPS_AGE_KEY_FILE, _load_env
from core.shell import _sse
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


# ── Docker Compose deployment ─────────────────────────────────────────────────


@router.get("/api/deploy")
async def deploy(request: Request, encrypt: bool = False):
    """Stream docker compose up -d output via Server-Sent Events."""

    _audit(request, "stack.deploy", encrypt=encrypt)

    async def event_stream():
        if encrypt:
            yield _sse("Encrypting .env with SOPS…")
            env = {**os.environ, "SOPS_AGE_KEY_FILE": str(SOPS_AGE_KEY_FILE)}
            r = subprocess.run(
                ["sops", "--encrypt", "--input-type", "dotenv", "--output-type", "dotenv", str(ENV_FILE)],
                capture_output=True,
                text=True,
                env=env,
            )
            if r.returncode != 0:
                yield _sse(f"[ERROR] SOPS: {r.stderr.strip()}")
                yield _sse("[DONE]")
                return
            ENV_ENC_FILE.write_text(r.stdout)
            yield _sse(".env.enc written.")

        # Ensure critical volumes exist before compose up.
        # External volumes are not touched by `docker compose down -v`.
        critical_volumes = [
            "autoflow_pki_data",
            "autoflow_postgres_data",
            "autoflow_redis_data",
            "autoflow_gitea_data",
            "autoflow_gitea_postgres_data",
        ]
        yield _sse("Ensuring critical volumes exist…")
        for vol in critical_volumes:
            r = subprocess.run(
                ["docker", "volume", "create", vol],
                capture_output=True,
                text=True,
            )
            status = "already exists" if r.returncode == 0 and r.stdout.strip() == vol else r.stdout.strip()
            yield _sse(f"  volume {vol}: {status}")

        yield _sse("Starting: docker compose up -d --build")
        process = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            "--env-file",
            str(ENV_FILE),
            "up",
            "-d",
            "--build",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
        async for line in process.stdout:
            yield _sse(line.decode().rstrip())
        await process.wait()
        if process.returncode != 0:
            yield _sse(f"[ERROR] docker compose exited with code {process.returncode}")
            yield _sse("[DONE]")
            return

        yield _sse("[SUCCESS] Stack deployed successfully!")
        yield _sse("─" * 55)

        # ── Post-deploy: auto-create Gitea admin ─────────────────
        yield _sse("Post-deploy: initializing Gitea admin account…")
        cfg = _load_env()
        git_user = cfg.get("GITEA_ADMIN_USER", "admin")
        git_pass = cfg.get("GITEA_ADMIN_PASSWORD", "")
        git_mail = cfg.get("GITEA_ADMIN_EMAIL", f"{git_user}@localhost")

        if not git_pass:
            yield _sse("[WARN] GITEA_ADMIN_PASSWORD not set — Gitea admin not created automatically.")
            yield _sse("[WARN] Set the password in the Gitea section and redeploy.")
        else:
            # Wait for Gitea to be healthy (up to 90 s)
            yield _sse("  Waiting for Gitea to be healthy (max 90 s)…")
            gitea_ready = False
            for _ in range(18):
                await asyncio.sleep(5)
                hc = subprocess.run(
                    ["docker", "inspect", "--format", "{{.State.Health.Status}}", "autoflow_gitea"],
                    capture_output=True,
                    text=True,
                )
                status = hc.stdout.strip()
                if status == "healthy":
                    gitea_ready = True
                    break
                yield _sse(f"  Gitea: {status}…")

            if not gitea_ready:
                yield _sse("[WARN] Gitea not yet healthy — proceeding anyway…")

            init_proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "autoflow_gitea",
                "gitea",
                "admin",
                "user",
                "create",
                "--username",
                git_user,
                "--password",
                git_pass,
                "--email",
                git_mail,
                "--admin",
                "--must-change-password=false",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for line in init_proc.stdout:
                yield _sse("  " + line.decode().rstrip())
            await init_proc.wait()
            if init_proc.returncode == 0:
                yield _sse(f"[SUCCESS] Gitea admin '{git_user}' created ✔")
            else:
                yield _sse(f"[INFO] Gitea admin '{git_user}' already exists (re-deploy) — OK ✔")

        # ── Post-deploy: provision AWX instance groups ───────────
        yield _sse("Post-deploy: provisioning AWX instance groups…")

        # Wait for AWX web to be healthy (up to 120 s)
        yield _sse("  Waiting for AWX to be healthy (max 120 s)…")
        awx_ready = False
        for _ in range(24):
            await asyncio.sleep(5)
            hc = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Health.Status}}", "autoflow_awx_web"],
                capture_output=True,
                text=True,
            )
            if hc.stdout.strip() == "healthy":
                awx_ready = True
                break
            yield _sse(f"  AWX: {hc.stdout.strip()}…")

        if not awx_ready:
            yield _sse("[WARN] AWX not yet healthy — proceeding anyway…")

        hostname = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip()

        awx_cmds = [
            (
                ["awx-manage", "provision_instance", f"--hostname={hostname}", "--node_type=control"],
                "provision_instance",
            ),
            (
                ["awx-manage", "register_queue", "--queuename=controlplane", "--instance_percent=100"],
                "register_queue controlplane",
            ),
            (
                ["awx-manage", "register_queue", "--queuename=default", "--instance_percent=100"],
                "register_queue default",
            ),
            (
                ["awx-manage", "register_default_execution_environments"],
                "register_default_execution_environments",
            ),
        ]

        for cmd, label in awx_cmds:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "autoflow_awx_task",
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out = []
            async for line in proc.stdout:
                out.append(line.decode().rstrip())
            await proc.wait()
            if proc.returncode == 0:
                detail = " — " + out[-1] if out else ""
                yield _sse(f"  {label}{detail} ✔")
            else:
                yield _sse(f"[WARN] {label} failed: {' | '.join(out)}")

        yield _sse("[SUCCESS] AWX instance groups provisioned ✔")
        yield _sse("─" * 55)

        # ── Post-deploy URL summary ──────────────────────────────────────────
        _domain = cfg.get("DOMAIN", "localhost")
        _awx_user = cfg.get("AWX_ADMIN_USER", "admin")
        _git_user = cfg.get("GITEA_ADMIN_USER", "admin")
        yield _sse("🎉  Deployment complete — your services:")
        yield _sse(f"  AWX        → https://awx.{_domain}  (user: {_awx_user})")
        yield _sse(f"  Gitea      → https://git.{_domain}  (user: {_git_user})")
        yield _sse(f"  API        → https://api.{_domain}")
        yield _sse(f"  Monitoring → https://monitoring.{_domain}")
        yield _sse(f"  PKI        → https://pki.{_domain}")
        yield _sse(f"  MinIO      → https://minio.{_domain}")
        yield _sse("─" * 55)
        yield _sse("[DONE]")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Targeted service restart ───────────────────────────────────────────────────


@router.get("/api/restart")
async def restart_services(request: Request, services: str = ""):
    """SSE: docker compose restart <services>."""
    service_list = [s.strip() for s in services.split(",") if s.strip()]
    _audit(request, "services.restart", services=service_list)

    async def stream():
        if not service_list:
            yield _sse("[ERROR] No services specified")
            yield _sse("[DONE]")
            return
        yield _sse(f"Restarting: {', '.join(service_list)}")
        process = await asyncio.create_subprocess_exec(
            "docker",
            "compose",
            "--env-file",
            str(ENV_FILE),
            "restart",
            *service_list,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(ROOT),
        )
        async for line in process.stdout:
            yield _sse(line.decode().rstrip())
        await process.wait()
        if process.returncode == 0:
            yield _sse(f"[SUCCESS] Restarted: {', '.join(service_list)}")
        else:
            yield _sse(f"[ERROR] docker compose restart exited with code {process.returncode}")
        yield _sse("[DONE]")

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Stack status ──────────────────────────────────────────────────────────────


@router.get("/api/status")
def stack_status():
    result = subprocess.run(
        ["docker", "compose", "ps", "--format", "table {{.Name}}\t{{.Status}}"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    running = result.stdout.count("Up") if result.returncode == 0 else 0
    return {"running": running, "output": result.stdout}
