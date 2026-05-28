"""
Autoflow Deploy Wizard — Backend
"""

from __future__ import annotations

# Core modules — importing core.auth triggers the WIZARD_TOKEN sys.exit guard
from core.auth import (  # noqa: F401
    _audit,
    _http_basic,
    _require_auth,  # noqa: F401 (guard runs on import)
)
from core.env import (
    AUDIT_LOG,  # noqa: F401 — re-exported for test conftest monkeypatching
    ENV_EXAMPLE_FILE,  # noqa: F401 — re-exported for test conftest monkeypatching
    ENV_FILE,  # noqa: F401 — re-exported for test conftest monkeypatching
    GITEA_BEARER_TOKEN_FILE,  # noqa: F401 — re-exported for test conftest monkeypatching
    MONITORING_USERS,  # noqa: F401 — re-exported for test conftest monkeypatching
    STATIC_DIR,
    WIZARD_VERSION,  # noqa: F401 — re-exported for test access via wizard_main
    _load_env,  # noqa: F401 — re-exported for test access via wizard_main
    _quote_env_value,  # noqa: F401 — re-exported for test access via wizard_main
    _write_env,  # noqa: F401 — re-exported for test access via wizard_main
)
from core.shell import (  # noqa: F401
    PKI_CA_NAME,
    PKI_URL,
    WIZARD_PKI_OVERRIDE,
    _async_sudo_exec,
    _gitea_api_url,
    _sse,
    _sudo_password,
    _sudo_run,
)
from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from routers.backup import router as backup_router
from routers.cleanup import router as cleanup_router
from routers.compliance import router as compliance_router
from routers.config import (  # noqa: F401
    FIRST_START_ONLY,
    KEY_TO_SERVICES,
    NEEDS_RECREATE,
)

# Routers
from routers.config import router as config_router
from routers.deploy import router as deploy_router
from routers.ee import router as ee_router
from routers.preflight import router as preflight_router
from routers.setup import router as setup_router
from routers.ssh import router as ssh_router
from routers.tls import router as tls_router

app = FastAPI(
    title="Autoflow Deploy Wizard",
    docs_url=None,
    redoc_url=None,
    dependencies=[Depends(_require_auth)],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=None)
async def root():
    from fastapi.responses import HTMLResponse

    return HTMLResponse((STATIC_DIR / "index.html").read_text())


# Include all routers
for _r in (
    config_router,
    deploy_router,
    tls_router,
    ssh_router,
    ee_router,
    setup_router,
    preflight_router,
    backup_router,
    compliance_router,
    cleanup_router,
):
    app.include_router(_r)
