"""
Notification Callback Stub — captures POST payloads sent by the API's
job-completion notification system.

Endpoints:
  POST /callback         — record a notification payload
  GET  /_test/received   — return all recorded payloads (newest first)
  GET  /_test/count      — return the count of received notifications
  DELETE /_test/reset    — clear state

Usage as standalone server:
  python callback.py     — listens on 0.0.0.0:9999
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Callback Stub", version="1.0.0-stub")

_received: list[dict] = []


@app.post("/callback")
async def receive_callback(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {"raw": (await request.body()).decode(errors="replace")}

    _received.append(body)
    return JSONResponse(status_code=200, content={"ok": True})


@app.get("/_test/received")
async def get_received():
    return list(reversed(_received))


@app.get("/_test/count")
async def get_count():
    return {"count": len(_received)}


@app.delete("/_test/reset")
async def reset():
    _received.clear()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9999, log_level="warning")
