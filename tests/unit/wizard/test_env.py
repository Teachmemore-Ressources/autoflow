"""
Unit tests for deploy-wizard environment-file helpers.

Covered:
  - _quote_env_value  : shell-safe quoting of .env values
  - _write_env        : writing .env with template structure preserved
  - _load_env         : loading .env with schema-default fallback
"""
from __future__ import annotations

import stat

import pytest
from dotenv import dotenv_values


# ── Helpers ───────────────────────────────────────────────────────────────────

def _wm():
    import wizard_main as wm
    return wm


# ─────────────────────────────────────────────────────────────────────────────
# _quote_env_value
# ─────────────────────────────────────────────────────────────────────────────

class TestQuoteEnvValue:

    def test_empty_string_returns_empty(self):
        assert _wm()._quote_env_value("") == ""

    def test_simple_alphanum_unchanged(self):
        assert _wm()._quote_env_value("abc123") == "abc123"

    def test_dot_and_hyphen_unchanged(self):
        assert _wm()._quote_env_value("example.com") == "example.com"
        assert _wm()._quote_env_value("my-value-x9") == "my-value-x9"

    def test_path_without_spaces_unchanged(self):
        assert _wm()._quote_env_value("/usr/bin/python3") == "/usr/bin/python3"

    def test_value_with_space_is_quoted(self):
        result = _wm()._quote_env_value("hello world")
        assert result == '"hello world"'

    def test_value_with_dollar_is_quoted(self):
        result = _wm()._quote_env_value("$VAR")
        assert result.startswith('"') and result.endswith('"')

    def test_value_with_semicolon_is_quoted(self):
        result = _wm()._quote_env_value("a;b")
        assert result.startswith('"') and result.endswith('"')

    def test_value_with_pipe_is_quoted(self):
        result = _wm()._quote_env_value("a|b")
        assert result.startswith('"') and result.endswith('"')

    def test_value_with_ampersand_is_quoted(self):
        result = _wm()._quote_env_value("foo&bar")
        assert result.startswith('"') and result.endswith('"')

    def test_double_quote_inside_is_escaped(self):
        result = _wm()._quote_env_value('say "hello"')
        assert result == '"say \\"hello\\""'

    def test_backslash_is_doubled(self):
        result = _wm()._quote_env_value("C:\\Users\\admin")
        assert result == '"C:\\\\Users\\\\admin"'

    def test_backtick_is_quoted(self):
        result = _wm()._quote_env_value("`cmd`")
        assert result.startswith('"') and result.endswith('"')

    def test_asterisk_is_quoted(self):
        result = _wm()._quote_env_value("*.log")
        assert result.startswith('"') and result.endswith('"')

    def test_complex_password_is_quoted(self):
        pwd = "P@$$w0rd!secret"
        result = _wm()._quote_env_value(pwd)
        # Must be wrapped in double quotes
        assert result.startswith('"') and result.endswith('"')


# ─────────────────────────────────────────────────────────────────────────────
# Round-trip: python-dotenv must read back the original value
# ─────────────────────────────────────────────────────────────────────────────

class TestQuoteRoundTrip:

    @pytest.mark.parametrize("raw_value", [
        "simple",
        "with space",
        "has$dollar",
        'has"quote',
        "has\\backslash",
        "https://example.com",
        "P@$$w0rd;dangerous",
        "",
    ])
    def test_round_trip(self, tmp_path, raw_value):
        """Values written with _quote_env_value must survive a dotenv_values read."""
        wm = _wm()
        env_file = tmp_path / ".env"
        quoted = wm._quote_env_value(raw_value)
        env_file.write_text(f"TEST_KEY={quoted}\n")
        loaded = dotenv_values(env_file)
        assert loaded.get("TEST_KEY", "") == raw_value


# ─────────────────────────────────────────────────────────────────────────────
# _write_env
# ─────────────────────────────────────────────────────────────────────────────

