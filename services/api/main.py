"""
Autoflow API
============
Thin FastAPI service that proxies and augments AWX operations.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import awx, health
from settings import settings


# ── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Shared async HTTP client – reused across all requests
    app.state.http = httpx.AsyncClient(
        base_url=settings.awx_url,
        auth=(settings.awx_admin_user, settings.awx_admin_password),
        timeout=30.0,
        verify=False,  # internal network; adjust if TLS is enabled on AWX
    )
    yield
    await app.state.http.aclose()


# ── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Autoflow API",
    description="Automation platform API powered by AWX",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(awx.router, prefix="/awx", tags=["AWX"])
