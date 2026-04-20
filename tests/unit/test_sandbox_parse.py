"""Tests for ``src/verification/sandbox_parse.py``.

Security audit findings #21 + #22 (2026-04-20): two parsers in
``src/verification/`` were fragile — both called ``json.loads(stdout)``
on the full stdout string and failed on any non-JSON prefix. A third
parser in the same file correctly used ``splitlines()[-1]``.
Post-fix, all three delegate to ``parse_sandbox_last_json_line`` in
this module.
"""

from __future__ import annotations

import pytest

from src.verification.sandbox_parse import parse_sandbox_last_json_line


class TestParseSandboxLastJsonLine:
    def test_plain_json_parses(self):
        result = {"success": True, "stdout": '{"result": "42", "error": null}\n'}
        parsed = parse_sandbox_last_json_line(result)
        assert parsed == {"result": "42", "error": None}

    @pytest.mark.parametrize(
        "prefix",
        [
            "debug: starting\n",
            "pandas FutureWarning: api moved\n",
            "INFO: loading config\nINFO: done\n",
            "matplotlib backend noise\n",
        ],
    )
    def test_tolerates_debug_prefix(self, prefix):
        """Reporter's core case: debug prints before the JSON line."""
        payload = '{"result": "42", "error": null}'
        result = {"success": True, "stdout": f"{prefix}{payload}\n"}
        parsed = parse_sandbox_last_json_line(result)
        assert parsed == {"result": "42", "error": None}

    def test_failure_when_success_false(self):
        result = {"success": False, "stdout": '{"result": "42"}'}
        assert parse_sandbox_last_json_line(result) is None

    def test_empty_stdout_returns_none(self):
        assert parse_sandbox_last_json_line({"success": True, "stdout": ""}) is None
        assert parse_sandbox_last_json_line({"success": True, "stdout": "   "}) is None
        assert parse_sandbox_last_json_line({"success": True, "stdout": "\n\n"}) is None

    def test_last_line_not_json_returns_none(self):
        """If the last non-empty line isn't JSON, parse fails (the helper
        doesn't scan backward for the nearest JSON line — the contract is
        'the sandbox code emits JSON as the LAST line')."""
        result = {"success": True, "stdout": '{"result": "42"}\nnot-json\n'}
        assert parse_sandbox_last_json_line(result) is None

    def test_missing_stdout_key_returns_none(self):
        """Sandbox failure shape: no stdout key at all."""
        assert parse_sandbox_last_json_line({"success": True}) is None

    def test_whitespace_only_lines_ignored(self):
        """Blank lines between debug output and JSON must not hide the JSON."""
        result = {
            "success": True,
            "stdout": "debug\n\n  \n{\"x\": 1}\n",
        }
        assert parse_sandbox_last_json_line(result) == {"x": 1}

    def test_large_json_object_parses(self):
        import json

        big = {"items": list(range(100)), "nested": {"a": "b"}}
        result = {"success": True, "stdout": f"loading...\n{json.dumps(big)}\n"}
        assert parse_sandbox_last_json_line(result) == big

    def test_nested_newlines_in_json_value_handled(self):
        """Only the final LINE is parsed — a JSON string containing \\n is
        a single line of text if it was emitted via json.dumps (which
        escapes newlines as ``\\n``). Real-world: sandbox code always
        uses ``json.dumps(...)`` which single-lines the output."""
        result = {
            "success": True,
            "stdout": 'debug\n{"msg": "line1\\nline2"}\n',
        }
        assert parse_sandbox_last_json_line(result) == {"msg": "line1\nline2"}
