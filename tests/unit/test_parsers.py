"""Unit tests for webhook parsers (services/event-engine/parsers.py)."""

from parsers import parse_alertmanager, parse_github

# ── GitHub — push ─────────────────────────────────────────────────────────────

def test_github_push_basic():
    payload = {
        "ref": "refs/heads/main",
        "after": "abc123",
        "before": "000000",
        "repository": {"full_name": "acme/app", "name": "app", "html_url": "https://github.com/acme/app"},
        "sender": {"login": "alice"},
        "head_commit": {"id": "abc123", "message": "fix: typo", "author": {"name": "Alice"}},
    }
    source, action, data = parse_github("push", payload)
    assert source == "github"
    assert action == "push"
    assert data["ref"] == "refs/heads/main"
    assert data["repository"]["full_name"] == "acme/app"
    assert data["sender"] == "alice"
    assert data["head_commit"]["id"] == "abc123"
    assert data["github_event"] == "push"


def test_github_push_tag():
    payload = {"ref": "refs/tags/v1.2.3", "repository": {}, "sender": {}}
    _, action, data = parse_github("push", payload)
    assert action == "push"
    assert data["ref"] == "refs/tags/v1.2.3"


def test_github_pull_request_opened():
    payload = {
        "action": "opened",
        "pull_request": {"number": 42, "title": "My PR", "merged": False,
                         "base": {"ref": "main"}, "head": {"ref": "feature-x"}},
        "repository": {},
        "sender": {"login": "bob"},
    }
    source, action, data = parse_github("pull_request", payload)
    assert action == "opened"
    assert data["pull_request"]["number"] == 42
    assert data["pull_request"]["base"] == "main"


def test_github_pull_request_merged():
    payload = {
        "action": "closed",
        "pull_request": {"number": 7, "merged": True,
                         "base": {"ref": "main"}, "head": {"ref": "hotfix"}},
        "repository": {},
        "sender": {},
    }
    _, action, data = parse_github("pull_request", payload)
    assert action == "closed"
    assert data["pull_request"]["merged"] is True


def test_github_release():
    payload = {
        "action": "published",
        "release": {"tag_name": "v2.0.0", "name": "Version 2.0"},
        "repository": {},
        "sender": {},
    }
    _, action, data = parse_github("release", payload)
    assert action == "published"
    assert data["release"]["tag_name"] == "v2.0.0"


def test_github_ping():
    source, action, data = parse_github("ping", {"repository": {}, "sender": {}})
    assert source == "github"
    assert action == "ping"


def test_github_unknown_event_falls_back_to_event_type():
    source, action, _ = parse_github("check_run", {"repository": {}, "sender": {}})
    assert source == "github"
    assert action == "check_run"


# ── Alertmanager ──────────────────────────────────────────────────────────────

_ALERTMANAGER_FIRING = {
    "status": "firing",
    "receiver": "autoflow-webhook",
    "externalURL": "https://alertmanager.example.com",
    "groupLabels": {"alertname": "HighCPU"},
    "alerts": [
        {
            "labels": {"alertname": "HighCPU", "severity": "warning", "instance": "web-01"},
            "annotations": {"summary": "CPU above 90%", "description": "CPU usage is 95%"},
            "startsAt": "2026-04-26T10:00:00Z",
            "endsAt":   "0001-01-01T00:00:00Z",
        }
    ],
}

_ALERTMANAGER_RESOLVED = dict(_ALERTMANAGER_FIRING, status="resolved")


def test_alertmanager_firing():
    source, action, data = parse_alertmanager(_ALERTMANAGER_FIRING)
    assert source == "alertmanager"
    assert action == "firing"
    assert data["alertname"] == "HighCPU"
    assert data["severity"] == "warning"
    assert data["summary"] == "CPU above 90%"
    assert data["alerts_count"] == 1
    assert data["receiver"] == "autoflow-webhook"


def test_alertmanager_resolved():
    source, action, data = parse_alertmanager(_ALERTMANAGER_RESOLVED)
    assert action == "resolved"
    assert data["status"] == "resolved"


def test_alertmanager_empty_alerts():
    source, action, data = parse_alertmanager({"status": "firing", "alerts": []})
    assert action == "firing"
    assert data["alerts_count"] == 0
    assert data["alertname"] == "unknown"


def test_alertmanager_group_labels():
    source, action, data = parse_alertmanager(_ALERTMANAGER_FIRING)
    assert data["group_labels"] == {"alertname": "HighCPU"}


def test_alertmanager_first_alert_details():
    source, action, data = parse_alertmanager(_ALERTMANAGER_FIRING)
    first = data["first_alert"]
    assert first["labels"]["severity"] == "warning"
    assert first["starts_at"] == "2026-04-26T10:00:00Z"
