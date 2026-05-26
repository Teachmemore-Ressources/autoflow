import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()

# Recorded at module import – good enough for a relative uptime indicator.
_START_TIME = time.time()


@router.get("/health", tags=["Health"], summary="Service liveness check")
async def health():
    return {
        "status": "ok",
        "service": "autoflow-api",
        "uptime_seconds": round(time.time() - _START_TIME, 1),
    }


@router.get("/health/ready", tags=["Health"], summary="Readiness check – verifies all dependencies")
async def health_ready(request: Request):
    checks: dict[str, dict] = {}
    overall = "ok"

    # AWX connectivity
    try:
        resp = await request.app.state.http.get("/api/v2/ping/", timeout=5.0)
        resp.raise_for_status()
        checks["awx"] = {"status": "ok"}
    except Exception as exc:
        checks["awx"] = {"status": "degraded", "detail": str(exc)}
        overall = "degraded"

    status_code = 200 if overall == "ok" else 503
    return JSONResponse(
        status_code=status_code,
        content={"status": overall, "checks": checks},
    )


@router.get("/health/awx", tags=["Health"], summary="AWX connectivity check")
async def health_awx(request: Request):
    try:
        resp = await request.app.state.http.get("/api/v2/ping/")
        resp.raise_for_status()
        return {"status": "ok", "awx": resp.json()}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "detail": str(exc)},
        )
