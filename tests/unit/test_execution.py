"""Unit tests for tests/evaluation/execution.py — SKILL/AGENT execution via DeepAgent."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.evaluation.execution import (
    AGENT_EXECUTION_TIMEOUT_S,
    LEAD_WITH_SUBAGENTS_TIMEOUT_S,
    TASK_RESULT_MODELS,
    AgentExecutionMeta,
    GenericResult,
    T11Result,
    T12Result,
    T13Result,
    T19Result,
    _build_dynamic_result_model,
    _agent_process_context,
    _agent_timeout_seconds,
    _extract_last_ai_json,
    _extract_last_json_object,
    _extract_last_sandbox_json,
    _fixtures_to_workspace_files,
    _format_fixture_message,
    _read_fixture_contents,
    _record_agent_execution_meta,
    _select_result_model,
    clear_last_agent_execution_meta,
    execute_agent_artifact,
    execute_skill_artifact,
    get_last_agent_execution_meta,
)


def _inline_agent_process_context():
    class _InlineQueue:
        def __init__(self):
            self._items: list[dict] = []

        def put(self, item):
            self._items.append(item)

        def get_nowait(self):
            return self._items.pop(0)

    class _InlineProcess:
        def __init__(self, target, args, kwargs):
            self._target = target
            self._args = args
            self._kwargs = kwargs
            self.exitcode = 0
            self._alive = False

        def start(self):
            self._alive = True
            self._target(*self._args, **self._kwargs)
            self._alive = False

        def join(self, timeout=None):
            return None

        def is_alive(self):
            return self._alive

        def terminate(self):
            self._alive = False

        def kill(self):
            self._alive = False

    class _InlineContext:
        def Queue(self, maxsize=1):
            return _InlineQueue()

        def Process(self, target, args=(), kwargs=None):
            return _InlineProcess(target, args, kwargs or {})

    return _InlineContext()


# ---------------------------------------------------------------------------
# Result model tests
# ---------------------------------------------------------------------------


class TestResultModels:
    def test_t11_result_valid(self):
        r = T11Result(summary_row_count=4, region_revenues={"North": 1000.0}, regions_meeting_target=2)
        assert r.summary_row_count == 4

    def test_t12_result_valid(self):
        r = T12Result(clean_row_count=60, violations_found=20, rules_applied=3)
        assert r.rules_applied == 3

    def test_t13_result_valid(self):
        r = T13Result(anomaly_count=5, anomalies=[{"ts": "a", "m": "b", "v": 1}], metrics_analyzed=4)
        assert r.anomaly_count == 5

    def test_t19_result_valid(self):
        r = T19Result(n_rows=25, n_categories=3, categories=["A", "B", "C"])
        assert r.n_rows == 25

    def test_generic_result(self):
        r = GenericResult(result={"key": "value"})
        assert r.result == {"key": "value"}

    def test_task_result_models_mapping(self):
        assert TASK_RESULT_MODELS["T11"] is T11Result
        assert TASK_RESULT_MODELS["T12"] is T12Result
        assert TASK_RESULT_MODELS["T13"] is T13Result
        assert TASK_RESULT_MODELS["T19"] is T19Result
        assert "T01" not in TASK_RESULT_MODELS


class TestSelectResultModel:
    def test_named_task_prefers_task_model_without_output_schema(self):
        task = SimpleNamespace(task_id="T19")
        model = _select_result_model(task)
        assert model is T19Result

    def test_chain_step_output_schema_overrides_task_model(self):
        task = SimpleNamespace(task_id="T15")
        schema = {
            "daily_aggregates": "list[dict]",
            "metrics_processed": "int",
        }
        model = _select_result_model(
            task,
            output_schema=schema,
            prefer_output_schema=True,
        )
        assert model is not TASK_RESULT_MODELS["T15"]
        assert set(model.model_fields.keys()) == set(schema.keys())

    def test_generic_task_uses_dynamic_model_when_output_schema_present(self):
        task = SimpleNamespace(task_id="")
        schema = {"records": "list[dict]"}
        model = _select_result_model(
            task,
            output_schema=schema,
            prefer_output_schema=True,
        )
        assert model is not GenericResult
        assert set(model.model_fields.keys()) == {"records"}


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


class TestReadFixtureContents:
    def test_reads_existing_files(self, tmp_path):
        f1 = tmp_path / "data.csv"
        f1.write_text("a,b\n1,2\n")
        task = SimpleNamespace(fixture_paths=[f1])
        contents = _read_fixture_contents(task)
        assert "data.csv" in contents
        assert contents["data.csv"] == "a,b\n1,2\n"

    def test_missing_file_skipped(self, tmp_path):
        task = SimpleNamespace(fixture_paths=[tmp_path / "nonexistent.csv"])
        contents = _read_fixture_contents(task)
        assert len(contents) == 0

    def test_no_fixture_paths(self):
        task = SimpleNamespace(fixture_paths=[])
        contents = _read_fixture_contents(task)
        assert contents == {}


class TestFormatFixtureMessage:
    def test_formats_files(self):
        result = _format_fixture_message({"a.csv": "col1\n1", "b.json": "{}"})
        assert "--- FILE: a.csv ---" in result
        assert "--- FILE: b.json ---" in result
        assert "col1\n1" in result


# ---------------------------------------------------------------------------
# SKILL execution
# ---------------------------------------------------------------------------


class TestExecuteSkillArtifact:
    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_returns_dict_on_success(self, mock_inner):
        mock_inner.return_value = {"summary_row_count": 4, "region_revenues": {}, "regions_meeting_target": 2}
        task = SimpleNamespace(task_id="T11", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(skill_content="# Test Skill", capability_type="skill")
        result = execute_skill_artifact(artifact, task, {})
        assert result == {"summary_row_count": 4, "region_revenues": {}, "regions_meeting_target": 2}

    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_returns_empty_on_exception(self, mock_inner):
        mock_inner.side_effect = RuntimeError("boom")
        task = SimpleNamespace(task_id="T11", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(skill_content="# Test Skill", capability_type="skill")
        result = execute_skill_artifact(artifact, task, {})
        assert result == {}

    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_returns_empty_on_timeout(self, mock_inner):
        import concurrent.futures
        mock_inner.side_effect = concurrent.futures.TimeoutError()
        task = SimpleNamespace(task_id="T11", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(skill_content="# Test Skill", capability_type="skill")
        result = execute_skill_artifact(artifact, task, {})
        assert result == {}


# ---------------------------------------------------------------------------
# AGENT execution
# ---------------------------------------------------------------------------


class TestExecuteAgentArtifact:
    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_returns_dict_on_success(self, mock_inner):
        mock_inner.return_value = {"anomaly_count": 5, "anomalies": [], "metrics_analyzed": 4}
        task = SimpleNamespace(task_id="T13", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(
            agent_config={"system_prompt": "You are an agent.", "is_deep_agent": False},
            skill_content=None,
            capability_type="agent",
        )
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            result = execute_agent_artifact(artifact, task, {})
        assert result["anomaly_count"] == 5

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_returns_empty_on_exception(self, mock_inner):
        mock_inner.side_effect = RuntimeError("boom")
        task = SimpleNamespace(task_id="T13", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(agent_config={}, skill_content=None, capability_type="agent")
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            result = execute_agent_artifact(artifact, task, {})
        assert result == {}

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_returns_empty_on_timeout(self, mock_inner):
        import concurrent.futures
        mock_inner.side_effect = concurrent.futures.TimeoutError()
        task = SimpleNamespace(task_id="T13", task_prompt="test", fixture_paths=[])
        artifact = SimpleNamespace(agent_config={}, skill_content=None, capability_type="agent")
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            result = execute_agent_artifact(artifact, task, {})
        assert result == {}


# ---------------------------------------------------------------------------
# Step 13 — sticky agent execution meta
# ---------------------------------------------------------------------------


class TestAgentExecutionMetaThreadLocal:
    """Round-trip tests for the thread-local meta primitives."""

    def setup_method(self):
        clear_last_agent_execution_meta()

    def teardown_method(self):
        clear_last_agent_execution_meta()

    def test_empty_before_first_call(self):
        assert get_last_agent_execution_meta() is None

    def test_record_and_read_success(self):
        _record_agent_execution_meta(succeeded=True)
        meta = get_last_agent_execution_meta()
        assert isinstance(meta, AgentExecutionMeta)
        assert meta.succeeded is True
        assert meta.failure_reason is None
        assert meta.timeout_seconds is None

    def test_record_and_read_timeout(self):
        _record_agent_execution_meta(
            succeeded=False, failure_reason="timeout", timeout_seconds=900,
        )
        meta = get_last_agent_execution_meta()
        assert meta.succeeded is False
        assert meta.failure_reason == "timeout"
        assert meta.timeout_seconds == 900

    def test_record_and_read_error(self):
        _record_agent_execution_meta(succeeded=False, failure_reason="error")
        meta = get_last_agent_execution_meta()
        assert meta.succeeded is False
        assert meta.failure_reason == "error"
        assert meta.timeout_seconds is None

    def test_clear_resets_meta(self):
        _record_agent_execution_meta(succeeded=True)
        assert get_last_agent_execution_meta() is not None
        clear_last_agent_execution_meta()
        assert get_last_agent_execution_meta() is None

    def test_meta_is_thread_local(self):
        import threading
        _record_agent_execution_meta(succeeded=True)
        other_thread_meta: list = []

        def _worker():
            other_thread_meta.append(get_last_agent_execution_meta())

        t = threading.Thread(target=_worker)
        t.start()
        t.join()
        # Main thread still sees its own meta; worker saw nothing.
        assert get_last_agent_execution_meta() is not None
        assert other_thread_meta == [None]


class TestExecuteAgentArtifactMeta:
    """Integration tests for execute_agent_artifact → meta side effects."""

    def setup_method(self):
        clear_last_agent_execution_meta()

    def teardown_method(self):
        clear_last_agent_execution_meta()

    def _artifact(self):
        return SimpleNamespace(
            agent_config={"system_prompt": "You are an agent.", "is_deep_agent": False},
            skill_content=None,
            capability_type="agent",
        )

    def _task(self):
        return SimpleNamespace(task_id="T13", task_prompt="test", fixture_paths=[])

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_success_records_succeeded_true(self, mock_inner):
        mock_inner.return_value = {"result": "ok"}
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            execute_agent_artifact(self._artifact(), self._task(), {})
        meta = get_last_agent_execution_meta()
        assert meta is not None
        assert meta.succeeded is True
        assert meta.failure_reason is None

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_exception_in_inner_records_error(self, mock_inner):
        mock_inner.side_effect = RuntimeError("boom")
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            execute_agent_artifact(self._artifact(), self._task(), {})
        meta = get_last_agent_execution_meta()
        assert meta is not None
        assert meta.succeeded is False
        assert meta.failure_reason == "error"

    def test_inner_sets_timeout_meta_wrapper_preserves(self, monkeypatch):
        """The inner function sets meta on its own timeout branch; the outer
        wrapper must NOT overwrite it with a success signal on a clean return."""

        def _fake_inner(*args, **kwargs):
            _record_agent_execution_meta(
                succeeded=False,
                failure_reason="timeout",
                timeout_seconds=AGENT_EXECUTION_TIMEOUT_S,
            )
            return {}

        import tests.evaluation.execution as exec_mod
        monkeypatch.setattr(exec_mod, "_execute_agent_inner", _fake_inner)

        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            result = execute_agent_artifact(self._artifact(), self._task(), {})
        assert result == {}
        meta = get_last_agent_execution_meta()
        assert meta is not None
        assert meta.succeeded is False
        assert meta.failure_reason == "timeout"
        assert meta.timeout_seconds == AGENT_EXECUTION_TIMEOUT_S

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_meta_cleared_between_calls(self, mock_inner):
        # First call records error.
        mock_inner.side_effect = RuntimeError("boom")
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            execute_agent_artifact(self._artifact(), self._task(), {})
        assert get_last_agent_execution_meta().failure_reason == "error"

        # Second call succeeds — must overwrite, not leak prior error.
        mock_inner.side_effect = None
        mock_inner.return_value = {"ok": True}
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=_inline_agent_process_context(),
        ):
            execute_agent_artifact(self._artifact(), self._task(), {})
        meta = get_last_agent_execution_meta()
        assert meta.succeeded is True
        assert meta.failure_reason is None

    def test_timeout_ceiling_is_900s(self):
        # Regression guard for the 480→900 bump. If this fires, update
        # the value in tests/evaluation/execution.py and re-read the
        # langfuse trace that justified it.
        assert AGENT_EXECUTION_TIMEOUT_S == 900

    def test_lead_with_subagents_timeout_is_extended(self):
        artifact = SimpleNamespace(
            agent_config={
                "system_prompt": "You are an agent.",
                "subagents": [{"name": "worker", "description": "x", "system_prompt": "y", "tools": []}],
            }
        )
        assert _agent_timeout_seconds(artifact) == LEAD_WITH_SUBAGENTS_TIMEOUT_S


# ---------------------------------------------------------------------------
# JSON extraction helpers — regression for CLI T11-style empty-output bug
# ---------------------------------------------------------------------------


def _tool_msg(content: str) -> SimpleNamespace:
    """Build a minimal ToolMessage-like object for the extractors."""
    return SimpleNamespace(type="tool", content=content)


def _ai_msg(content: str) -> SimpleNamespace:
    return SimpleNamespace(type="ai", content=content)


class TestExtractLastSandboxJson:
    """Regression tests for the CLI T11 empty-output bug.

    The original implementation only parsed the LAST LINE of sandbox stdout,
    which broke on `print(json.dumps(result, indent=2))` output (last line
    would be just `}`). This caused MINT CLI SKILL runs to return empty dicts
    when the generated skill used pretty-printed JSON output.
    """

    def test_single_line_json(self):
        import json
        tool_content = json.dumps({
            "success": True,
            "stdout": '{"summary_row_count": 60, "regions_meeting_target": 3}',
            "stderr": "",
            "exit_code": 0,
        })
        result = _extract_last_sandbox_json([_tool_msg(tool_content)])
        assert result == {"summary_row_count": 60, "regions_meeting_target": 3}

    def test_multiline_pretty_printed_json(self):
        """Regression for CLI T11 bug — pretty-printed multi-line JSON."""
        import json
        pretty = json.dumps(
            {"summary_row_count": 60, "region_revenues": {"East": 15019.57}, "regions_meeting_target": 3},
            indent=2,
        )
        tool_content = json.dumps({
            "success": True,
            "stdout": pretty,
            "stderr": "",
            "exit_code": 0,
        })
        result = _extract_last_sandbox_json([_tool_msg(tool_content)])
        assert result is not None
        assert result["summary_row_count"] == 60
        assert result["region_revenues"] == {"East": 15019.57}
        assert result["regions_meeting_target"] == 3

    def test_debug_prefix_then_single_line_json(self):
        """Debug text before a single-line JSON — strategy 2 should catch it."""
        import json
        stdout = 'debug: processed 60 rows\n{"summary_row_count": 60}'
        tool_content = json.dumps({
            "success": True,
            "stdout": stdout,
            "stderr": "",
            "exit_code": 0,
        })
        result = _extract_last_sandbox_json([_tool_msg(tool_content)])
        assert result == {"summary_row_count": 60}

    def test_debug_text_then_pretty_printed_json(self):
        """Debug prefix + pretty-printed JSON — strategy 3 (brace match)."""
        import json
        pretty = json.dumps({"summary_row_count": 60}, indent=2)
        stdout = f"loading data...\nok\n{pretty}\ndone"
        tool_content = json.dumps({
            "success": True,
            "stdout": stdout,
            "stderr": "",
            "exit_code": 0,
        })
        result = _extract_last_sandbox_json([_tool_msg(tool_content)])
        assert result == {"summary_row_count": 60}

    def test_unsuccessful_sandbox_skipped(self):
        import json
        tool_content = json.dumps({
            "success": False,
            "stdout": '{"x": 1}',
            "stderr": "error",
            "exit_code": 1,
        })
        assert _extract_last_sandbox_json([_tool_msg(tool_content)]) is None

    def test_empty_stdout_skipped(self):
        import json
        tool_content = json.dumps({
            "success": True,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
        })
        assert _extract_last_sandbox_json([_tool_msg(tool_content)]) is None

    def test_non_dict_json_rejected(self):
        """A list or primitive at top level should not be accepted."""
        import json
        tool_content = json.dumps({
            "success": True,
            "stdout": "[1, 2, 3]",
            "stderr": "",
            "exit_code": 0,
        })
        assert _extract_last_sandbox_json([_tool_msg(tool_content)]) is None

    def test_iterates_from_most_recent(self):
        """When multiple tool messages exist, the most recent one is used."""
        import json
        old = json.dumps({"success": True, "stdout": '{"x": 1}', "stderr": "", "exit_code": 0})
        new = json.dumps({"success": True, "stdout": '{"x": 99}', "stderr": "", "exit_code": 0})
        result = _extract_last_sandbox_json([_tool_msg(old), _tool_msg(new)])
        assert result == {"x": 99}


class TestExtractLastAiJson:
    """Fallback for when sandbox output can't be parsed but the LLM wrote
    the answer directly in a final AIMessage."""

    def test_whole_ai_message_is_json(self):
        result = _extract_last_ai_json([_ai_msg('{"answer": 42}')])
        assert result == {"answer": 42}

    def test_ai_message_with_prose_and_json(self):
        content = 'Here is the answer:\n{"answer": 42}'
        result = _extract_last_ai_json([_ai_msg(content)])
        assert result == {"answer": 42}

    def test_non_ai_messages_ignored(self):
        result = _extract_last_ai_json([_tool_msg('{"x": 1}')])
        assert result is None

    def test_empty_content_skipped(self):
        result = _extract_last_ai_json([_ai_msg("")])
        assert result is None

    def test_uses_most_recent_ai(self):
        msgs = [_ai_msg('{"old": 1}'), _ai_msg('{"new": 2}')]
        assert _extract_last_ai_json(msgs) == {"new": 2}


class TestExtractLastJsonObject:
    """Brace-matching JSON extractor — used by both sandbox and AI fallbacks."""

    def test_single_object(self):
        assert _extract_last_json_object('{"a": 1}') == {"a": 1}

    def test_object_with_prose_prefix(self):
        assert _extract_last_json_object('the answer is {"a": 1}') == {"a": 1}

    def test_last_of_multiple_objects(self):
        assert _extract_last_json_object('{"first": 1} {"second": 2}') == {"second": 2}

    def test_nested_object(self):
        assert _extract_last_json_object('{"a": {"b": 2}}') == {"a": {"b": 2}}

    def test_no_brace(self):
        assert _extract_last_json_object('no json here') is None

    def test_unbalanced_braces(self):
        assert _extract_last_json_object('{"a": 1') is None


# ---------------------------------------------------------------------------
# Fixture → workspace file conversion — regression for T16 AGENT filesystem bug
# ---------------------------------------------------------------------------


class TestFixturesToWorkspaceFiles:
    """Regression tests for solo-AGENT fixture file access (T16 bug).

    Keys MUST be absolute virtual paths (leading ``/``). DeepAgents'
    ``validate_path`` normalizes every ``read_file`` argument to start with
    ``/`` before ``StateBackend.read`` does a dict lookup, so bare-basename
    keys will never match. The T16 agent trace on 2026-04-15
    (``45a39ccd95d0edbedaa7bae3f71b4290``) showed the exact failure mode:
    the agent called ``read_file("t16_incident.yaml")`` and got back
    ``"Error: File '/t16_incident.yaml' not found"``, then fabricated an
    answer via ``sandbox_execute`` on no data.

    An earlier version of this helper stored fixtures under bare basenames
    and these tests codified that wrong behavior. The test suite thus
    passed while the runtime silently failed — a case of tests enshrining
    the bug the code introduced.
    """

    def test_empty_fixtures(self):
        assert _fixtures_to_workspace_files({}) == {}

    def test_single_fixture_keyed_by_absolute_path(self):
        fixtures = {"t16_incident.yaml": "onset_time: '2024-07-01 05:00:00'\naffected: api"}
        result = _fixtures_to_workspace_files(fixtures)
        # Key MUST start with "/" to match validate_path normalization.
        assert "/t16_incident.yaml" in result
        assert "t16_incident.yaml" not in result
        assert result["/t16_incident.yaml"] == {
            "content": ["onset_time: '2024-07-01 05:00:00'", "affected: api"]
        }

    def test_multiple_fixtures_all_absolute(self):
        fixtures = {
            "t16_system_metrics.csv": "ts,cpu,mem\n0,10,50\n1,11,52",
            "t16_incident.yaml": "onset: '2024-07-01'",
        }
        result = _fixtures_to_workspace_files(fixtures)
        assert set(result.keys()) == {"/t16_system_metrics.csv", "/t16_incident.yaml"}
        assert result["/t16_system_metrics.csv"]["content"] == ["ts,cpu,mem", "0,10,50", "1,11,52"]

    def test_already_absolute_path_not_double_prefixed(self):
        """If a caller passes a key that already starts with ``/``, the
        helper must not turn it into ``//filename``.
        """
        fixtures = {"/already_absolute.txt": "hello"}
        result = _fixtures_to_workspace_files(fixtures)
        assert "/already_absolute.txt" in result
        assert "//already_absolute.txt" not in result

    def test_keys_round_trip_through_validate_path(self):
        """Every key this helper produces must equal what DeepAgents'
        ``validate_path`` returns for the corresponding filename. This is
        the direct invariant the T16 bug violated.
        """
        from deepagents.backends.utils import validate_path

        fixtures = {
            "t16_incident.yaml": "x",
            "t16_system_metrics.csv": "y",
            "nested/not_really_nested.txt": "z",
        }
        result = _fixtures_to_workspace_files(fixtures)
        for original_name in fixtures:
            expected_key = validate_path(original_name)
            assert expected_key in result, (
                f"Fixture {original_name!r} stored under a key that "
                f"validate_path() will never produce. validate_path "
                f"returned {expected_key!r}, stored keys: {list(result)}"
            )

    def test_statebackend_lookup_simulation(self):
        """End-to-end simulation: mimic what DeepAgents' ``StateBackend.read``
        does — call ``validate_path`` on the LLM's file_path argument, then
        do a plain dict lookup against the files dict. This must return a
        match for every original fixture filename.
        """
        from deepagents.backends.utils import validate_path

        fixtures = {"t16_incident.yaml": "onset: x"}
        files = _fixtures_to_workspace_files(fixtures)

        # Agent calls read_file("t16_incident.yaml") — the bare form.
        validated = validate_path("t16_incident.yaml")
        assert files.get(validated) is not None, (
            "StateBackend lookup would fail — bare-basename read_file call "
            "does not resolve. This is the T16 bug."
        )

        # Agent calls read_file("/t16_incident.yaml") — the absolute form.
        validated = validate_path("/t16_incident.yaml")
        assert files.get(validated) is not None

    def test_statebackend_format(self):
        """Each entry must be a dict with a 'content' key whose value is a
        list of strings — the DeepAgents StateBackend expected format.
        """
        fixtures = {"data.csv": "col1\nval1"}
        result = _fixtures_to_workspace_files(fixtures)
        entry = result["/data.csv"]
        assert isinstance(entry, dict)
        assert "content" in entry
        assert isinstance(entry["content"], list)
        assert all(isinstance(line, str) for line in entry["content"])

    def test_preserves_blank_lines_but_strips_newlines(self):
        """splitlines() drops trailing newlines but preserves blank lines
        inside the content, matching how _collect_skill_files handles skills.
        """
        fixtures = {"config.yaml": "key: value\n\nother: value2\n"}
        result = _fixtures_to_workspace_files(fixtures)
        # splitlines() on "a\n\nb\n" → ["a", "", "b"]
        assert result["/config.yaml"]["content"] == ["key: value", "", "other: value2"]

    def test_empty_file_content(self):
        fixtures = {"empty.txt": ""}
        result = _fixtures_to_workspace_files(fixtures)
        assert result["/empty.txt"] == {"content": []}

    def test_single_line_no_trailing_newline(self):
        fixtures = {"oneline.txt": "just one line"}
        result = _fixtures_to_workspace_files(fixtures)
        assert result["/oneline.txt"] == {"content": ["just one line"]}


# ---------------------------------------------------------------------------
# _execute_agent_inner — fixtures are placed in workspace (integration-ish)
# ---------------------------------------------------------------------------


class TestAgentInvokeInput:
    """Verify that _execute_agent_inner passes fixtures into invoke_input['files'].

    These tests mock `create_deep_agent` to capture the invoke_input dict
    and verify fixtures appear in the workspace alongside the skill file.
    """

    def _make_agent_artifact(self, skill_content: str = "# Test Skill\n\nDoes stuff."):
        return SimpleNamespace(
            capability_name="test-agent",
            capability_type="agent",
            skill_content=skill_content,
            agent_config={
                "system_prompt": "You are a test agent.",
                "is_deep_agent": True,
                "skill_content": skill_content,
            },
        )

    def test_fixtures_placed_in_workspace_files(self, tmp_path):
        """Regression: fixtures must land in invoke_input['files'] so the
        agent's read_file tool can access them."""
        from tests.evaluation import execution as exec_mod

        artifact = self._make_agent_artifact()
        task = SimpleNamespace(task_id="T16", task_prompt="Investigate.", fixture_paths=[])
        fixture_contents = {
            "t16_incident.yaml": "onset_time: '2024-07-01 05:00:00'",
            "t16_system_metrics.csv": "timestamp,cpu\n0,10",
        }

        captured_invoke_input: dict = {}

        class _MockAgent:
            def invoke(self_, invoke_input, config):
                captured_invoke_input.update(invoke_input)
                return {"structured_response": None}

        def _mock_create_deep_agent(**kwargs):
            return _MockAgent()

        with patch("deepagents.create_deep_agent", _mock_create_deep_agent):
            with patch("src.orchestrator.generators.skill.SKILLS_OUTPUT_DIR", tmp_path):
                # Short-circuit the skill file setup (not under test here)
                with patch.object(
                    exec_mod, "_ensure_skill_on_disk", lambda *a, **k: None
                ):
                    with patch.object(
                        exec_mod, "_collect_skill_files", return_value={}
                    ):
                        exec_mod._execute_agent_inner(artifact, task, fixture_contents)

        # invoke_input['files'] must contain both fixtures under their
        # absolute virtual paths (leading "/" — matches validate_path
        # normalization, see TestFixturesToWorkspaceFiles).
        assert "files" in captured_invoke_input
        files = captured_invoke_input["files"]
        assert "/t16_incident.yaml" in files
        assert "/t16_system_metrics.csv" in files
        # Content must be in StateBackend list-of-lines format
        assert files["/t16_incident.yaml"]["content"] == [
            "onset_time: '2024-07-01 05:00:00'"
        ]
        assert files["/t16_system_metrics.csv"]["content"] == ["timestamp,cpu", "0,10"]

    def test_user_message_lists_workspace_filenames(self, tmp_path):
        """Agent's user message must tell the LLM which workspace filenames
        are available via read_file, so it uses the right paths."""
        from tests.evaluation import execution as exec_mod

        artifact = self._make_agent_artifact()
        task = SimpleNamespace(task_id="T16", task_prompt="Investigate.", fixture_paths=[])
        fixture_contents = {
            "t16_incident.yaml": "onset: 2024",
            "t16_system_metrics.csv": "ts,cpu\n0,10",
        }

        captured_invoke_input: dict = {}

        class _MockAgent:
            def invoke(self_, invoke_input, config):
                captured_invoke_input.update(invoke_input)
                return {"structured_response": None}

        with patch("deepagents.create_deep_agent", lambda **kw: _MockAgent()):
            with patch("src.orchestrator.generators.skill.SKILLS_OUTPUT_DIR", tmp_path):
                with patch.object(exec_mod, "_ensure_skill_on_disk", lambda *a, **k: None):
                    with patch.object(exec_mod, "_collect_skill_files", return_value={}):
                        exec_mod._execute_agent_inner(artifact, task, fixture_contents)

        msg_content = captured_invoke_input["messages"][0]["content"]
        # The workspace note must reference both filenames and mention read_file
        assert "read_file" in msg_content
        assert "t16_incident.yaml" in msg_content
        assert "t16_system_metrics.csv" in msg_content

    def test_no_fixtures_still_invokes_cleanly(self, tmp_path):
        """Agent with no fixtures must still work (only skill files in workspace)."""
        from tests.evaluation import execution as exec_mod

        artifact = self._make_agent_artifact()
        task = SimpleNamespace(task_id="T99", task_prompt="No inputs.", fixture_paths=[])

        captured_invoke_input: dict = {}

        class _MockAgent:
            def invoke(self_, invoke_input, config):
                captured_invoke_input.update(invoke_input)
                return {"structured_response": None}

        skill_files = {
            "skills/test-agent/SKILL.md": {"content": ["# Test Skill"]}
        }
        with patch("deepagents.create_deep_agent", lambda **kw: _MockAgent()):
            with patch("src.orchestrator.generators.skill.SKILLS_OUTPUT_DIR", tmp_path):
                with patch.object(
                    exec_mod,
                    "_ensure_skill_on_disk",
                    lambda a, sc, sf, sd: sf.update(skill_files),
                ):
                    with patch.object(exec_mod, "_collect_skill_files", return_value={}):
                        exec_mod._execute_agent_inner(artifact, task, {})

        # No fixtures in user message workspace-note
        msg_content = captured_invoke_input["messages"][0]["content"]
        assert "read_file" not in msg_content  # no workspace note when no fixtures
        # Skill file still in workspace
        assert "files" in captured_invoke_input
        assert "skills/test-agent/SKILL.md" in captured_invoke_input["files"]

    def test_fixture_does_not_clobber_skill_with_same_name(self, tmp_path):
        """Skills win on filename collision with fixtures.

        Semantic invariant (NOT a dict.update ordering side-effect):
        ``_execute_agent_inner`` seeds ``workspace_files`` with the skill
        files first, then iterates fixture entries and only inserts each
        one when its key is NOT already present. Pinned to the
        ``if filename not in workspace_files`` guard at
        ``tests/evaluation/execution.py`` around line 1193 (the for-loop
        below ``workspace_files = dict(skill_files)``).

        The assertion below verifies the invariant by semantic intent:
        when a skill and a fixture share the same absolute-virtual path
        (``/SKILL.md``), the skill content survives. Refactoring the
        merge from explicit-loop to any order-independent mechanism
        (e.g. ``{**fixtures, **skills}``) is fine as long as this
        assertion still holds.
        """
        from tests.evaluation import execution as exec_mod

        artifact = self._make_agent_artifact()
        task = SimpleNamespace(task_id="T?", task_prompt="x", fixture_paths=[])
        fixture_contents = {"SKILL.md": "fixture content"}

        captured_invoke_input: dict = {}

        class _MockAgent:
            def invoke(self_, invoke_input, config):
                captured_invoke_input.update(invoke_input)
                return {"structured_response": None}

        # The skill file is stored at the absolute key the fixture would
        # normalize to (``/SKILL.md``) — simulating a collision between a
        # pre-existing skill and an incoming fixture of the same basename.
        existing_skill = {"/SKILL.md": {"content": ["# Skill from skills dir"]}}
        with patch("deepagents.create_deep_agent", lambda **kw: _MockAgent()):
            with patch("src.orchestrator.generators.skill.SKILLS_OUTPUT_DIR", tmp_path):
                with patch.object(
                    exec_mod,
                    "_ensure_skill_on_disk",
                    lambda a, sc, sf, sd: sf.update(existing_skill),
                ):
                    with patch.object(exec_mod, "_collect_skill_files", return_value={}):
                        exec_mod._execute_agent_inner(artifact, task, fixture_contents)

        files = captured_invoke_input["files"]
        # The pre-existing skill must win — the fixture is filtered out.
        assert files["/SKILL.md"]["content"] == ["# Skill from skills dir"]


