"""
AWX router – lightweight proxy to the most common AWX API endpoints.
Extend as needed; does not modify AWX source code.
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


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


# ── Job Templates ────────────────────────────────────────────────────────────

@router.get("/job-templates", summary="List AWX job templates")
async def list_job_templates(request: Request):
    return await _awx_get(request, "/api/v2/job_templates/")


@router.get("/job-templates/{template_id}", summary="Get a job template")
async def get_job_template(template_id: int, request: Request):
    return await _awx_get(request, f"/api/v2/job_templates/{template_id}/")


@router.post("/job-templates/{template_id}/launch", summary="Launch a job template")
async def launch_job_template(template_id: int, request: Request, body: dict = {}):
    return await _awx_post(request, f"/api/v2/job_templates/{template_id}/launch/", body)


# ── Jobs ─────────────────────────────────────────────────────────────────────

@router.get("/jobs", summary="List AWX jobs")
async def list_jobs(request: Request):
    return await _awx_get(request, "/api/v2/jobs/")


@router.get("/jobs/{job_id}", summary="Get job status")
async def get_job(job_id: int, request: Request):
    return await _awx_get(request, f"/api/v2/jobs/{job_id}/")


# ── Inventories ───────────────────────────────────────────────────────────────

@router.get("/inventories", summary="List inventories")
async def list_inventories(request: Request):
    return await _awx_get(request, "/api/v2/inventories/")


# ── Projects ──────────────────────────────────────────────────────────────────

@router.get("/projects", summary="List projects")
async def list_projects(request: Request):
    return await _awx_get(request, "/api/v2/projects/")
