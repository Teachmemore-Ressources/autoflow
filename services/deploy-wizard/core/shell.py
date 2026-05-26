"""
core/shell.py — SSE helper, sudo wrappers, PKI constants and Gitea URL helper.
"""
from __future__ import annotations

import asyncio
import subprocess

from core.env import ROOT, _load_env

# ── PKI constants ─────────────────────────────────────────────────────────────

PKI_URL             = "http://localhost:8004"
PKI_CA_NAME         = "autoflow-root"
WIZARD_PKI_OVERRIDE = ROOT / "docker-compose.wizard-pki.yml"


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(msg: str) -> str:
    return f"data: {msg}\n\n"


# ── Sudo helpers ──────────────────────────────────────────────────────────────

def _sudo_password() -> str:
    return _load_env().get("SUDO_PASSWORD", "")


def _sudo_run(cmd: list, *, password: str | None = None, **kwargs) -> subprocess.CompletedProcess:
    """Run cmd with sudo, injecting password via stdin when available."""
    pw = password if password is not None else _sudo_password()
    if pw:
        return subprocess.run(
            ["sudo", "-S", "--"] + cmd,
            input=pw + "\n",
            **kwargs,
        )
    return subprocess.run(["sudo"] + cmd, **kwargs)


async def _async_sudo_exec(cmd: list, *, password: str | None = None, **kwargs):
    """Async version — returns (proc, stdout_pipe) using create_subprocess_exec."""
    pw = password if password is not None else _sudo_password()
    if pw:
        proc = await asyncio.create_subprocess_exec(
            "sudo", "-S", "--", *cmd,
            stdin=asyncio.subprocess.PIPE,
            **kwargs,
        )
        proc.stdin.write((pw + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()
    else:
        proc = await asyncio.create_subprocess_exec("sudo", *cmd, **kwargs)
    return proc


# ── Gitea API URL ─────────────────────────────────────────────────────────────

def _gitea_api_url() -> str:
    """Return the Gitea API base URL reachable from the host.

    Gitea has no direct host port binding — it is accessible only via Traefik
    at https://git.<DOMAIN>.  We use verify=False (or the local CA) because
    the CA may not yet be in the system trust store when this is called.
    """
    cfg = _load_env()
    domain = cfg.get("DOMAIN", "localhost")
    return f"https://git.{domain}/api/v1"
