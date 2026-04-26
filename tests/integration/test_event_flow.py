"""
Integration tests: event → Event Engine dispatch pipeline → AWX job.

Tests call ``_dispatch_core()`` directly, bypassing the HTTP layer.
This exercises the full pipeline (dedup → rule resolution → AWX launch)
without the Python-3.10 ``from __future__ import annotations`` +
``@limiter.limit()`` interaction that prevents FastAPI from resolving
forward-referenced type hints in the wrapped endpoint functions.

All imports from the event-engine are done at module level here (safe,
because this file is always loaded before api modules change sys.path).
The ``ee_client`` fixture (for the single HTTP test) comes from
``tests/integration/conftest.py``.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from httpx import ASGITransport, AsyncClient

# ── Ensure event-engine is on sys.path before any service import ──────────────

_EE    = Path(__file__).parent.parent.parent / "services" / "event-engine"
_STUBS = Path(__file__).parent.parent / "stubs"

for _p in (_EE, _STUBS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ── Event-engine module imports (canonical names) ─────────────────────────────

from awx_client import AWXClient           # noqa: E402
from dedup import DedupStore               # noqa: E402
from parsers import parse_alertmanager, parse_github  # noqa: E402
from rules import RuleEngine               # noqa: E402
from settings import settings              # noqa: E402

# ── AWX stub ──────────────────────────────────────────────────────────────────

from awx import app as _awx_stub_app       # noqa: E402  (tests/stubs/awx.py)


@pytest.fixture
async def awx_stub():
    """AWX stub — state reset before each test."""
    async with AsyncClient(
        transport=ASGITransport(app=_awx_stub_app),
        base_url="http://awx-stub",
    ) as client:
        await client.delete("/_test/reset")
        yield client


@pytest.fixture
async def components():
    """Core dispatch components for direct function-level tests."""
    awx_http = AsyncClient(
        transport=ASGITransport(app=_awx_stub_app),
        base_url="http://awx-stub",
        headers={"Authorization": "Bearer test-awx-token"},
        timeout=30.0,
    )
    awx   = AWXClient(awx_http, settings.awx_job_template_id)
    rules = RuleEngine(settings.rules_file or None, settings.awx_job_template_id)
    dedup = DedupStore(ttl_seconds=settings.dedup_ttl)

    yield {"awx": awx, "rules": rules, "dedup": dedup}

    await awx_http.aclose()


# ── Direct dispatch tests ─────────────────────────────────────────────────────

@pytest.mark.integration
async def test_dispatch_core_launches_awx_job(components, awx_stub):
    """_dispatch_core() with a fresh event launches an AWX job."""
    from main import _dispatch_core

    result = await _dispatch_core(
        components["dedup"],
        components["rules"],
        components["awx"],
        source="ci",
        action="deploy",
        data={"env": "prod"},
    )
    assert result["status"] == "accepted"
    assert result["source"] == "ci"
    assert result["action"] == "deploy"
    assert result["job_id"] is not None

    launches = (await awx_stub.get("/_test/launches")).json()
    assert len(launches) == 1
    assert launches[0]["template_id"] == settings.awx_job_template_id


@pytest.mark.integration
async def test_dispatch_core_returns_job_id_and_template(components, awx_stub):
    """Result dict contains job_id and template_id."""
    from main import _dispatch_core

    result = await _dispatch_core(
        components["dedup"],
        components["rules"],
        components["awx"],
        source="ci",
        action="build",
        data={"sha": "abc123"},
    )
    assert "job_id" in result
    assert "template_id" in result


@pytest.mark.integration
async def test_dispatch_core_extra_vars_propagated(components, awx_stub):
    """Extra vars sent to AWX contain event metadata."""
    from main import _dispatch_core

    await _dispatch_core(
        components["dedup"],
        components["rules"],
        components["awx"],
        source="github",
        action="push",
        data={"ref": "refs/heads/main", "sha": "deadbeef"},
    )
    launches = (await awx_stub.get("/_test/launches")).json()
    ev = launches[-1]["extra_vars"]
    assert ev["event_source"] == "github"
    assert ev["event_action"] == "push"


@pytest.mark.integration
async def test_dispatch_core_dedup_suppresses_second_call(components, awx_stub):
    """Identical events within the TTL window are deduplicated."""
    from main import _dispatch_core

    data = {"sha": "unique-for-dedup-test"}
    r1 = await _dispatch_core(
        components["dedup"], components["rules"], components["awx"],
        "ci", "dedup-test", data,
    )
    r2 = await _dispatch_core(
        components["dedup"], components["rules"], components["awx"],
        "ci", "dedup-test", data,
    )
    assert r1["status"] == "accepted"
    assert r2["status"] == "deduplicated"

    launches = (await awx_stub.get("/_test/launches")).json()
    dedup_launches = [
        l for l in launches
        if l["extra_vars"].get("event_action") == "dedup-test"
    ]
    assert len(dedup_launches) == 1


@pytest.mark.integration
async def test_dispatch_core_different_data_not_deduplicated(components, awx_stub):
    """Events with different data fields are never considered duplicates."""
    from main import _dispatch_core

    for sha in ("sha-111", "sha-222"):
        await _dispatch_core(
            components["dedup"], components["rules"], components["awx"],
            "ci", "build", {"sha": sha},
        )
    launches = (await awx_stub.get("/_test/launches")).json()
    assert len(launches) == 2


@pytest.mark.integration
async def test_dispatch_core_event_id_injected_into_extra_vars(components, awx_stub):
    """When an event_id is provided it is included in extra_vars."""
    from main import _dispatch_core

    await _dispatch_core(
        components["dedup"], components["rules"], components["awx"],
        "ci", "release", {"tag": "v1.0"},
        event_id="evt-abc-123",
    )
    launches = (await awx_stub.get("/_test/launches")).json()
    assert launches[-1]["extra_vars"]["_event_id"] == "evt-abc-123"


# ── GitHub webhook parsing + dispatch ─────────────────────────────────────────

@pytest.mark.integration
async def test_github_push_parse_and_dispatch(components, awx_stub):
    """parse_github push + _dispatch_core reaches AWX with correct vars."""
    from main import _dispatch_core

    payload = {
        "ref":         "refs/heads/main",
        "after":       "cafebabe",
        "before":      "00000000",
        "repository":  {"full_name": "acme/app", "name": "app",
                         "html_url": "https://github.com/acme/app"},
        "sender":      {"login": "alice"},
        "head_commit": {"id": "cafebabe", "message": "fix: prod",
                         "author": {"name": "Alice"}},
    }
    source, action, data = parse_github("push", payload)
    result = await _dispatch_core(
        components["dedup"], components["rules"], components["awx"],
        source, action, data,
    )
    assert result["status"] == "accepted"
    launches = (await awx_stub.get("/_test/launches")).json()
    ev = launches[-1]["extra_vars"]
    assert ev["event_source"] == "github"
    assert ev["event_action"] == "push"


@pytest.mark.integration
async def test_github_pr_opened_parse_and_dispatch(components, awx_stub):
    """parse_github pull_request + _dispatch_core dispatches correctly."""
    from main import _dispatch_core

    payload = {
        "action":       "opened",
        "pull_request": {
            "number": 12, "title": "feat", "merged": False,
            "base":   {"ref": "main"}, "head": {"ref": "feat/x"},
        },
        "repository": {"full_name": "acme/app"},
        "sender":     {"login": "bob"},
    }
    source, action, data = parse_github("pull_request", payload)
    result = await _dispatch_core(
        components["dedup"], components["rules"], components["awx"],
        source, action, data,
    )
    assert result["status"] == "accepted"
    assert result["action"] == "opened"


# ── Alertmanager webhook parsing + dispatch ───────────────────────────────────

@pytest.mark.integration
async def test_alertmanager_firing_parse_and_dispatch(components, awx_stub):
    """parse_alertmanager firing + _dispatch_core dispatches correctly."""
    from main import _dispatch_core

    payload = {
        "status":      "firing",
        "receiver":    "autoflow-webhook",
        "externalURL": "https://alertmanager.example.com",
        "groupLabels": {"alertname": "HighMemory"},
        "alerts": [{
            "labels":      {"alertname": "HighMemory", "severity": "critical",
                              "instance": "db-01"},
            "annotations": {"summary": "Memory above 95%"},
            "startsAt":    "2026-04-26T10:00:00Z",
            "endsAt":      "0001-01-01T00:00:00Z",
        }],
    }
    source, action, data = parse_alertmanager(payload)
    result = await _dispatch_core(
        components["dedup"], components["rules"], components["awx"],
        source, action, data,
    )
    assert result["status"] == "accepted"
    launches = (await awx_stub.get("/_test/launches")).json()
    ev = launches[-1]["extra_vars"]
    assert ev["event_source"] == "alertmanager"
    assert ev["event_action"] == "firing"


@pytest.mark.integration
async def test_alertmanager_resolved_parse_and_dispatch(components, awx_stub):
    """parse_alertmanager resolved + _dispatch_core dispatches correctly."""
    from main import _dispatch_core

    payload = {
        "status":      "resolved",
        "receiver":    "autoflow",
        "groupLabels": {"alertname": "HighCPU"},
        "alerts": [{
            "labels":      {"alertname": "HighCPU", "severity": "warning"},
            "annotations": {},
            "startsAt":    "2026-04-26T09:00:00Z",
            "endsAt":      "2026-04-26T09:30:00Z",
        }],
    }
    source, action, data = parse_alertmanager(payload)
    result = await _dispatch_core(
        components["dedup"], components["rules"], components["awx"],
        source, action, data,
    )
    assert result["status"] == "accepted"
    assert result["action"] == "resolved"


# ── Rule-based routing ────────────────────────────────────────────────────────

@pytest.mark.integration
async def test_rule_routes_to_correct_template(awx_stub):
    """Events are routed to specific AWX templates via YAML rules."""
    from main import _dispatch_core

    rules_content = yaml.dump({
        "rules": [
            {
                "name": "github-main-push",
                "match": {
                    "source": "github",
                    "action": "push",
                    "ref":    "refs/heads/main",
                },
                "job_template_id": 42,
            },
            {
                "name": "alertmanager-critical",
                "match": {"source": "alertmanager", "action": "firing"},
                "job_template_id": 77,
            },
        ]
    })

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(rules_content)
        rules_path = f.name

    try:
        awx_http = AsyncClient(
            transport=ASGITransport(app=_awx_stub_app),
            base_url="http://awx-stub",
            headers={"Authorization": "Bearer test-awx-token"},
            timeout=30.0,
        )
        awx   = AWXClient(awx_http, 1)
        rules = RuleEngine(rules_path, 1)
        dedup = DedupStore(ttl_seconds=0)  # no dedup for this test

        # GitHub push to main → template 42
        source, action, data = parse_github("push", {
            "ref":         "refs/heads/main",
            "repository":  {"full_name": "acme/app"},
            "sender":      {"login": "alice"},
            "head_commit": {"id": "x", "message": "y", "author": {"name": "a"}},
        })
        result = await _dispatch_core(dedup, rules, awx, source, action, data)
        assert result["template_id"] == 42
        launches = (await awx_stub.get("/_test/launches")).json()
        assert launches[-1]["template_id"] == 42

        # Alertmanager firing → template 77
        source2, action2, data2 = parse_alertmanager({
            "status":      "firing",
            "groupLabels": {"alertname": "Disk"},
            "alerts": [{
                "labels":      {"alertname": "Disk", "severity": "critical"},
                "annotations": {},
                "startsAt":    "2026-04-26T10:00:00Z",
                "endsAt":      "0001-01-01T00:00:00Z",
            }],
        })
        result2 = await _dispatch_core(dedup, rules, awx, source2, action2, data2)
        assert result2["template_id"] == 77
        launches = (await awx_stub.get("/_test/launches")).json()
        assert launches[-1]["template_id"] == 77

        await awx_http.aclose()
    finally:
        os.unlink(rules_path)


# ── HTTP health endpoint ──────────────────────────────────────────────────────

@pytest.mark.integration
async def test_health_endpoint_responds(ee_client):
    """Health endpoint returns 200 with service info (uses ee_client fixture)."""
    resp = await ee_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "event-engine"
    assert "version" in body
