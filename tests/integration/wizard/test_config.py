"""
Integration tests — GET /api/config and POST /api/config.

Covered:
  - GET returns a dict of current config (schema defaults when no .env)
  - POST persists values to .env
  - POST auto-derives domain-based fields (GITEA_DOMAIN, GITEA_ROOT_URL, …)
  - POST emits first_start_only warning when FSO keys change
  - POST emits needs_recreate warning + flag when NEEDS_RECREATE keys change
  - POST emits domain_changed warning when DOMAIN changes
  - POST creates a timestamped backup when .env already exists
  - Backup file preserves the previous .env content
  - Response always contains status/changed/affected_services/needs_recreate/warnings
"""
from __future__ import annotations

from pathlib import Path

import pytest
from dotenv import dotenv_values


@pytest.mark.integration
async def test_get_config_returns_200(client, wizard_paths):
    resp = await client.get("/api/config")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_get_config_returns_dict(client, wizard_paths):
    body = (await client.get("/api/config")).json()
    assert isinstance(body, dict)
    assert len(body) >= 10


@pytest.mark.integration
async def test_get_config_has_schema_keys(client, wizard_paths):
    body = (await client.get("/api/config")).json()
    for key in ("DOMAIN", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
        assert key in body, f"Expected {key!r} in GET /api/config response"


@pytest.mark.integration
async def test_post_config_returns_200(client, wizard_paths):
    resp = await client.post("/api/config", json={"DOMAIN": "test.local"})
    assert resp.status_code == 200


@pytest.mark.integration
async def test_post_config_response_structure(client, wizard_paths):
    body = (await client.post("/api/config", json={"DOMAIN": "x.local"})).json()
    assert "status"            in body
    assert "changed"           in body
    assert "affected_services" in body
    assert "needs_recreate"    in body
    assert "warnings"          in body


@pytest.mark.integration
async def test_post_config_status_is_saved(client, wizard_paths):
    body = (await client.post("/api/config", json={"DOMAIN": "x.local"})).json()
    assert body["status"] == "saved"


@pytest.mark.integration
async def test_post_config_writes_env_file(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "write.local"})
    assert wizard_paths["env"].exists(), ".env file was not created by POST /api/config"


@pytest.mark.integration
async def test_post_config_env_file_permissions(client, wizard_paths):
    import stat
    await client.post("/api/config", json={"DOMAIN": "perm.local"})
    mode = wizard_paths["env"].stat().st_mode
    assert stat.S_IMODE(mode) == 0o600, ".env file should be mode 0600"


@pytest.mark.integration
async def test_post_config_derives_gitea_domain(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "acme.example.com"})
    env = dotenv_values(wizard_paths["env"])
    assert env.get("GITEA_DOMAIN") == "acme.example.com"


@pytest.mark.integration
async def test_post_config_derives_gitea_root_url(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "acme.example.com"})
    env = dotenv_values(wizard_paths["env"])
    assert env.get("GITEA_ROOT_URL") == "https://git.acme.example.com"


@pytest.mark.integration
async def test_post_config_derives_pki_base_url(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "acme.example.com"})
    env = dotenv_values(wizard_paths["env"])
    assert env.get("PKI_BASE_URL") == "https://pki.acme.example.com"


@pytest.mark.integration
async def test_post_config_derives_cors_origins(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "acme.example.com"})
    env = dotenv_values(wizard_paths["env"])
    cors = env.get("CORS_ORIGINS", "")
    assert "awx.acme.example.com" in cors
    assert "api.acme.example.com" in cors


@pytest.mark.integration
async def test_post_config_changed_list(client, wizard_paths):
    body = (await client.post("/api/config", json={"POSTGRES_PASSWORD": "secret123"})).json()
    assert "POSTGRES_PASSWORD" in body["changed"]


@pytest.mark.integration
async def test_post_config_affected_services_for_postgres(client, wizard_paths):
    body = (await client.post(
        "/api/config", json={"POSTGRES_PASSWORD": "new_pass"}
    )).json()
    services = body["affected_services"]
    assert "postgres" in services
    assert "awx"      in services


@pytest.mark.integration
async def test_post_config_affected_services_for_redis(client, wizard_paths):
    body = (await client.post(
        "/api/config", json={"REDIS_PASSWORD": "r3d1s_pass"}
    )).json()
    services = body["affected_services"]
    assert "redis" in services
    assert "awx"   in services


# ── First-start-only warnings ─────────────────────────────────────────────────

