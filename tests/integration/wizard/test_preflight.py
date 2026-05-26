"""
Integration tests — /api/system/preflight endpoint.

Validates:
  - Response structure (checks list, all_ok boolean)
  - Each check has the required keys (id, label, ok, detail)
  - All five expected check IDs are present
  - all_ok is consistent with individual check results
  - The endpoint is callable synchronously (no SSE, returns JSON immediately)
"""

from __future__ import annotations

import pytest

EXPECTED_CHECK_IDS = {"docker", "compose", "ram", "disk", "dns"}


@pytest.mark.integration
async def test_preflight_returns_200(client):
    resp = await client.get("/api/system/preflight")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_preflight_response_has_checks_and_all_ok(client):
    body = (await client.get("/api/system/preflight")).json()
    assert "checks" in body, "Response missing 'checks' key"
    assert "all_ok" in body, "Response missing 'all_ok' key"


@pytest.mark.integration
async def test_preflight_checks_is_nonempty_list(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    assert isinstance(checks, list)
    assert len(checks) > 0


@pytest.mark.integration
async def test_preflight_all_ok_is_bool(client):
    all_ok = (await client.get("/api/system/preflight")).json()["all_ok"]
    assert isinstance(all_ok, bool)


@pytest.mark.integration
async def test_preflight_each_check_has_required_keys(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    required = {"id", "label", "ok", "detail"}
    for check in checks:
        missing = required - check.keys()
        assert not missing, f"Check {check.get('id', '?')!r} missing keys: {missing}"


@pytest.mark.integration
async def test_preflight_check_ok_is_bool(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    for check in checks:
        assert isinstance(check["ok"], bool), (
            f"check[{check['id']!r}]['ok'] should be bool, got {type(check['ok'])}"
        )


@pytest.mark.integration
async def test_preflight_check_detail_is_string(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    for check in checks:
        assert isinstance(check["detail"], str), f"check[{check['id']!r}]['detail'] should be str"


@pytest.mark.integration
async def test_preflight_known_check_ids_present(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    ids = {c["id"] for c in checks}
    missing = EXPECTED_CHECK_IDS - ids
    assert not missing, f"Expected check IDs not found: {missing}"


@pytest.mark.integration
async def test_preflight_all_ok_consistent_with_checks(client):
    body = (await client.get("/api/system/preflight")).json()
    computed = all(c["ok"] for c in body["checks"])
    assert body["all_ok"] == computed, f"all_ok={body['all_ok']} but individual checks compute to {computed}"


@pytest.mark.integration
async def test_preflight_docker_check_present(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    docker = next((c for c in checks if c["id"] == "docker"), None)
    assert docker is not None
    assert "Docker" in docker["label"]


@pytest.mark.integration
async def test_preflight_ram_check_mentions_gb(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    ram = next((c for c in checks if c["id"] == "ram"), None)
    assert ram is not None
    # Detail should mention GB / memory quantity
    assert "GB" in ram["detail"] or "meminfo" in ram["detail"]


@pytest.mark.integration
async def test_preflight_disk_check_mentions_gb(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    disk = next((c for c in checks if c["id"] == "disk"), None)
    assert disk is not None
    assert "GB" in disk["detail"]


@pytest.mark.integration
async def test_preflight_dns_check_mentions_github(client):
    checks = (await client.get("/api/system/preflight")).json()["checks"]
    dns = next((c for c in checks if c["id"] == "dns"), None)
    assert dns is not None
    assert "github.com" in dns["detail"]
