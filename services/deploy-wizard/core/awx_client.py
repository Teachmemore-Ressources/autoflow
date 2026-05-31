# Autoflow — core/awx_client.py — Apache 2.0
"""
Async AWX API client shared across all deploy-wizard routers.

Authentication: HTTP Basic (AWX_ADMIN_USER / AWX_ADMIN_PASSWORD).
All methods raise AWXError on non-2xx responses.

Usage
-----
    client = AWXClient.from_env()
    orgs   = await client.list_organizations()
    tpl_id = await client.ensure_verify_template()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

_log = logging.getLogger("autoflow.awx_client")

# ── Playbook written to /var/lib/awx/projects/autoflow-internal/ ─────────────

_VERIFY_PLAYBOOK = """\
---
# Autoflow — SSH connectivity verification
# Launched by the wizard to validate a Machine credential before marking it ACTIVE.
- name: Verify SSH connectivity
  hosts: all
  gather_facts: false
  tasks:
    - name: Ping host via SSH
      ansible.builtin.ping:
"""

_AWX_PROJECTS_DIR = Path(os.environ.get("AWX_PROJECTS_DIR", "/var/lib/awx/projects"))
_INTERNAL_PROJECT_NAME = "Autoflow Internal"
_INTERNAL_PROJECT_PATH = "autoflow-internal"
_VERIFY_TPL_NAME = "Autoflow — SSH Verify"


# ── Exceptions ────────────────────────────────────────────────────────────────


class AWXError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"AWX API error {status}: {detail}")


# ── Client ────────────────────────────────────────────────────────────────────


class AWXClient:
    """
    Thin async wrapper around the AWX REST API v2.

    Parameters
    ----------
    base_url  : AWX root URL, e.g. ``https://awx.example.com``
    username  : AWX admin username
    password  : AWX admin password
    verify_ssl: Whether to verify TLS certificates (False for self-signed)
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password)
        self._verify = verify_ssl
        # Cached IDs to avoid redundant API calls within a request
        self._verify_tpl_id: int | None = None

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "AWXClient":
        """Build a client from env / .env values (loaded by the wizard at startup)."""
        from core.env import _load_env

        cfg = _load_env()
        domain = cfg.get("DOMAIN", "localhost")
        user = cfg.get("AWX_ADMIN_USER", "admin")
        password = cfg.get("AWX_ADMIN_PASSWORD", "")
        base_url = cfg.get("AWX_BASE_URL", f"https://awx.{domain}")
        return cls(base_url=base_url, username=user, password=password)

    # ── Low-level HTTP helpers ────────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            auth=self._auth,
            verify=self._verify,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )

    async def _get(self, path: str, params: dict | None = None) -> dict:
        async with self._client() as c:
            r = await c.get(f"{self.base_url}{path}", params=params)
        if r.status_code != 200:
            raise AWXError(r.status_code, r.text[:300])
        return r.json()

    async def _post(self, path: str, body: dict) -> dict:
        async with self._client() as c:
            r = await c.post(f"{self.base_url}{path}", content=json.dumps(body))
        if r.status_code not in (200, 201, 202):
            raise AWXError(r.status_code, r.text[:300])
        return r.json() if r.content else {}

    async def _patch(self, path: str, body: dict) -> dict:
        async with self._client() as c:
            r = await c.patch(f"{self.base_url}{path}", content=json.dumps(body))
        if r.status_code not in (200, 204):
            raise AWXError(r.status_code, r.text[:300])
        return r.json() if r.content else {}

    async def _delete(self, path: str) -> None:
        async with self._client() as c:
            r = await c.delete(f"{self.base_url}{path}")
        if r.status_code not in (200, 202, 204):
            raise AWXError(r.status_code, r.text[:300])

    async def _paginated(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages of a list endpoint."""
        results: list[dict] = []
        url = path
        p = params or {}
        while url:
            data = await self._get(url if url.startswith("/") else path, params=p if url == path else None)
            results.extend(data.get("results", []))
            next_url = data.get("next")
            url = next_url.replace(self.base_url, "") if next_url else None
            p = {}
        return results

    # ── Ping ──────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Return True if AWX responds on /api/v2/ping/."""
        try:
            await self._get("/api/v2/ping/")
            return True
        except Exception:
            return False

    # ── Organizations ─────────────────────────────────────────────────────────

    async def list_organizations(self) -> list[dict]:
        """Return list of {id, name} for all accessible organizations."""
        items = await self._paginated("/api/v2/organizations/")
        return [{"id": o["id"], "name": o["name"]} for o in items]

    # ── Inventories ───────────────────────────────────────────────────────────

    async def list_inventories(self, org_id: int | None = None) -> list[dict]:
        params = {"organization": org_id} if org_id else {}
        items = await self._paginated("/api/v2/inventories/", params)
        return [
            {
                "id": i["id"],
                "name": i["name"],
                "kind": i.get("kind", ""),
                "org_id": i.get("organization"),
            }
            for i in items
        ]

    async def list_inventory_groups(self, inventory_id: int) -> list[dict]:
        items = await self._paginated(f"/api/v2/inventories/{inventory_id}/groups/")
        return [{"id": g["id"], "name": g["name"]} for g in items]

    async def list_inventory_hosts(self, inventory_id: int) -> list[dict]:
        items = await self._paginated(f"/api/v2/inventories/{inventory_id}/hosts/")
        return [{"id": h["id"], "name": h["name"]} for h in items]

    # ── Credentials ───────────────────────────────────────────────────────────

    async def list_credentials(
        self,
        org_id: int | None = None,
        name: str | None = None,
        kind: str = "ssh",
    ) -> list[dict]:
        params: dict[str, Any] = {"credential_type__kind": kind}
        if org_id:
            params["organization"] = org_id
        if name:
            params["name"] = name
        return await self._paginated("/api/v2/credentials/", params)

    async def get_credential(self, cred_id: int) -> dict:
        return await self._get(f"/api/v2/credentials/{cred_id}/")

    async def create_credential(
        self,
        org_id: int,
        name: str,
        username: str,
        private_key: str,
        description: str = "",
    ) -> dict:
        """
        Create a Machine (SSH) credential in AWX.

        Returns the full credential object including its ``id``.
        """
        # Resolve Machine credential type id (always 1 in standard AWX, but let's be safe)
        cred_type_id = await self._get_machine_cred_type_id()
        body = {
            "name": name,
            "description": description,
            "credential_type": cred_type_id,
            "organization": org_id,
            "inputs": {
                "username": username,
                "ssh_key_data": private_key,
            },
        }
        return await self._post("/api/v2/credentials/", body)

    async def update_credential_key(self, cred_id: int, private_key: str) -> None:
        """Replace the private key on an existing Machine credential."""
        await self._patch(
            f"/api/v2/credentials/{cred_id}/",
            {"inputs": {"ssh_key_data": private_key}},
        )

    async def update_credential_description(self, cred_id: int, description: str) -> None:
        """Update only the description field (used to persist metadata)."""
        await self._patch(
            f"/api/v2/credentials/{cred_id}/",
            {"description": description},
        )

    async def delete_credential(self, cred_id: int) -> None:
        await self._delete(f"/api/v2/credentials/{cred_id}/")

    async def _get_machine_cred_type_id(self) -> int:
        """Return the AWX id for the built-in Machine credential type."""
        data = await self._get("/api/v2/credential_types/", params={"kind": "ssh", "managed": True})
        results = data.get("results", [])
        if not results:
            raise AWXError(404, "Machine credential type not found in AWX")
        return results[0]["id"]

    # ── Jobs ──────────────────────────────────────────────────────────────────

    async def launch_job(
        self,
        template_id: int,
        inventory_id: int,
        credential_id: int,
        limit: str = "",
    ) -> int:
        """Launch a job template and return the new job id."""
        body: dict[str, Any] = {
            "inventory": inventory_id,
            "credentials": [credential_id],
        }
        if limit:
            body["limit"] = limit
        result = await self._post(f"/api/v2/job_templates/{template_id}/launch/", body)
        return result["id"]

    async def get_job(self, job_id: int) -> dict:
        return await self._get(f"/api/v2/jobs/{job_id}/")

    # ── Auto-provision verify job template ────────────────────────────────────

    async def ensure_verify_template(self) -> int:
        """
        Return the id of the "Autoflow — SSH Verify" job template,
        creating the project and template if they don't exist yet.

        The template id is cached in memory for the process lifetime
        and persisted to .env as AUTOFLOW_VERIFY_JOB_TPL_ID.
        """
        # 1. In-memory cache
        if self._verify_tpl_id:
            return self._verify_tpl_id

        # 2. Check .env cache
        from core.env import _load_env

        cfg = _load_env()
        cached = cfg.get("AUTOFLOW_VERIFY_JOB_TPL_ID", "").strip()
        if cached.isdigit():
            tpl_id = int(cached)
            # Quick sanity check
            try:
                await self._get(f"/api/v2/job_templates/{tpl_id}/")
                self._verify_tpl_id = tpl_id
                return tpl_id
            except AWXError:
                _log.warning("Cached AUTOFLOW_VERIFY_JOB_TPL_ID=%s no longer exists — recreating", tpl_id)

        # 3. Search AWX
        data = await self._get("/api/v2/job_templates/", params={"name": _VERIFY_TPL_NAME})
        results = data.get("results", [])
        if results:
            tpl_id = results[0]["id"]
            _log.info("Found existing verify template id=%s", tpl_id)
            self._verify_tpl_id = tpl_id
            self._cache_tpl_id(tpl_id, cfg)
            return tpl_id

        # 4. Create project + template
        _log.info("Creating Autoflow Internal project and SSH verify job template…")
        project_id = await self._ensure_verify_project()
        tpl_id = await self._create_verify_template(project_id)
        self._verify_tpl_id = tpl_id
        self._cache_tpl_id(tpl_id, cfg)
        _log.info("SSH verify template created: id=%s", tpl_id)
        return tpl_id

    async def _ensure_verify_project(self) -> int:
        """Ensure the 'Autoflow Internal' manual project exists in AWX."""
        # Check if already exists
        data = await self._get("/api/v2/projects/", params={"name": _INTERNAL_PROJECT_NAME})
        if data.get("results"):
            return data["results"][0]["id"]

        # Write playbook to the shared projects volume
        project_dir = _AWX_PROJECTS_DIR / _INTERNAL_PROJECT_PATH
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            (project_dir / "verify_ssh.yml").write_text(_VERIFY_PLAYBOOK)
            _log.info("Wrote verify_ssh.yml to %s", project_dir)
        except OSError as exc:
            raise AWXError(500, f"Cannot write to AWX projects dir {project_dir}: {exc}") from exc

        # Resolve a suitable organization (use the first available)
        orgs = await self.list_organizations()
        if not orgs:
            raise AWXError(500, "No organizations found in AWX")
        org_id = orgs[0]["id"]

        project = await self._post(
            "/api/v2/projects/",
            {
                "name": _INTERNAL_PROJECT_NAME,
                "description": json.dumps({"autoflow_managed": True}),
                "scm_type": "",  # manual
                "local_path": _INTERNAL_PROJECT_PATH,
                "organization": org_id,
            },
        )
        return project["id"]

    async def _create_verify_template(self, project_id: int) -> int:
        """Create the SSH verify job template."""
        tpl = await self._post(
            "/api/v2/job_templates/",
            {
                "name": _VERIFY_TPL_NAME,
                "description": json.dumps({"autoflow_managed": True}),
                "job_type": "run",
                "project": project_id,
                "playbook": "verify_ssh.yml",
                "ask_inventory_on_launch": True,
                "ask_credential_on_launch": True,
                "ask_limit_on_launch": True,
                "ask_variables_on_launch": False,
            },
        )
        return tpl["id"]

    @staticmethod
    def _cache_tpl_id(tpl_id: int, cfg: dict) -> None:
        """Persist the template id in .env to survive wizard restarts."""
        from core.env import _write_env

        cfg["AUTOFLOW_VERIFY_JOB_TPL_ID"] = str(tpl_id)
        try:
            _write_env(cfg)
        except Exception as exc:
            _log.warning("Could not cache AUTOFLOW_VERIFY_JOB_TPL_ID in .env: %s", exc)
