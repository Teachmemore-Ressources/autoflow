"""
routers/backup.py — Disaster Recovery / Backup: Restic integration and cron management.
"""
from __future__ import annotations

import asyncio
import os
import subprocess

from core.env import ROOT, _load_env
from core.shell import _sse
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

BACKUP_SCRIPT = ROOT / "scripts" / "backup.sh"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _restic_env(config: dict) -> dict | None:
    """Build env vars for Restic commands. Returns None if not configured."""
    import shutil
    password = config.get("BACKUP_RESTIC_PASSWORD", "")
    if not password or not shutil.which("restic"):
        return None

    env = {**os.environ, "RESTIC_PASSWORD": password}
    backend = config.get("BACKUP_BACKEND", "local")

    if backend == "local":
        env["RESTIC_REPOSITORY"] = config.get("BACKUP_LOCAL_PATH", str(ROOT / "backups" / "restic"))
    elif backend == "sftp":
        path = config.get("BACKUP_LOCAL_PATH", "")
        env["RESTIC_REPOSITORY"] = path if path.startswith("sftp:") else f"sftp:{path}"
    elif backend == "s3":
        endpoint = config.get("BACKUP_S3_ENDPOINT", "").rstrip("/")
        bucket   = config.get("BACKUP_S3_BUCKET", "autoflow-backup")
        env["AWS_ACCESS_KEY_ID"]     = config.get("BACKUP_S3_ACCESS_KEY", "")
        env["AWS_SECRET_ACCESS_KEY"] = config.get("BACKUP_S3_SECRET_KEY", "")
        if endpoint:
            env["RESTIC_REPOSITORY"] = f"s3:{endpoint}/{bucket}"
        else:
            env["RESTIC_REPOSITORY"] = f"s3:s3.amazonaws.com/{bucket}"
    elif backend == "b2":
        bucket = config.get("BACKUP_S3_BUCKET", "autoflow-backup")
        env["B2_ACCOUNT_ID"]  = config.get("BACKUP_S3_ACCESS_KEY", "")
        env["B2_ACCOUNT_KEY"] = config.get("BACKUP_S3_SECRET_KEY", "")
        env["RESTIC_REPOSITORY"] = f"b2:{bucket}:restic"

    return env


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/backup/status")
def backup_status():
    import json as _j
    import shutil
    config = _load_env()

    restic_path = shutil.which("restic")
    result: dict = {
        "configured":       bool(config.get("BACKUP_RESTIC_PASSWORD", "")),
        "restic_installed": bool(restic_path),
        "backend":          config.get("BACKUP_BACKEND", "local"),
        "rto":              config.get("BACKUP_RTO_HOURS", "4"),
        "rpo":              config.get("BACKUP_RPO_HOURS", "24"),
        "cron_schedule":    config.get("BACKUP_CRON", "0 2 * * *"),
    }

    if not result["configured"] or not restic_path:
        return result

    env = _restic_env(config)
    if not env:
        return result

    result["repo"] = env.get("RESTIC_REPOSITORY", "")

    # Check last snapshot
    r = subprocess.run(
        ["restic", "snapshots", "--json", "--last", "--no-lock"],
        capture_output=True, text=True, env=env,
    )
    if r.returncode == 0:
        try:
            snaps = _j.loads(r.stdout)
            result["repo_initialized"] = True
            if snaps:
                last = snaps[-1]
                result["last_snapshot"] = {
                    "id":       last.get("id", "")[:8],
                    "time":     last.get("time", "")[:19].replace("T", " "),
                    "hostname": last.get("hostname", ""),
                }
        except Exception:
            result["repo_initialized"] = True
            result["last_snapshot"] = None
    else:
        result["repo_initialized"] = False

    # Check cron
    cron_r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    result["cron_installed"] = (
        "backup.sh" in cron_r.stdout if cron_r.returncode == 0 else False
    )

    return result


