"""Tests for ``mint retry --edit`` — security audit finding #4 (2026-04-19).

Pre-fix, ``os.system(f'{editor} "{tmp_path}"')`` shelled out with shell=True
and interpolated ``$EDITOR`` into a command string; the ``os.unlink(tmp_path)``
cleanup ran only on the happy path (no ``try/finally``), so a raise between
the editor call and unlink leaked the temp file. Fix uses
``subprocess.run([...], shell=False)`` via ``shlex.split`` and wraps the
editor + read block in ``try/finally`` with ``missing_ok=True``.

These tests patch ``subprocess.run`` so the editor is never actually
invoked — the interesting behaviour is the argv shape and the cleanup
discipline.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _load_last_run_fake(task_text: str = "initial task"):
    """Return a fake load_last_run() result that the retry command accepts."""
    return {
        "task": task_text,
        "files": [],
        "params": [],
        "skip_verification": False,
        "no_optimizer": False,
        "enable_optimizer": False,
        "timeout": None,
        "allow_network": False,
    }


def _invoke_retry_edit(tmp_path, monkeypatch, editor_value: str, editor_side_effect=None):
    """Invoke ``mint retry --edit`` with subprocess.run + execute_task stubbed.

    Returns (exit_code, captured_cmd, tmp_files_remaining).
    """
    from typer.testing import CliRunner

    from src.cli import app as app_module

    monkeypatch.setenv("EDITOR", editor_value)
    monkeypatch.setenv("TMPDIR", str(tmp_path))  # force NamedTemporaryFile into tmp_path
    monkeypatch.setattr(
        app_module, "_settings_path", lambda: tmp_path / "settings.json"
    )

    # Stub load_last_run so we don't need a real last_run.json on disk.
    from src.cli import runner as runner_module

    monkeypatch.setattr(
        runner_module, "load_last_run", lambda: _load_last_run_fake()
    )
    monkeypatch.setattr(
        runner_module, "execute_task", lambda *args, **kwargs: 0
    )

    captured: dict = {}

    def _fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell", False)
        # Simulate editor leaving the file intact (or whatever the caller asks).
        if editor_side_effect is not None:
            editor_side_effect(cmd)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("subprocess.run", side_effect=_fake_run) as mock_run:
        runner_cli = CliRunner()
        result = runner_cli.invoke(app_module.app, ["retry", "--edit"])

    # What tmp files ``mint_*`` remained in tmp_path after retry returned?
    remaining = list(tmp_path.glob("mint_*"))
    return result.exit_code, captured, remaining, mock_run


class TestRetryEditShellFreeInvocation:
    def test_editor_invoked_without_shell(self, tmp_path, monkeypatch):
        exit_code, captured, remaining, mock_run = _invoke_retry_edit(
            tmp_path, monkeypatch, editor_value="vi"
        )
        # subprocess.run was called; shell must be False (default for list argv).
        assert mock_run.called
        assert captured["shell"] is False, (
            "subprocess.run must be invoked with shell=False (default for "
            "list-form argv); security audit finding #4."
        )

    def test_editor_with_flags_splits_correctly(self, tmp_path, monkeypatch):
        """``EDITOR='emacs -nw'`` must split into argv pair, not shell-parse."""
        exit_code, captured, remaining, _ = _invoke_retry_edit(
            tmp_path, monkeypatch, editor_value="emacs -nw"
        )
        cmd = captured["cmd"]
        # shlex.split("emacs -nw") → ["emacs", "-nw"]; then tmp_path appended.
        assert cmd[0] == "emacs"
        assert cmd[1] == "-nw"
        # Last entry is the tmp file path.
        assert cmd[-1].endswith(".txt")
        assert "mint_" in Path(cmd[-1]).name

    def test_shell_metacharacters_do_not_get_executed(self, tmp_path, monkeypatch):
        """``EDITOR='vi; rm -rf ~'`` must produce argv[0]='vi;' (a single token
        that will fail exec), NOT a shell pipeline. The shell cannot see the
        semicolon because shell=False."""
        exit_code, captured, remaining, _ = _invoke_retry_edit(
            tmp_path, monkeypatch, editor_value="vi; rm -rf ~"
        )
        cmd = captured["cmd"]
        # shlex.split("vi; rm -rf ~") → ["vi;", "rm", "-rf", "~"]
        # the important invariant: the semicolon is an inert character in
        # argv[0], not a statement separator.
        assert cmd[0] == "vi;"


class TestRetryEditCleanupDiscipline:
    def test_tmp_file_cleaned_up_on_happy_path(self, tmp_path, monkeypatch):
        exit_code, captured, remaining, _ = _invoke_retry_edit(
            tmp_path, monkeypatch, editor_value="vi"
        )
        # Editor no-op → task text becomes empty after strip → SystemExit(1).
        # Regardless, finally must have cleaned up the mint_* temp.
        assert remaining == [], (
            f"Expected no mint_* tmp files remaining, got {remaining}. "
            "try/finally must clean up even on SystemExit(1)."
        )

    def test_tmp_file_cleaned_up_when_editor_raises(self, tmp_path, monkeypatch):
        """If subprocess.run itself raises (e.g., editor binary missing),
        the finally must still remove the tmp file."""
        def _raise(cmd):
            raise FileNotFoundError(f"editor not found: {cmd[0]}")

        try:
            _invoke_retry_edit(
                tmp_path, monkeypatch,
                editor_value="nonexistent-editor",
                editor_side_effect=_raise,
            )
        except Exception:
            # The runner may re-raise or translate; we only care about cleanup.
            pass

        remaining = list(tmp_path.glob("mint_*"))
        assert remaining == [], (
            f"Expected no mint_* tmp files remaining after editor raised, "
            f"got {remaining}. try/finally didn't run."
        )

    def test_tmp_file_cleaned_up_on_empty_prompt_systemexit(self, tmp_path, monkeypatch):
        """The empty-prompt branch raises SystemExit(1); cleanup must still run.
        Pre-fix, the ``os.unlink`` was BEFORE the SystemExit, so it did run on
        this exact path — but only because of the specific ordering, not a
        try/finally contract. Test documents the invariant explicitly."""
        # Empty editor output → after read().strip() task_text=='' → SystemExit(1).
        exit_code, _, remaining, _ = _invoke_retry_edit(
            tmp_path, monkeypatch, editor_value="vi"
        )
        assert remaining == []
