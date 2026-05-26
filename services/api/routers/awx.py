"""
AWX router — lightweight proxy to the most common AWX API endpoints.

All routes are protected by require_auth (accepts X-API-Key OR JWT Bearer).
When launching a job template, include optional notification fields in the body:
  callback_url      — URL to POST when the job completes
  notify_metadata   — arbitrary dict attached to the notification payload
"""

from __future__ import annotations

from typing import Any

import notifications
from fastapi import APIRouter, Depends, HTTPException, Request

from routers.auth import require_auth, require_write_access

router = APIRouter()


# ── Base helpers ──────────────────────────────────────────────────────────────


async def _awx_get(request: Request, path: str) -> Any:
    resp = await request.app.state.http.get(path)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


async def _awx_post(request: Request, path: str, body: dict) -> Any:
    resp = await request.app.state.http.post(path, json=body)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


# ── Job Templates ─────────────────────────────────────────────────────────────


@router.get("/job-templates", summary="List AWX job templates", dependencies=[Depends(require_auth)])
async def list_job_templates(request: Request):
    return await _awx_get(request, "/api/v2/job_templates/")


@router.get(
    "/job-templates/{template_id}", summary="Get a job template", dependencies=[Depends(require_auth)]
)
async def get_job_template(template_id: int, request: Request):
    return await _awx_get(request, f"/api/v2/job_templates/{template_id}/")


@router.post(
    "/job-templates/{template_id}/launch",
    summary="Launch a job template",
    dependencies=[Depends(require_write_access)],
)
async def launch_job_template(template_id: int, request: Request, body: dict = {}):
    """
    Launch an AWX job template.

    **Extra fields (stripped before forwarding to AWX):**
    - `callback_url`    — POST notification when job completes
    - `notify_metadata` — arbitrary dict attached to the notification payload

    Both fields are optional. When omitted, global notification settings
    (NOTIFICATION_WEBHOOK_URL / NOTIFICATION_SLACK_WEBHOOK) still apply.
    """
    # Extract notification hints before forwarding body to AWX
    callback_url = body.pop("callback_url", "")
    notify_metadata = body.pop("notify_metadata", {})

    result = await _awx_post(request, f"/api/v2/job_templates/{template_id}/launch/", body)

    # Auto-register the new job for completion notification
    job_id = result.get("id")
    if job_id:
        notifications.register(
            job_id=job_id,
            callback_url=callback_url,
            metadata=notify_metadata,
        )

    return result


# ── Jobs ──────────────────────────────────────────────────────────────────────


@router.get("/jobs", summary="List AWX jobs", dependencies=[Depends(require_auth)])
async def list_jobs(request: Request):
    return await _awx_get(request, "/api/v2/jobs/")


@router.get("/jobs/{job_id}", summary="Get job status", dependencies=[Depends(require_auth)])
async def get_job(job_id: int, request: Request):
    return await _awx_get(request, f"/api/v2/jobs/{job_id}/")


# ── Inventories ───────────────────────────────────────────────────────────────


@router.get("/inventories", summary="List inventories", dependencies=[Depends(require_auth)])
async def list_inventories(request: Request):
    return await _awx_get(request, "/api/v2/inventories/")


# ── Projects ──────────────────────────────────────────────────────────────────


@router.get("/projects", summary="List projects", dependencies=[Depends(require_auth)])
async def list_projects(request: Request):
    return await _awx_get(request, "/api/v2/projects/")
