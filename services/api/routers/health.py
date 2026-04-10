from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health", tags=["Health"], summary="Service liveness check")
async def health():
    return {"status": "ok", "service": "autoflow-api"}


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