# ---------------------------------------------------------------------------
# §12 finding #12 — dynamic response_format from decomposer output_schema
# ---------------------------------------------------------------------------


class TestBuildDynamicResultModel:
    def test_empty_schema_returns_none(self):
        from tests.evaluation.execution import _build_dynamic_result_model

        assert _build_dynamic_result_model(None) is None
        assert _build_dynamic_result_model({}) is None

    def test_builds_model_with_declared_keys(self):
        from tests.evaluation.execution import _build_dynamic_result_model

        schema = {
            "metrics_table": "list[dict]",
            "root_cause_metric": "str",
            "cascade_chain": "list[str]",
        }
        model = _build_dynamic_result_model(schema)
        assert model is not None
        # All declared keys must be present as required fields.
        assert set(model.model_fields.keys()) == {
            "metrics_table", "root_cause_metric", "cascade_chain",
        }
        for name, field in model.model_fields.items():
            assert field.is_required(), f"Field '{name}' should be required"

    def test_model_allows_extra_keys(self):
        """extra='allow' tolerates additional keys beyond the declared ones.

        This is an INTENTIONAL design decision — see CLAUDE.md
        §12 finding #12 ("AGENT output_schema compliance") and
        ``MEMORY.md`` topic file
        ``project_agent_output_schema_compliance.md``. Do NOT tighten to
        ``extra='forbid'`` without revisiting that finding.

        Important for lead+subagents AGENTs where the lead produces
        aggregated output covering multiple collapsed gaps plus task-level
        return keys. An earlier extra='forbid' design empirically failed
        on the T16 third-run incident (2026-04-16): the lead produced
        task-level keys as "extras", Pydantic rejected them, and the
        whole compositional chain failed — that run is the reason this
        test exists and the reason the decomposer's declared keys are
        treated as a required floor with extras tolerated on top.

        Required-key enforcement is NOT weakened — see
        ``test_model_still_requires_declared_keys`` below.
        """
        from tests.evaluation.execution import _build_dynamic_result_model

        model = _build_dynamic_result_model({"a": "int"})
        # Declared key must be present.
        instance = model(a=1, b=2, c="extra")
        assert instance.a == 1
        # Extra keys are tolerated (stored via model_extra on Pydantic v2
        # with extra='allow').
        assert getattr(instance, "b", None) == 2

    def test_model_still_requires_declared_keys(self):
        """extra='allow' doesn't weaken required-key enforcement."""
        import pytest
        from pydantic import ValidationError

        from tests.evaluation.execution import _build_dynamic_result_model

        model = _build_dynamic_result_model({"a": "int"})
        # Missing declared key must be rejected even with extra='allow'.
        with pytest.raises(ValidationError):
            model(b=2)  # no "a"


