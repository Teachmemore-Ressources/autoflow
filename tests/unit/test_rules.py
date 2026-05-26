"""Unit tests for the rule engine (services/event-engine/rules.py)."""

import textwrap

from rules import Rule, RuleEngine

# ── Helpers ───────────────────────────────────────────────────────────────────


def _engine_from_yaml(text: str, default_id: int = 99) -> RuleEngine:
    """Build a RuleEngine from an inline YAML string."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        f.write(textwrap.dedent(text))
        path = f.name
    engine = RuleEngine(path, default_id)
    os.unlink(path)
    return engine


# ── Rule.matches ──────────────────────────────────────────────────────────────


def test_rule_exact_match():
    rule = Rule(
        {
            "name": "push-to-main",
            "match": {"source": "github", "action": "push", "ref": "refs/heads/main"},
            "job_template_id": 5,
        }
    )
    assert rule.matches("github", "push", {"ref": "refs/heads/main"})


def test_rule_glob_suffix():
    rule = Rule(
        {
            "name": "any-push",
            "match": {"source": "github", "action": "push", "ref": "refs/heads/*"},
            "job_template_id": 5,
        }
    )
    assert rule.matches("github", "push", {"ref": "refs/heads/feature-x"})
    assert not rule.matches("github", "push", {"ref": "refs/tags/v1.0"})


def test_rule_no_match_wrong_source():
    rule = Rule(
        {
            "name": "github-only",
            "match": {"source": "github"},
            "job_template_id": 5,
        }
    )
    assert not rule.matches("alertmanager", "firing", {})


def test_rule_nested_data_match():
    rule = Rule(
        {
            "name": "nested-match",
            "match": {"source": "github", "repository.full_name": "acme/webapp"},
            "job_template_id": 7,
        }
    )
    assert rule.matches("github", "push", {"repository": {"full_name": "acme/webapp"}})
    assert not rule.matches("github", "push", {"repository": {"full_name": "acme/other"}})


def test_rule_empty_match_always_matches():
    rule = Rule({"name": "catch-all", "match": {}, "job_template_id": 1})
    assert rule.matches("anything", "anything", {})


# ── Rule.build_extra_vars ─────────────────────────────────────────────────────


def test_rule_extra_vars_static():
    rule = Rule(
        {
            "name": "prod-deploy",
            "match": {},
            "job_template_id": 5,
            "extra_vars": {"environment": "production", "debug": False},
        }
    )
    ev = rule.build_extra_vars("github", "push", {})
    assert ev["environment"] == "production"
    assert ev["event_source"] == "github"
    assert ev["event_action"] == "push"


def test_rule_extra_vars_from_data():
    rule = Rule(
        {
            "name": "extract-data",
            "match": {},
            "job_template_id": 5,
            "extra_vars_from_data": {
                "repo": "repository.full_name",
                "commit": "head_commit.id",
            },
        }
    )
    data = {"repository": {"full_name": "acme/app"}, "head_commit": {"id": "abc123"}}
    ev = rule.build_extra_vars("github", "push", data)
    assert ev["repo"] == "acme/app"
    assert ev["commit"] == "abc123"


def test_rule_extra_vars_missing_path_defaults_to_empty():
    rule = Rule(
        {
            "name": "missing-path",
            "match": {},
            "job_template_id": 5,
            "extra_vars_from_data": {"tag": "release.tag_name"},
        }
    )
    ev = rule.build_extra_vars("github", "push", {})
    assert ev["tag"] == ""


# ── RuleEngine.resolve ────────────────────────────────────────────────────────


def test_engine_first_match_wins():
    engine = _engine_from_yaml("""
        rules:
          - name: push-main
            match: {source: github, action: push, ref: refs/heads/main}
            job_template_id: 10
          - name: push-any
            match: {source: github, action: push}
            job_template_id: 20
    """)
    template_id, ev = engine.resolve("github", "push", {"ref": "refs/heads/main"})
    assert template_id == 10


def test_engine_second_rule_matches_when_first_doesnt():
    engine = _engine_from_yaml("""
        rules:
          - name: push-main
            match: {source: github, action: push, ref: refs/heads/main}
            job_template_id: 10
          - name: push-any
            match: {source: github, action: push}
            job_template_id: 20
    """)
    template_id, _ = engine.resolve("github", "push", {"ref": "refs/heads/feature"})
    assert template_id == 20


def test_engine_fallback_to_default_when_no_match():
    engine = _engine_from_yaml(
        """
        rules:
          - name: github-only
            match: {source: github}
            job_template_id: 5
    """,
        default_id=99,
    )
    template_id, ev = engine.resolve("alertmanager", "firing", {})
    assert template_id == 99
    assert ev["event_source"] == "alertmanager"
    assert ev["event_action"] == "firing"


def test_engine_no_rules_file_uses_default():
    engine = RuleEngine(rules_path=None, default_template_id=42)
    template_id, ev = engine.resolve("ci", "deploy", {"env": "prod"})
    assert template_id == 42
    assert "event_data" in ev


def test_engine_reload_picks_up_changes(tmp_path):
    rules_file = tmp_path / "rules.yml"
    rules_file.write_text("""
rules:
  - name: initial
    match: {source: ci}
    job_template_id: 1
""")
    engine = RuleEngine(str(rules_file), default_template_id=99)
    assert engine.resolve("ci", "deploy", {})[0] == 1

    rules_file.write_text("""
rules:
  - name: updated
    match: {source: ci}
    job_template_id: 7
""")
    engine.reload()
    assert engine.resolve("ci", "deploy", {})[0] == 7
