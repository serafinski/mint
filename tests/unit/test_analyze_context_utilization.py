"""Unit tests for scripts/analyze_context_utilization.py (ext7 #47 fixes).

Covers:
- Default --environment value matches the runner emission convention
  (`default`, not `production`).
- Loud-fail when the LangFuse fetch returns zero traces across all agents
  (unless --allow-empty is passed).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _build_parser():
    """Rebuild the analyzer's argparse to inspect defaults without invoking main."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--detail", action="store_true")
    parser.add_argument("--csv", nargs="?", const="data/context_utilization.csv")
    # Mirror the post-ext7-#47 defaults in the real script. If these drift,
    # the assertions below will fail to detect it — the second test below
    # invokes main() directly so defaults are read from the real source.
    parser.add_argument("--environment", default="default")
    parser.add_argument("--session-ids", nargs="+", default=None)
    parser.add_argument("--allow-empty", action="store_true")
    return parser


def test_default_environment_matches_runner_emission_convention(monkeypatch):
    """ext7 #47: default is 'default' (matches runner), not 'production'.

    Intercepts parse_args + captures the returned Namespace's --environment
    default. Drives through the real script's main() so drift is caught.
    """
    import scripts.analyze_context_utilization as acu

    captured: dict = {}

    class _StubHttpx:
        @staticmethod
        def get(*a, **k):
            return type("R", (), {"raise_for_status": lambda self: None})()

        class Client:
            def __init__(self, *a, **k):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return None

    monkeypatch.setattr(acu, "_langfuse_client", lambda: ("https://stub", ("u", "p")))
    monkeypatch.setattr(acu, "httpx", _StubHttpx)

    def _capture_env(*args, **kwargs):
        captured["environment"] = kwargs.get("environment")
        return []

    monkeypatch.setattr(acu, "fetch_traces", _capture_env)
    monkeypatch.setattr(acu, "print_summary", lambda *a, **kw: None)
    monkeypatch.setattr(sys, "argv", [
        "analyze_context_utilization.py", "--allow-empty",
    ])

    acu.main()

    assert captured.get("environment") == "default", (
        "ext7 #47: default --environment must be 'default' to match "
        "runner emission convention (LANGFUSE_TRACING_ENVIRONMENT=default). "
        f"Got: {captured.get('environment')!r}."
    )


def test_fails_loud_on_zero_traces_across_all_agents(capsys, monkeypatch):
    """ext7 #47: if every agent returns zero traces, exit 2 with a clear
    error message instead of exit 0 with an empty Chapter 6 T3 section.
    """
    import scripts.analyze_context_utilization as acu

    # Stub LangFuse connectivity + fetch.
    monkeypatch.setattr(acu, "_langfuse_client", lambda: ("https://stub", ("u", "p")))

    class _StubClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return None

    class _StubHttpx:
        @staticmethod
        def get(*a, **k):
            class _R:
                def raise_for_status(self):
                    pass
            return _R()

    monkeypatch.setattr(acu, "httpx", type("M", (), {
        "get": _StubHttpx.get,
        "Client": _StubClient,
    }))
    monkeypatch.setattr(acu, "fetch_traces", lambda *a, **kw: [])

    # Invoke main with only the required defaults.
    monkeypatch.setattr(sys, "argv", ["analyze_context_utilization.py"])
    exit_code = acu.main()
    captured = capsys.readouterr()

    assert exit_code == 2, (
        f"ext7 #47: zero traces must return exit code 2; got {exit_code}."
    )
    assert "zero traces found" in captured.err
    assert "environment=" in captured.err


def test_allow_empty_suppresses_loud_fail(capsys, monkeypatch):
    """--allow-empty keeps the pre-ext7-#47 silent-empty semantics when a user
    legitimately wants to probe a zero-traces environment (e.g., during setup).
    """
    import scripts.analyze_context_utilization as acu

    monkeypatch.setattr(acu, "_langfuse_client", lambda: ("https://stub", ("u", "p")))

    class _StubClient:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return None

    monkeypatch.setattr(acu, "httpx", type("M", (), {
        "get": lambda *a, **k: type("R", (), {"raise_for_status": lambda self: None})(),
        "Client": _StubClient,
    }))
    monkeypatch.setattr(acu, "fetch_traces", lambda *a, **kw: [])
    monkeypatch.setattr(acu, "print_summary", lambda *a, **kw: None)

    monkeypatch.setattr(sys, "argv", ["analyze_context_utilization.py", "--allow-empty"])
    exit_code = acu.main()

    assert exit_code == 0