class TestExecuteAgentArtifactDynamicResponseFormat:
    @staticmethod
    def _inline_process_context():
        return _inline_agent_process_context()

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_output_schema_threaded_through(self, mock_inner):
        """execute_agent_artifact forwards output_schema to _execute_agent_inner."""
        from tests.evaluation.execution import execute_agent_artifact

        mock_inner.return_value = {"metrics_table": [], "root_cause_metric": "x"}

        artifact = SimpleNamespace(agent_config={"system_prompt": "you are"})
        task = SimpleNamespace(task_id="", task_prompt="test")

        output_schema = {
            "metrics_table": "list[dict]",
            "root_cause_metric": "str",
        }
        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=self._inline_process_context(),
        ):
            execute_agent_artifact(
                artifact, task, {},
                output_schema=output_schema,
            )

        # _execute_agent_inner received output_schema as a keyword arg.
        _, kwargs = mock_inner.call_args
        assert kwargs["output_schema"] == output_schema

    @patch("tests.evaluation.execution._execute_agent_inner")
    def test_default_no_output_schema(self, mock_inner):
        """Existing callers without output_schema still work — param defaults to None."""
        from tests.evaluation.execution import execute_agent_artifact

        mock_inner.return_value = {}
        artifact = SimpleNamespace(agent_config={"system_prompt": "you are"})
        task = SimpleNamespace(task_id="", task_prompt="test")

        with patch(
            "tests.evaluation.execution._agent_process_context",
            return_value=self._inline_process_context(),
        ):
            execute_agent_artifact(artifact, task, {})

        _, kwargs = mock_inner.call_args
        assert kwargs["output_schema"] is None


