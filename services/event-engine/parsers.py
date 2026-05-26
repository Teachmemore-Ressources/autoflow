"""
Webhook payload parsers.

Each parser normalises a vendor-specific webhook body into the canonical
(source, action, data) triple consumed by the RuleEngine.
"""

from __future__ import annotations

from typing import Any

# ── GitHub ────────────────────────────────────────────────────────────────────


def parse_github(event_type: str, payload: dict[str, Any]) -> tuple[str, str, dict]:
    """
    Normalise a GitHub webhook payload.

    Parameters
    ----------
    event_type : str
        Value of the ``X-GitHub-Event`` header (e.g. "push", "pull_request").
    payload : dict
        Raw decoded JSON body.

    Returns
    -------
    (source, action, data)
        source is always "github".
        action maps event_type → meaningful verb (see table below).
        data contains the fields most useful for AWX extra_vars.

    Action mapping
    --------------
    push            → "push"
    pull_request    → payload["action"]  (opened / closed / merged / …)
    release         → payload["action"]  (created / published / …)
    workflow_run    → payload["action"]  (requested / completed)
    create          → "create"
    delete          → "delete"
    ping            → "ping"
    *               → raw event_type
    """
    action: str

    if event_type == "push":
        action = "push"
    elif event_type in ("pull_request", "release", "workflow_run", "issues", "issue_comment"):
        action = payload.get("action", event_type)
    elif event_type in ("create", "delete", "ping"):
        action = event_type
    else:
        action = payload.get("action", event_type)

    repo = payload.get("repository", {})
    sender = payload.get("sender", {})
    head_commit = payload.get("head_commit", {})
    pull_request = payload.get("pull_request", {})

    data: dict[str, Any] = {
        # Common
        "ref": payload.get("ref", ""),
        "after": payload.get("after", ""),
        "before": payload.get("before", ""),
        # Repository
        "repository": {
            "full_name": repo.get("full_name", ""),
            "name": repo.get("name", ""),
            "url": repo.get("html_url", ""),
            "default_branch": repo.get("default_branch", "main"),
        },
        # Sender
        "sender": sender.get("login", ""),
        # Push-specific
        "head_commit": {
            "id": head_commit.get("id", ""),
            "message": head_commit.get("message", ""),
            "author": head_commit.get("author", {}).get("name", ""),
        },
        # Pull request-specific
        "pull_request": {
            "number": pull_request.get("number", ""),
            "title": pull_request.get("title", ""),
            "merged": pull_request.get("merged", False),
            "base": pull_request.get("base", {}).get("ref", ""),
            "head": pull_request.get("head", {}).get("ref", ""),
        },
        # Release-specific
        "release": {
            "tag_name": payload.get("release", {}).get("tag_name", ""),
            "name": payload.get("release", {}).get("name", ""),
        },
        # Raw event type for rules
        "github_event": event_type,
    }

    return "github", action, data


# ── Alertmanager ──────────────────────────────────────────────────────────────


def parse_alertmanager(payload: dict[str, Any]) -> tuple[str, str, dict]:
    """
    Normalise an Alertmanager webhook payload.

    Alertmanager sends the same structure for both "firing" and "resolved"
    notifications.  We emit one event per Alertmanager group (not per alert).

    Parameters
    ----------
    payload : dict
        Raw Alertmanager JSON body.

    Returns
    -------
    (source, action, data)
        source  = "alertmanager"
        action  = "firing" | "resolved"
        data    = normalised alert metadata
    """
    status: str = payload.get("status", "unknown")  # "firing" | "resolved"
    action: str = status

    alerts: list[dict] = payload.get("alerts", [])
    first: dict = alerts[0] if alerts else {}

    labels: dict = first.get("labels", {})
    annotations: dict = first.get("annotations", {})

    data: dict[str, Any] = {
        "alertname": labels.get("alertname", "unknown"),
        "severity": labels.get("severity", "unknown"),
        "status": status,
        "summary": annotations.get("summary", ""),
        "description": annotations.get("description", ""),
        "receiver": payload.get("receiver", ""),
        "external_url": payload.get("externalURL", ""),
        "alerts_count": len(alerts),
        "group_labels": payload.get("groupLabels", {}),
        # Keep full first alert for advanced rules
        "first_alert": {
            "labels": labels,
            "annotations": annotations,
            "starts_at": first.get("startsAt", ""),
            "ends_at": first.get("endsAt", ""),
        },
    }

    return "alertmanager", action, data
