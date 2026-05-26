"""
core/auth.py — HTTP Basic Auth guard and audit-log helper for the Deploy Wizard.

Importing this module triggers the WIZARD_TOKEN sys.exit guard immediately.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from core.env import AUDIT_LOG

# ── WIZARD_TOKEN guard ────────────────────────────────────────────────────────

_WIZARD_TOKEN = os.environ.get("WIZARD_TOKEN", "").strip()
if not _WIZARD_TOKEN:
    print(
        "\n  ERROR: WIZARD_TOKEN environment variable is not set.\n"
        "  Generate a token and export it before starting the wizard:\n\n"
        '    export WIZARD_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")\n'
        "    make wizard\n",
        file=sys.stderr,
    )
    sys.exit(1)

# ── HTTP Basic Auth ───────────────────────────────────────────────────────────

_http_basic = HTTPBasic(realm="Autoflow Deploy Wizard")


def _require_auth(creds: HTTPBasicCredentials = Depends(_http_basic)) -> str:
    """HTTP Basic Auth — username ignored, password must match WIZARD_TOKEN."""
    ok = secrets.compare_digest(creds.password.encode(), _WIZARD_TOKEN.encode())
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": 'Basic realm="Autoflow Deploy Wizard"'},
        )
    return creds.username


# ── Audit log ─────────────────────────────────────────────────────────────────


def _audit(request: Request, action: str, **extra) -> None:
    """Append a JSON line to wizard-audit.log — values are never logged."""
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ip": request.client.host if request.client else "unknown",
        "action": action,
        **extra,
    }
    with AUDIT_LOG.open("a") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