class TestAgentProcessContext:
    def test_prefers_spawn_on_darwin(self, monkeypatch):
        import tests.evaluation.execution as exec_mod

        calls = []

        def _fake_get_context(method=None):
            calls.append(method)
            return method

        monkeypatch.setattr(exec_mod.sys, "platform", "darwin")
        monkeypatch.setattr(exec_mod.multiprocessing, "get_context", _fake_get_context)

        ctx = _agent_process_context()
        assert ctx == "spawn"
        assert calls == ["spawn"]

    def test_prefers_fork_off_darwin(self, monkeypatch):
        import tests.evaluation.execution as exec_mod

        calls = []

        def _fake_get_context(method=None):
            calls.append(method)
            return method

        monkeypatch.setattr(exec_mod.sys, "platform", "linux")
        monkeypatch.setattr(exec_mod.multiprocessing, "get_context", _fake_get_context)

        ctx = _agent_process_context()
        assert ctx == "fork"
        assert calls == ["fork"]


class TestExecuteSkillArtifactDynamicResponseFormat:
    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_output_schema_threaded_through(self, mock_inner):
        mock_inner.return_value = {"summary": "ok", "counts": {}}

        artifact = SimpleNamespace(skill_content="# skill")
        task = SimpleNamespace(task_id="T04", task_prompt="test")
        output_schema = {"summary": "str", "counts": "dict"}

        execute_skill_artifact(
            artifact, task, {},
            output_schema=output_schema,
        )

        _, kwargs = mock_inner.call_args
        assert kwargs["output_schema"] == output_schema

    @patch("tests.evaluation.execution._execute_skill_inner")
    def test_default_no_output_schema(self, mock_inner):
        mock_inner.return_value = {}

        artifact = SimpleNamespace(skill_content="# skill")
        task = SimpleNamespace(task_id="T04", task_prompt="test")

        execute_skill_artifact(artifact, task, {})

        _, kwargs = mock_inner.call_args
        assert kwargs["output_schema"] is None