class TestWriteEnv:

    def test_writes_key_value_pairs(self, wizard_env_paths):
        _wm()._write_env({"FOO": "bar", "BAZ": "qux"})
        content = wizard_env_paths["env"].read_text()
        assert "FOO=bar" in content
        assert "BAZ=qux" in content

    def test_file_has_restrictive_permissions(self, wizard_env_paths):
        _wm()._write_env({"FOO": "bar"})
        mode = wizard_env_paths["env"].stat().st_mode
        assert stat.S_IMODE(mode) == 0o600

    def test_preserves_comment_lines_from_template(self, wizard_env_paths):
        wizard_env_paths["example"].write_text(
            "# Section header\nFOO=default\nBAR=other\n"
        )
        _wm()._write_env({"FOO": "new", "BAR": "other"})
        lines = wizard_env_paths["env"].read_text().splitlines()
        assert lines[0] == "# Section header"

    def test_replaces_value_in_template(self, wizard_env_paths):
        wizard_env_paths["example"].write_text("FOO=old_value\n")
        _wm()._write_env({"FOO": "new_value"})
        content = wizard_env_paths["env"].read_text()
        assert "FOO=new_value" in content
        assert "FOO=old_value" not in content

    def test_appends_extra_keys_not_in_template(self, wizard_env_paths):
        wizard_env_paths["example"].write_text("FOO=default\n")
        _wm()._write_env({"FOO": "val", "EXTRA_KEY": "appended"})
        content = wizard_env_paths["env"].read_text()
        assert "FOO=val" in content
        assert "EXTRA_KEY=appended" in content
        # Extra key must come after template keys
        assert content.index("EXTRA_KEY") > content.index("FOO=val")

    def test_writes_without_template(self, wizard_env_paths):
        # No .env.example present → still writes the file
        _wm()._write_env({"KEY1": "a", "KEY2": "b"})
        content = wizard_env_paths["env"].read_text()
        assert "KEY1=a" in content
        assert "KEY2=b" in content

    def test_unsafe_values_are_quoted(self, wizard_env_paths):
        _wm()._write_env({"PASS": "secret password"})
        content = wizard_env_paths["env"].read_text()
        assert 'PASS="secret password"' in content

    def test_empty_values_written_without_quotes(self, wizard_env_paths):
        _wm()._write_env({"EMPTY_KEY": ""})
        content = wizard_env_paths["env"].read_text()
        assert "EMPTY_KEY=" in content

    def test_file_ends_with_newline(self, wizard_env_paths):
        _wm()._write_env({"FOO": "bar"})
        content = wizard_env_paths["env"].read_text()
        assert content.endswith("\n")


# ─────────────────────────────────────────────────────────────────────────────
# _load_env
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadEnv:

    def test_returns_dict_with_schema_defaults_when_no_files(self, wizard_env_paths):
        result = _wm()._load_env()
        assert isinstance(result, dict)
        # Schema has many fields — at least 10 expected
        assert len(result) >= 10

    def test_env_file_overrides_defaults(self, wizard_env_paths):
        wizard_env_paths["env"].write_text("POSTGRES_PASSWORD=supersecret\n")
        result = _wm()._load_env()
        assert result["POSTGRES_PASSWORD"] == "supersecret"

    def test_env_example_used_when_no_env_file(self, wizard_env_paths):
        wizard_env_paths["example"].write_text("POSTGRES_PASSWORD=from_example\n")
        result = _wm()._load_env()
        assert result.get("POSTGRES_PASSWORD") == "from_example"

    def test_env_file_takes_precedence_over_example(self, wizard_env_paths):
        wizard_env_paths["env"].write_text("REDIS_PASSWORD=from_env\n")
        wizard_env_paths["example"].write_text("REDIS_PASSWORD=from_example\n")
        result = _wm()._load_env()
        assert result["REDIS_PASSWORD"] == "from_env"

    def test_known_schema_keys_always_present(self, wizard_env_paths):
        """Keys defined in the schema should always be present (at least as empty)."""
        result = _wm()._load_env()
        for key in ("DOMAIN", "POSTGRES_PASSWORD", "REDIS_PASSWORD"):
            assert key in result, f"Expected schema key {key!r} in _load_env result"

    def test_empty_value_in_env_file_returns_empty_string(self, wizard_env_paths):
        wizard_env_paths["env"].write_text("EMPTY_KEY=\n")
        result = _wm()._load_env()
        assert result.get("EMPTY_KEY", "MISSING") == ""