@pytest.mark.integration
async def test_post_config_fso_warning_when_awx_admin_password_changes(
    client, wizard_paths
):
    body = (await client.post(
        "/api/config", json={"AWX_ADMIN_PASSWORD": "new_awx_pass"}
    )).json()
    fso_warnings = [w for w in body["warnings"] if w["type"] == "first_start_only"]
    assert len(fso_warnings) == 1
    assert "AWX_ADMIN_PASSWORD" in fso_warnings[0]["message"]


@pytest.mark.integration
async def test_post_config_fso_warning_mentions_all_changed_fso_keys(
    client, wizard_paths
):
    body = (await client.post("/api/config", json={
        "AWX_ADMIN_PASSWORD": "p1",
        "GITEA_ADMIN_PASSWORD": "p2",
    })).json()
    fso_warnings = [w for w in body["warnings"] if w["type"] == "first_start_only"]
    assert len(fso_warnings) == 1
    msg = fso_warnings[0]["message"]
    assert "AWX_ADMIN_PASSWORD"   in msg
    assert "GITEA_ADMIN_PASSWORD" in msg


@pytest.mark.integration
async def test_post_config_no_fso_warning_for_non_fso_key(client, wizard_paths):
    body = (await client.post(
        "/api/config", json={"POSTGRES_PASSWORD": "safe_change"}
    )).json()
    fso_warnings = [w for w in body["warnings"] if w["type"] == "first_start_only"]
    assert len(fso_warnings) == 0


# ── needs_recreate warnings ───────────────────────────────────────────────────

@pytest.mark.integration
async def test_post_config_needs_recreate_true_for_docker_gid(client, wizard_paths):
    body = (await client.post("/api/config", json={"DOCKER_GID": "999"})).json()
    assert body["needs_recreate"] is True


@pytest.mark.integration
async def test_post_config_needs_recreate_warning_present(client, wizard_paths):
    body = (await client.post("/api/config", json={"DOCKER_GID": "999"})).json()
    recreate_warnings = [w for w in body["warnings"] if w["type"] == "needs_recreate"]
    assert len(recreate_warnings) == 1


@pytest.mark.integration
async def test_post_config_needs_recreate_false_for_normal_key(client, wizard_paths):
    body = (await client.post("/api/config", json={"LOG_LEVEL": "debug"})).json()
    assert body["needs_recreate"] is False


@pytest.mark.integration
async def test_post_config_needs_recreate_for_traefik_http_port(client, wizard_paths):
    body = (await client.post(
        "/api/config", json={"TRAEFIK_HTTP_PORT": "8080"}
    )).json()
    assert body["needs_recreate"] is True


# ── domain_changed warning ────────────────────────────────────────────────────

@pytest.mark.integration
async def test_post_config_domain_changed_warning(client, wizard_paths):
    body = (await client.post("/api/config", json={"DOMAIN": "new.example.com"})).json()
    domain_warnings = [w for w in body["warnings"] if w["type"] == "domain_changed"]
    assert len(domain_warnings) == 1


# ── Backup behaviour ──────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_post_config_no_backup_on_first_save(client, wizard_paths):
    body = (await client.post("/api/config", json={"DOMAIN": "first.local"})).json()
    assert body["backup"] is None, "First save should not create a backup"


@pytest.mark.integration
async def test_post_config_creates_backup_on_second_save(client, wizard_paths):
    # First save — establishes .env
    await client.post("/api/config", json={"DOMAIN": "orig.local"})
    assert wizard_paths["env"].exists()
    # Second save — should create a backup
    body = (await client.post("/api/config", json={"DOMAIN": "updated.local"})).json()
    assert body["backup"] is not None, "Second save should create a backup"


@pytest.mark.integration
async def test_post_config_backup_file_exists(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "v1.local"})
    body = (await client.post("/api/config", json={"DOMAIN": "v2.local"})).json()
    backup_path = Path(body["backup"])
    assert backup_path.exists(), f"Backup file {backup_path} does not exist"


@pytest.mark.integration
async def test_post_config_backup_preserves_previous_content(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "original.local"})
    body = (await client.post("/api/config", json={"DOMAIN": "new.local"})).json()
    backup_content = Path(body["backup"]).read_text()
    assert "original.local" in backup_content


@pytest.mark.integration
async def test_post_config_backup_is_in_same_dir_as_env(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "a.local"})
    body = (await client.post("/api/config", json={"DOMAIN": "b.local"})).json()
    backup = Path(body["backup"])
    assert backup.parent == wizard_paths["env"].parent


# ── GET /api/config reflects saved values ────────────────────────────────────

@pytest.mark.integration
async def test_get_config_reflects_saved_domain(client, wizard_paths):
    await client.post("/api/config", json={"DOMAIN": "reflect.local"})
    config = (await client.get("/api/config")).json()
    assert config.get("DOMAIN") == "reflect.local"
