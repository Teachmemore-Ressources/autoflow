"""
Rule engine — maps incoming events to AWX job templates.

A rule file (YAML) is loaded at startup and can be hot-reloaded via
POST /admin/rules/reload.  Rules are evaluated in declaration order;
the first match wins.  If no rule matches, the engine falls back to
the default template defined in settings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    """Recursively flatten a nested dict with dot-separated keys.

    Example: {"a": {"b": 1}} → {"a.b": 1}
    """
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


# ── Rule model ────────────────────────────────────────────────────────────────


class Rule:
    """
    A single routing rule.

    YAML schema
    -----------
    name: "Human-readable label"
    match:
      source: "github"          # exact string match on event.source
      action: "push"            # exact string match on event.action
      ref: "refs/heads/main"    # arbitrary dotted path into event.data
    job_template_id: 5
    extra_vars:                 # static extra_vars to inject
      environment: "production"
    extra_vars_from_data:       # map event.data paths → extra_vars keys
      repo: "repository.full_name"
      commit: "head_commit.id"
    """

    def __init__(self, raw: dict) -> None:
        self.name: str = raw.get("name", "unnamed")
        self.match: dict = raw.get("match", {})
        self.job_template_id: int = int(raw["job_template_id"])
        self.extra_vars: dict = raw.get("extra_vars", {})
        self.extra_vars_from_data: dict = raw.get("extra_vars_from_data", {})

    def matches(self, source: str, action: str, data: dict) -> bool:
        """Return True if every condition in self.match is satisfied."""
        ctx: dict[str, Any] = {
            "source": source,
            "action": action,
            **_flatten(data),
        }
        for field, expected in self.match.items():
            actual = ctx.get(field)
            # Support simple glob suffix: "refs/heads/*"
            if isinstance(expected, str) and expected.endswith("*"):
                prefix = expected[:-1]
                if not str(actual or "").startswith(prefix):
                    return False
            elif actual != expected:
                return False
        return True

    def build_extra_vars(self, source: str, action: str, data: dict) -> dict:
        """Merge static extra_vars with values extracted from event data."""
        ev: dict = {
            "event_source": source,
            "event_action": action,
            **self.extra_vars,
        }
        flat = _flatten(data)
        for target_key, data_path in self.extra_vars_from_data.items():
            ev[target_key] = flat.get(data_path, "")
        return ev


# ── Rule engine ───────────────────────────────────────────────────────────────


class RuleEngine:
    """Loads rules from a YAML file and resolves events to job templates."""

    def __init__(self, rules_path: str | None, default_template_id: int) -> None:
        self._path = Path(rules_path) if rules_path else None
        self._default_id = default_template_id
        self._rules: list[Rule] = []
        self._load()

    # ── Loading ───────────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path or not self._path.exists():
            logger.info(
                "No rules file at %s — all events routed to default template %d",
                self._path,
                self._default_id,
            )
            self._rules = []
            return

        try:
            with self._path.open() as fh:
                raw = yaml.safe_load(fh) or {}
            self._rules = [Rule(r) for r in raw.get("rules", [])]
            logger.info("Loaded %d rule(s) from %s", len(self._rules), self._path)
        except Exception as exc:
            logger.error("Failed to load rules from %s: %s", self._path, exc)
            self._rules = []

    def reload(self) -> int:
        """Hot-reload rules from disk. Returns new rule count."""
        self._load()
        return len(self._rules)

    # ── Resolution ────────────────────────────────────────────────────────────

    def resolve(self, source: str, action: str, data: dict) -> tuple[int, dict]:
        """
        Return (job_template_id, extra_vars) for the first matching rule.

        Falls back to (default_template_id, full-event-dict) when no rule
        matches.
        """
        for rule in self._rules:
            if rule.matches(source, action, data):
                logger.info(
                    "Rule '%s' matched — routing to template %d",
                    rule.name,
                    rule.job_template_id,
                )
                return rule.job_template_id, rule.build_extra_vars(source, action, data)

        logger.debug(
            "No rule matched (source=%s action=%s) — using default template %d",
            source,
            action,
            self._default_id,
        )
        return self._default_id, {
            "event_source": source,
            "event_action": action,
            "event_data": data,
        }
