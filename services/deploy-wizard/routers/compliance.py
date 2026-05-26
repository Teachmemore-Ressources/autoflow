"""
routers/compliance.py — Compliance scanning and report retrieval.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from core.shell import _sse
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()

# ── Constants ─────────────────────────────────────────────────────────────────

SCANNER_CONTAINER = "autoflow_security_scanner"
_COMPLIANCE_REPORT_DIR = Path("/tmp/wizard-compliance")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scanner_running() -> bool:
    r = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", SCANNER_CONTAINER],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "true"


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/api/compliance/status")
def compliance_status():
    """Return compliance report metadata (last generated, summary)."""
    import json as _j
    running = _scanner_running()
    _COMPLIANCE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    cached  = _COMPLIANCE_REPORT_DIR / "latest.json"

    result: dict = {
        "scanner_running": running,
        "report_available": cached.exists(),
    }
    if cached.exists():
        try:
            data = _j.loads(cached.read_text())
            result["generated_at"]   = data.get("generated_at", "")
            result["total_findings"] = data.get("summary", {}).get("total_findings", 0)
            result["by_severity"]    = data.get("summary", {}).get("by_severity", {})
        except Exception:
            pass
    return result


@router.get("/api/compliance/generate")
async def compliance_generate():
    """SSE: trigger on-demand compliance report via docker exec inside the scanner."""

    async def stream():
        if not _scanner_running():
            yield _sse("[ERROR] Security scanner container is not running — deploy the stack first.")
            yield _sse("[DONE]")
            return

        yield _sse("Triggering compliance report generation inside security-scanner…")
        yield _sse("(This may take 5–30 min depending on image sizes and Trivy cache)")

        # Stream output from docker exec python3 compliance.py inside the container
        cmd = ["docker", "exec", SCANNER_CONTAINER, "python3", "/app/compliance.py"]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async for raw in proc.stdout:
            line = raw.decode().rstrip()
            if not line:
                continue
            if "ERROR" in line.upper():
                yield _sse(f"[ERROR] {line}" if not line.startswith("[") else line)
            elif "saving" in line.lower() or "complete" in line.lower() or "report saved" in line.lower():
                yield _sse(f"[SUCCESS] {line}" if not line.startswith("[") else line)
            else:
                yield _sse(line)

        rc = await proc.wait()

        if rc != 0:
            yield _sse(f"[ERROR] compliance.py exited with code {rc}")
            yield _sse("[DONE]")
            return

        # Pull report files out of the container for local caching
        _COMPLIANCE_REPORT_DIR.mkdir(parents=True, exist_ok=True)
        for filename in ("latest.json", "latest.md"):
            cp_r = subprocess.run(
                ["docker", "cp",
                 f"{SCANNER_CONTAINER}:/tmp/compliance/{filename}",
                 str(_COMPLIANCE_REPORT_DIR / filename)],
                capture_output=True, text=True,
            )
            if cp_r.returncode != 0:
                yield _sse(f"[WARN] Could not copy {filename} from container: {cp_r.stderr.strip()}")

        yield _sse("[SUCCESS] Compliance report generated and cached.")
        yield _sse("[DONE]")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/api/compliance/report/latest")
def compliance_report_latest():
    """Return the cached compliance report JSON."""
    cached = _COMPLIANCE_REPORT_DIR / "latest.json"
    if not cached.exists():
        raise HTTPException(404, "No compliance report — run generate first")
    return StreamingResponse(
        iter([cached.read_bytes()]),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="compliance-report.json"'},
    )


@router.get("/api/compliance/report/latest/markdown")
def compliance_report_latest_md():
    """Return the cached compliance report as Markdown."""
    cached = _COMPLIANCE_REPORT_DIR / "latest.md"
    if not cached.exists():
        raise HTTPException(404, "No compliance report — run generate first")
    return StreamingResponse(
        iter([cached.read_bytes()]),
        media_type="text/markdown",
        headers={"Content-Disposition": 'attachment; filename="compliance-report.md"'},
    )
