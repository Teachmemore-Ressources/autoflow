"""
Job History & Statistics
========================
Enriched AWX job history with pagination, filtering and aggregate stats.

Endpoints
---------
GET  /awx/jobs/history           – Paginated job list with enriched fields
GET  /awx/jobs/stats             – Aggregate counts and success rate
POST /awx/jobs/{id}/watch        – Register a job for completion notification
"""

from __future__ import annotations

import notifications as notif
from fastapi import APIRouter, Depends, HTTPException, Request

from routers.auth import require_auth, require_write_access

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _human_dur(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = float(seconds)
    if s < 60:
        return f"{s:.0f}s"
    m, sec = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {sec}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


async def _get(request: Request, path: str) -> dict:
    r = await request.app.state.http.get(path)
    if r.status_code >= 400:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()


# ── History ───────────────────────────────────────────────────────────────────


@router.get(
    "/awx/jobs/history",
    tags=["Jobs"],
    summary="Enriched job history",
    dependencies=[Depends(require_auth)],
)
async def job_history(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    template: str | None = None,
    job_type: str | None = None,
):
    """
    Returns AWX job list enriched with:
    - Human-readable duration
    - Template name (from summary_fields)
    - launched_by username

    **Filter parameters:**
    - `status`: successful | failed | error | canceled | running | pending
    - `template`: job template name (exact match)
    - `job_type`: run | check | scan
    """
    qs = f"page={page}&page_size={page_size}&order_by=-id"
    if status:
        qs += f"&status={status}"
    if template:
        qs += f"&job_template__name={template}"
    if job_type:
        qs += f"&job_type={job_type}"

    data = await _get(request, f"/api/v2/jobs/?{qs}")
    sf_key = "summary_fields"

    results = [
        {
            "id": j.get("id"),
            "name": j.get("name"),
            "status": j.get("status"),
            "job_type": j.get("job_type"),
            "started": j.get("started"),
            "finished": j.get("finished"),
            "elapsed_seconds": j.get("elapsed"),
            "elapsed_human": _human_dur(j.get("elapsed")),
            "failed": j.get("failed"),
            "job_template": j.get(sf_key, {}).get("job_template", {}).get("name"),
            "launched_by": j.get(sf_key, {}).get("created_by", {}).get("username"),
            "execution_node": j.get("execution_node"),
        }
        for j in data.get("results", [])
    ]

    return {
        "count": data.get("count", 0),
        "page": page,
        "page_size": page_size,
        "next": data.get("next"),
        "previous": data.get("previous"),
        "results": results,
    }


# ── Stats ─────────────────────────────────────────────────────────────────────


@router.get(
    "/awx/jobs/stats",
    tags=["Jobs"],
    summary="Job statistics by status",
    dependencies=[Depends(require_auth)],
)
async def job_stats(request: Request):
    """
    Returns aggregated job counts and success rate across all templates.
    Also reports how many jobs are currently being watched for notifications.
    """
    statuses = (
        "successful",
        "failed",
        "error",
        "canceled",
        "running",
        "pending",
        "waiting",
        "new",
    )
    by_status: dict[str, int] = {}
    for s in statuses:
        r = await request.app.state.http.get(f"/api/v2/jobs/?status={s}&page_size=1")
        if r.status_code == 200:
            by_status[s] = r.json().get("count", 0)

    terminal = sum(by_status.get(s, 0) for s in ("successful", "failed", "error", "canceled"))
    success_rate = round(by_status.get("successful", 0) / max(terminal, 1) * 100, 1)

    return {
        "total_terminal_jobs": terminal,
        "success_rate_percent": success_rate,
        "by_status": by_status,
        "watched_jobs": notif.watched_count(),
    }


# ── Watch ─────────────────────────────────────────────────────────────────────


@router.post(
    "/awx/jobs/{job_id}/watch",
    tags=["Jobs"],
    summary="Watch a job for completion notification",
    dependencies=[Depends(require_write_access)],
)
async def watch_job(job_id: int, body: dict = {}):
    """
    Register an already-launched job to receive a completion notification.

    For auto-watch at launch time, include `callback_url` and `notify_metadata`
    directly in the body of `POST /awx/job-templates/{id}/launch`.

    **Body (all optional):**
    ```json
    {
        "callback_url": "https://my-system/hooks/job-done",
        "metadata": {"env": "prod", "triggered_by": "ci"}
    }
    ```
    """
    notif.register(
        job_id=job_id,
        callback_url=body.get("callback_url", ""),
        metadata=body.get("metadata", {}),
    )
    return {"status": "watching", "job_id": job_id}
