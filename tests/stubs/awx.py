"""
AWX Stub — lightweight ASGI app simulating the AWX API.

Implements just enough of the AWX API to run integration and E2E tests:
  GET  /api/v2/ping/
  POST /api/v2/job_templates/{template_id}/launch/
  GET  /api/v2/jobs/{job_id}/
  GET  /api/v2/jobs/{job_id}/stdout/   (for API history calls)

Test-control endpoints (no auth required):
  GET    /_test/launches           — list all recorded job launches
  GET    /_test/jobs               — list all job objects
  PUT    /_test/jobs/{id}/status   — set job status (simulate completion)
  DELETE /_test/reset              — clear all state

Usage as standalone server (for docker-compose.test.yml):
  python awx.py                    — listens on 0.0.0.0:8052
"""
from __future__ import annotations

import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

app = FastAPI(title="AWX Stub", version="1.0.0-stub")

# ── In-memory state ───────────────────────────────────────────────────────────

_launches: list[dict]     = []
_jobs:     dict[int, dict] = {}
_next_id:  list[int]       = [1]   # mutable int-in-list to avoid global


def _new_job(template_id: int, extra_vars: dict) -> dict:
    job_id = _next_id[0]
    _next_id[0] += 1
    job: dict[str, Any] = {
        "id":           job_id,
        "type":         "job",
        "url":          f"/api/v2/jobs/{job_id}/",
        "status":       "running",
        "failed":       False,
        "started":      "2026-04-26T12:00:00.000000Z",
        "finished":     None,
        "elapsed":      0.0,
        "name":         f"Test Job {job_id}",
        "job_template": template_id,
        "summary_fields": {
            "job_template": {"id": template_id, "name": f"Template {template_id}"},
            "launched_by":  {"username": "admin"},
        },
        "extra_vars":   json.dumps(extra_vars),
    }
    _jobs[job_id] = job
    return job


# ── AWX API ───────────────────────────────────────────────────────────────────

@app.get("/api/v2/ping/")
async def ping():
    return {"ha_enabled": False, "version": "24.6.1-stub", "active_node": "stub"}


@app.post("/api/v2/job_templates/{template_id}/launch/")
async def launch_job(template_id: int, request: Request):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    extra_vars = body.get("extra_vars", {})
    job = _new_job(template_id, extra_vars)
    _launches.append({
        "template_id": template_id,
        "extra_vars":  extra_vars,
        "job_id":      job["id"],
        "timestamp":   time.time(),
    })
    return JSONResponse(status_code=201, content=job)


@app.get("/api/v2/jobs/{job_id}/")
async def get_job(job_id: int):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/v2/jobs/{job_id}/stdout/")
async def get_stdout(job_id: int):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return Response(content=f"[stub] Output of job {job_id}\n", media_type="text/plain")


@app.get("/api/v2/jobs/")
async def list_jobs(page: int = 1, page_size: int = 25, status: str = ""):
    jobs = list(_jobs.values())
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    return {
        "count":    len(jobs),
        "next":     None,
        "previous": None,
        "results":  jobs[-page_size:],
    }


@app.get("/api/v2/job_templates/")
async def list_templates():
    template_ids = {j["job_template"] for j in _jobs.values()} or {1}
    return {
        "count": len(template_ids),
        "next":  None,
        "previous": None,
        "results": [
            {"id": tid, "name": f"Template {tid}", "type": "job_template"}
            for tid in sorted(template_ids)
        ],
    }


# ── Test control endpoints ────────────────────────────────────────────────────

@app.get("/_test/launches")
async def get_launches():
    return _launches


@app.get("/_test/jobs")
async def get_jobs():
    return list(_jobs.values())


@app.put("/_test/jobs/{job_id}/status")
async def set_job_status(job_id: int, status: str):
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    _jobs[job_id]["status"] = status
    if status in ("successful", "failed", "error", "canceled"):
        _jobs[job_id]["finished"] = "2026-04-26T12:05:00.000000Z"
        _jobs[job_id]["elapsed"] = 300.0
        _jobs[job_id]["failed"] = status not in ("successful",)
    return {"ok": True, "job_id": job_id, "status": status}


@app.delete("/_test/reset")
async def reset():
    _launches.clear()
    _jobs.clear()
    _next_id[0] = 1
    return {"ok": True}


# ── Standalone entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8052, log_level="warning")