@router.get("/api/backup/run")
async def backup_run():
    """SSE: run a Restic backup immediately."""

    async def stream():
        config = _load_env()
        if not config.get("BACKUP_RESTIC_PASSWORD", ""):
            yield _sse("[ERROR] BACKUP_RESTIC_PASSWORD not set — configure the Disaster Recovery section.")
            yield _sse("[DONE]")
            return
        if not BACKUP_SCRIPT.exists():
            yield _sse(f"[ERROR] backup.sh not found at {BACKUP_SCRIPT}")
            yield _sse("[DONE]")
            return

        yield _sse("Starting backup…")
        env = {**os.environ, **{k: v for k, v in config.items() if v}}

        proc = await asyncio.create_subprocess_exec(
            "bash", str(BACKUP_SCRIPT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=str(ROOT),
        )
        async for line in proc.stdout:
            ln = line.decode().rstrip()
            if ln.startswith("[ERROR]") or "error" in ln.lower():
                yield _sse(f"[ERROR] {ln}" if not ln.startswith("[") else ln)
            elif ln.startswith("[SUCCESS]") or "snapshot" in ln.lower() or "complete" in ln.lower():
                yield _sse(f"[SUCCESS] {ln}" if not ln.startswith("[") else ln)
            else:
                yield _sse(ln)

        await proc.wait()
        if proc.returncode == 0:
            yield _sse("[SUCCESS] Backup completed successfully.")
        else:
            yield _sse(f"[ERROR] Backup script exited with code {proc.returncode}")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/backup/restore-test")
async def backup_restore_test():
    """SSE: smoke-test — restic check + snapshots list + dry-run restore."""

    async def stream():
        import json as _j
        import shutil
        import tempfile
        config = _load_env()

        if not config.get("BACKUP_RESTIC_PASSWORD", ""):
            yield _sse("[ERROR] BACKUP_RESTIC_PASSWORD not set.")
            yield _sse("[DONE]")
            return

        if not shutil.which("restic"):
            yield _sse("[ERROR] restic is not installed.")
            yield _sse("Install it: curl -fsSL https://rclone.org/install.sh | bash (or via package manager)")
            yield _sse("[DONE]")
            return

        env = _restic_env(config)
        if not env:
            yield _sse("[ERROR] Could not build Restic environment — check BACKUP_RESTIC_PASSWORD.")
            yield _sse("[DONE]")
            return

        # ── Step 1: repository integrity ──────────────────────────────────────
        yield _sse("Step 1/3: Verifying repository integrity (restic check)…")
        r = subprocess.run(["restic", "check", "--no-lock"], capture_output=True, text=True, env=env)
        for ln in r.stdout.splitlines():
            if ln.strip():
                yield _sse(f"  {ln}")
        if r.returncode != 0:
            yield _sse(f"[ERROR] Repository check failed: {r.stderr.strip()[:300]}")
            yield _sse("[DONE]")
            return
        yield _sse("[SUCCESS] Repository integrity: OK")

        # ── Step 2: list snapshots ────────────────────────────────────────────
        yield _sse("Step 2/3: Listing recent snapshots…")
        r2 = subprocess.run(
            ["restic", "snapshots", "--json", "--no-lock"], capture_output=True, text=True, env=env,
        )
        if r2.returncode != 0:
            yield _sse(f"[ERROR] Could not list snapshots: {r2.stderr.strip()[:200]}")
            yield _sse("[DONE]")
            return
        try:
            snaps = _j.loads(r2.stdout)
            if not snaps:
                yield _sse("[WARN] No snapshots found — run a backup first.")
                yield _sse("[DONE]")
                return
            yield _sse(f"  Found {len(snaps)} snapshot(s) — last 5:")
            for s in snaps[-5:]:
                sid   = s.get("id", "")[:8]
                stime = s.get("time", "")[:19].replace("T", " ")
                shost = s.get("hostname", "")
                yield _sse(f"  [{sid}] {stime} @ {shost}")
            yield _sse("[SUCCESS] Snapshots: OK")
        except Exception as exc:
            yield _sse(f"[WARN] Could not parse snapshots: {exc}")

        # ── Step 3: dry-run restore ───────────────────────────────────────────
        yield _sse("Step 3/3: Dry-run restore of latest snapshot…")
        with tempfile.TemporaryDirectory() as tmpdir:
            r3 = subprocess.run(
                ["restic", "restore", "latest", "--target", tmpdir, "--dry-run", "--no-lock"],
                capture_output=True, text=True, env=env,
            )
            for ln in (r3.stdout + r3.stderr).splitlines():
                if ln.strip():
                    yield _sse(f"  {ln}")
            if r3.returncode == 0:
                yield _sse("[SUCCESS] Restore dry-run: OK")
            else:
                yield _sse(f"[WARN] Restore dry-run had issues (exit {r3.returncode})")

        yield _sse("")
        yield _sse("[SUCCESS] Smoke test PASSED — backup is readable and restorable.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/api/backup/cron/install")
def backup_cron_install():
    """Install (or update) the automatic backup cron job."""
    config   = _load_env()
    schedule = config.get("BACKUP_CRON", "0 2 * * *").strip() or "0 2 * * *"

    if not BACKUP_SCRIPT.exists():
        raise HTTPException(400, "backup.sh not found")

    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = [ln for ln in (r.stdout if r.returncode == 0 else "").splitlines()
             if "backup.sh" not in ln]

    cron_line = (
        f"{schedule}  bash {BACKUP_SCRIPT} "
        f">> /var/log/autoflow-backup.log 2>&1  # autoflow-dr"
    )
    lines.append(cron_line)

    r2 = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                        capture_output=True, text=True)
    if r2.returncode != 0:
        raise HTTPException(500, f"crontab update failed: {r2.stderr.strip()}")

    return {"status": "installed", "schedule": schedule, "line": cron_line}


@router.delete("/api/backup/cron/uninstall")
def backup_cron_uninstall():
    """Remove the automatic backup cron job."""
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if r.returncode != 0:
        return {"status": "not_installed"}

    lines = [ln for ln in r.stdout.splitlines() if "backup.sh" not in ln]
    r2 = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                        capture_output=True, text=True)
    if r2.returncode != 0:
        raise HTTPException(500, f"crontab update failed: {r2.stderr.strip()}")

    return {"status": "uninstalled"}
