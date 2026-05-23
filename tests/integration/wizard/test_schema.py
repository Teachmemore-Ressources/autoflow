"""
Integration tests — /api/schema and /api/version endpoints.

Validates:
  - Version endpoint returns a semver string
  - Schema response structure (sections, fields, first_start_only)
  - All FSO keys exist in the fields list
  - Section and field required attributes are present
  - Auth is enforced (wrong token → 401)
"""
from __future__ import annotations

import re

import pytest


@pytest.mark.integration
async def test_version_returns_200(client):
    resp = await client.get("/api/version")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_version_is_semver(client):
    body = (await client.get("/api/version")).json()
    assert "version" in body
    assert re.match(r"^\d+\.\d+\.\d+$", body["version"]), (
        f"version {body['version']!r} is not semver"
    )


@pytest.mark.integration
async def test_schema_returns_200(client):
    resp = await client.get("/api/schema")
    assert resp.status_code == 200


@pytest.mark.integration
async def test_schema_has_required_top_level_keys(client):
    body = (await client.get("/api/schema")).json()
    assert "sections"        in body
    assert "fields"          in body
    assert "first_start_only" in body


@pytest.mark.integration
async def test_schema_sections_are_list(client):
    body = (await client.get("/api/schema")).json()
    assert isinstance(body["sections"], list)
    assert len(body["sections"]) > 0


@pytest.mark.integration
async def test_schema_fields_are_list(client):
    body = (await client.get("/api/schema")).json()
    assert isinstance(body["fields"], list)
    assert len(body["fields"]) > 0


@pytest.mark.integration
async def test_schema_first_start_only_is_list_of_strings(client):
    body = (await client.get("/api/schema")).json()
    fso = body["first_start_only"]
    assert isinstance(fso, list)
    assert all(isinstance(k, str) for k in fso), "first_start_only must contain strings"


@pytest.mark.integration
async def test_schema_sections_have_id_and_label(client):
    sections = (await client.get("/api/schema")).json()["sections"]
    for s in sections:
        assert "id"    in s, f"Section missing 'id': {s}"
        assert "label" in s, f"Section missing 'label': {s}"


@pytest.mark.integration
async def test_schema_fields_have_key(client):
    fields = (await client.get("/api/schema")).json()["fields"]
    for f in fields:
        assert "key" in f, f"Field missing 'key': {f}"


@pytest.mark.integration
async def test_schema_fields_have_section(client):
    fields = (await client.get("/api/schema")).json()["fields"]
    for f in fields:
        assert "section" in f, f"Field missing 'section': {f}"


@pytest.mark.integration
async def test_schema_all_fso_keys_exist_in_fields(client):
    """Every key in first_start_only must have a corresponding field definition."""
    body      = (await client.get("/api/schema")).json()
    field_keys = {f["key"] for f in body["fields"]}
    for fso_key in body["first_start_only"]:
        assert fso_key in field_keys, (
            f"first_start_only key {fso_key!r} has no matching field definition"
        )


@pytest.mark.integration
async def test_schema_known_section_ids_present(client):
    sections = (await client.get("/api/schema")).json()["sections"]
    ids = {s["id"] for s in sections}
    expected = {"infrastructure", "awx", "postgresql", "gitea", "monitoring"}
    assert expected.issubset(ids), f"Missing section ids: {expected - ids}"


@pytest.mark.integration
async def test_schema_known_field_keys_present(client):
    fields = (await client.get("/api/schema")).json()["fields"]
    keys = {f["key"] for f in fields}
    expected = {"DOMAIN", "POSTGRES_PASSWORD", "REDIS_PASSWORD",
                "AWX_ADMIN_PASSWORD", "GITEA_ADMIN_PASSWORD"}
    assert expected.issubset(keys), f"Missing field keys: {expected - keys}"


@pytest.mark.integration
async def test_unauthenticated_request_returns_401(wizard_app):
    """Requests without Basic auth must be rejected."""
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=wizard_app),
        base_url="http://testserver",
    ) as anon_client:
        resp = await anon_client.get("/api/schema")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_wrong_token_returns_401(wizard_app):
    """Wrong password in Basic auth must return 401."""
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=wizard_app),
        base_url="http://testserver",
        auth=("user", "wrong-password-xyz"),
    ) as bad_client:
        resp = await bad_client.get("/api/version")
    assert resp.status_code == 401
