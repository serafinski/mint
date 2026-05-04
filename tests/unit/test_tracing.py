"""Unit tests for src/orchestrator/tracing.py.

Covers tracing metadata propagation:
- ``trace_context`` context manager sets both ContextVar and module-global.
- ``_read_trace_context`` prefers ContextVar but falls back to global.
- ``emit_langfuse_event`` picks up fallback when get_config() returns nothing.
- Cross-thread visibility (ThreadPoolExecutor doesn't propagate ContextVars
  by default, so tracing uses explicit submit-context propagation and a
  module-global fallback for worker emissions).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from src.orchestrator.tracing import (
    _has_active_parent_span,
    _read_trace_context,
    _trace_context_global,
    _trace_context_lock,
    _trace_context_var,
    emit_langfuse_event,
    trace_context,
)


class TestTraceContext:
    def test_sets_contextvar_and_global(self):
        with trace_context(tags=["a", "b"], session_id="s1", user_id="u1"):
            ctx = _trace_context_var.get()
            assert ctx is not None
            assert ctx["tags"] == ["a", "b"]
            assert ctx["session_id"] == "s1"
            assert ctx["user_id"] == "u1"
            with _trace_context_lock:
                assert _trace_context_global["tags"] == ["a", "b"]
                assert _trace_context_global["session_id"] == "s1"

    def test_cleans_up_on_exit(self):
        with trace_context(tags=["x"], session_id="s", user_id="u"):
            pass
        assert _trace_context_var.get() is None
        with _trace_context_lock:
            assert _trace_context_global == {}

    def test_nested_contexts_merge(self):
        with trace_context(tags=["outer"], session_id="outer_s"):
            with trace_context(tags=["inner"], user_id="inner_u"):
                ctx = _trace_context_var.get()
                assert ctx["tags"] == ["inner"]  # inner overrides
                assert ctx["session_id"] == "outer_s"  # outer preserved
                assert ctx["user_id"] == "inner_u"
            # Back in outer
            ctx = _trace_context_var.get()
            assert ctx["tags"] == ["outer"]
            assert ctx["session_id"] == "outer_s"
            assert "user_id" not in ctx

    def test_read_trace_context_prefers_contextvar(self):
        with _trace_context_lock:
            _trace_context_global.clear()
            _trace_context_global["tags"] = ["global-only"]
        try:
            with trace_context(tags=["contextvar-value"]):
                result = _read_trace_context()
                assert result["tags"] == ["contextvar-value"]
        finally:
            with _trace_context_lock:
                _trace_context_global.clear()

    def test_read_trace_context_falls_back_to_global(self):
        # ContextVar is None, but module-global set.
        with _trace_context_lock:
            _trace_context_global.clear()
            _trace_context_global["tags"] = ["global-fallback"]
            _trace_context_global["session_id"] = "s-global"
        try:
            result = _read_trace_context()
            assert result["tags"] == ["global-fallback"]
            assert result["session_id"] == "s-global"
        finally:
            with _trace_context_lock:
                _trace_context_global.clear()

    def test_cross_thread_visibility_via_global(self):
        # This is the core regression test: ContextVars don't propagate
        # to ThreadPoolExecutor workers, so verification_node's gate pool
        # must read tags from the module-global fallback. Verify that a
        # worker thread inside a trace_context sees the module-global
        # even though its ContextVar was never set.
        captured: dict = {}
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()  # ensure we run INSIDE the outer `with`
            # ContextVar is not propagated to this thread by default
            captured["ctx_var"] = _trace_context_var.get()
            captured["read"] = _read_trace_context()

        with trace_context(tags=["cross-thread"], session_id="s-ct"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(worker)
                barrier.wait()
                fut.result(timeout=5)

        assert captured["ctx_var"] is None, (
            "Sanity check — ContextVars shouldn't propagate to ThreadPool "
            "workers by default. If this ever fires, Python's stdlib "
            "behavior has changed and the module-global fallback can be "
            "simplified."
        )
        assert captured["read"]["tags"] == ["cross-thread"]
        assert captured["read"]["session_id"] == "s-ct"


class TestEmitLangfuseEventFallback:
    def test_falls_back_to_trace_context_when_get_config_empty(self):
        """Primary concern: events emitted outside LangGraph pipeline nodes
        (ThreadPool workers, post-pipeline callers, LangChain @tool callbacks)
        must still inherit tags from the active trace_context.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None
        mock_span = MagicMock()
        mock_client.start_as_current_span.return_value.__enter__.return_value = mock_span

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=None):
            with trace_context(
                tags=["from-fallback"],
                session_id="sess-fallback",
                user_id="user-fallback",
            ):
                emit_langfuse_event(None, "some.event", "DEFAULT", {"k": "v"})

        # update_current_trace was called with tags/session_id/user_id
        # sourced from trace_context (not from get_config, which was empty).
        call = mock_client.update_current_trace.call_args
        assert call is not None
        kwargs = call.kwargs
        assert kwargs.get("tags") == ["from-fallback"]
        assert kwargs.get("session_id") == "sess-fallback"
        assert kwargs.get("user_id") == "user-fallback"

    def test_prefers_get_config_over_trace_context(self):
        """When LangGraph pipeline node IS active, its config.metadata
        takes precedence over trace_context (same-task-same-values, but
        the precedence matters for correctness in nested invocations).
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None

        langgraph_config = {"metadata": {
            "langfuse_tags": ["from-get-config"],
            "langfuse_session_id": "sess-get-config",
            "langfuse_user_id": "user-get-config",
        }}

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=langgraph_config):
            with trace_context(tags=["from-fallback"], session_id="sess-fallback"):
                emit_langfuse_event(None, "some.event", "DEFAULT", {})

        call = mock_client.update_current_trace.call_args
        kwargs = call.kwargs if call else {}
        assert kwargs.get("tags") == ["from-get-config"]
        assert kwargs.get("session_id") == "sess-get-config"
        assert kwargs.get("user_id") == "user-get-config"

    def test_no_trace_context_no_tags_emits_bare_event(self):
        """With neither get_config nor trace_context populated, the event
        still emits — just without tags/session/user. Must not raise.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=None):
            # No trace_context wrap
            emit_langfuse_event(None, "bare.event", "DEFAULT", {})

        # The span still opens + event still fires
        assert mock_client.start_as_current_span.called
        # update_current_trace was called but without tags/session/user kwargs
        call = mock_client.update_current_trace.call_args
        if call is not None:
            kwargs = call.kwargs
            assert "tags" not in kwargs
            assert "session_id" not in kwargs
            assert "user_id" not in kwargs

    def test_empty_tags_omitted_not_passed_as_empty_list(self):
        """Regression guard for the arrayValue protobuf-serialization bug:
        passing tags=[] to update_current_trace causes SDK v3 to serialize
        the empty list as the literal string ``{"arrayValue":{}}`` tag.
        Kwargs must be built dynamically so empty values are omitted.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=None):
            with trace_context(session_id="s", user_id="u"):
                # tags not passed — default None → not in kwargs at all
                emit_langfuse_event(None, "no-tags.event", "DEFAULT", {})

        call = mock_client.update_current_trace.call_args
        assert call is not None
        kwargs = call.kwargs
        assert "tags" not in kwargs  # not passed as empty list
        assert kwargs.get("session_id") == "s"
        assert kwargs.get("user_id") == "u"

    def test_never_raises_on_langfuse_import_failure(self):
        """Never-raises contract: if LangFuse is unavailable, silently no-op."""
        with patch("langfuse.get_client", side_effect=ImportError("no langfuse")):
            # Must not propagate
            emit_langfuse_event(None, "should-swallow", "DEFAULT", {"k": "v"})

    def test_cross_thread_emit_with_trace_context_gets_tags(self):
        """End-to-end: emit_langfuse_event called from a ThreadPool worker
        picks up tags from the outer trace_context via the module-global
        fallback. This is the core verification_node / adversarial worker
        case the fix targets.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None
        captured_update = {}

        def capture_update(**kwargs):
            captured_update.update(kwargs)

        mock_client.update_current_trace.side_effect = capture_update

        def worker():
            with patch("langfuse.get_client", return_value=mock_client), \
                 patch("langgraph.config.get_config", return_value=None):
                emit_langfuse_event(None, "worker.event", "DEFAULT", {})

        with trace_context(tags=["thread-tags"], session_id="thread-sess"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(worker).result(timeout=5)

        assert captured_update.get("tags") == ["thread-tags"]
        assert captured_update.get("session_id") == "thread-sess"


class TestOtelAwareWrapperSpan:
    """OTel-aware parent detection for LangChain-auto-traced pipeline nodes."""

    def test_has_active_parent_span_detects_langfuse_sdk_contextvar(self):
        """When lf.current_trace_id is set, _has_active_parent_span returns True."""
        mock_lf = MagicMock()
        mock_lf.current_trace_id = "some-trace-id"
        assert _has_active_parent_span(mock_lf) is True

    def test_has_active_parent_span_detects_otel_context(self):
        """When OTel has an active valid span, return True even if
        LangFuse SDK contextvar is unset. This is the key fix — previously
        only the SDK contextvar was checked."""
        mock_lf = MagicMock()
        mock_lf.current_trace_id = None

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.is_valid = True
        mock_ctx.trace_id = 12345
        mock_span.get_span_context.return_value = mock_ctx

        with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            assert _has_active_parent_span(mock_lf) is True

    def test_has_active_parent_span_false_for_invalid_otel_span(self):
        """OTel's INVALID_SPAN sentinel (no active span) has is_valid=False.
        Must return False so emit_langfuse_event creates a fresh wrapper.
        """
        mock_lf = MagicMock()
        mock_lf.current_trace_id = None

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.is_valid = False
        mock_ctx.trace_id = 0
        mock_span.get_span_context.return_value = mock_ctx

        with patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            assert _has_active_parent_span(mock_lf) is False

    def test_has_active_parent_span_defensive_on_otel_import_failure(self):
        """If opentelemetry isn't available, fall back to SDK-only check."""
        mock_lf = MagicMock()
        mock_lf.current_trace_id = None

        with patch(
            "opentelemetry.trace.get_current_span",
            side_effect=ImportError("no opentelemetry"),
        ):
            assert _has_active_parent_span(mock_lf) is False

    def test_skips_wrapper_span_when_otel_parent_active(self):
        """Core behavioral test: emit_langfuse_event called inside an active
        OTel span (simulating a LangChain-auto-traced pipeline node) should
        NOT create a wrapper SPAN. Just call lf.create_event directly and
        let OTel propagation attach it to the parent.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None  # LangFuse SDK contextvar unset

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.is_valid = True
        mock_ctx.trace_id = 42
        mock_span.get_span_context.return_value = mock_ctx

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=None), \
             patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            emit_langfuse_event(None, "nested.event", "DEFAULT", {"k": "v"})

        # THE regression guard: start_as_current_span must NOT be called
        # (would create the empty wrapper SPAN)
        assert not mock_client.start_as_current_span.called, (
            "Wrapper SPAN should not be created when OTel parent is active — "
            "this is the core expectation. If this test fails, the OTel-aware "
            "_has_active_parent_span check regressed and we're back to ~50% "
            "observation inflation."
        )
        # create_event still fires, just without a wrapping SPAN
        assert mock_client.create_event.called
        call_kwargs = mock_client.create_event.call_args.kwargs
        assert call_kwargs.get("name") == "nested.event"
        assert call_kwargs.get("metadata") == {"k": "v"}

    def test_still_wraps_when_truly_standalone(self):
        """The wrapper SPAN is STILL created when no parent exists anywhere.
        This is load-bearing for post-pipeline emits (cycle_flush,
        distribution_shift) — the wrapper is needed to carry tags via
        update_current_trace.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.is_valid = False
        mock_ctx.trace_id = 0
        mock_span.get_span_context.return_value = mock_ctx

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=None), \
             patch("opentelemetry.trace.get_current_span", return_value=mock_span):
            with trace_context(tags=["standalone"], session_id="ss"):
                emit_langfuse_event(None, "standalone.event", "DEFAULT", {})

        # Wrapper SPAN IS created + tags attached
        assert mock_client.start_as_current_span.called
        update_call = mock_client.update_current_trace.call_args
        assert update_call is not None
        assert update_call.kwargs.get("tags") == ["standalone"]
        assert update_call.kwargs.get("session_id") == "ss"


class TestTraceIdFallback:
    """Trace ID fallback for worker events emitted under trace_context."""

    def test_trace_context_stores_trace_id(self):
        """trace_context accepts and stores trace_id."""
        with trace_context(trace_id="pipeline-trace-abc"):
            ctx = _trace_context_var.get()
            assert ctx is not None
            assert ctx.get("trace_id") == "pipeline-trace-abc"

    def test_emit_uses_trace_id_from_context_when_handler_none(self):
        """When no explicit handler/trace_id is passed AND trace_context
        has a trace_id set, emit_langfuse_event attaches via TraceContext
        to that trace_id — no wrapper SPAN created.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=None):
            with trace_context(trace_id="parent-trace-xyz"):
                emit_langfuse_event(None, "nested.event", "DEFAULT", {"k": "v"})

        # trace_id path: create_event called with trace_context kwarg,
        # NOT via start_as_current_span wrapper.
        assert not mock_client.start_as_current_span.called, (
            "When trace_context has a trace_id, the event should attach "
            "via TraceContext — no wrapper SPAN."
        )
        # Must have called create_event with the trace_context kwarg
        calls = mock_client.create_event.call_args_list
        assert len(calls) == 1
        kwargs = calls[0].kwargs
        assert "trace_context" in kwargs, (
            "create_event should be called with trace_context=TraceContext(...)"
        )

    def test_explicit_handler_trace_id_wins_over_context(self):
        """Explicit handler.last_trace_id takes priority over context's
        trace_id (otherwise inner explicit overrides would be ignored)."""
        mock_client = MagicMock()
        mock_client.current_trace_id = None

        mock_handler = MagicMock()
        mock_handler.last_trace_id = "explicit-trace"

        with patch("langfuse.get_client", return_value=mock_client), \
             patch("langgraph.config.get_config", return_value=None):
            with trace_context(trace_id="context-trace"):
                emit_langfuse_event(mock_handler, "ev.name", "DEFAULT", {})

        calls = mock_client.create_event.call_args_list
        assert len(calls) == 1
        tc = calls[0].kwargs.get("trace_context")
        assert tc is not None
        # ``TraceContext`` is a TypedDict in LangFuse SDK v3, so it's a
        # plain dict at runtime. Explicit handler's last_trace_id wins.
        assert tc["trace_id"] == "explicit-trace"

    def test_cross_thread_trace_id_propagates(self):
        """The critical case: ThreadPool workers see trace_id from the
        outer trace_context via the module-global fallback, and their
        emits attach to the parent pipeline trace.
        """
        mock_client = MagicMock()
        mock_client.current_trace_id = None
        captured_trace_ids: list = []

        def capture_event(**kwargs):
            tc = kwargs.get("trace_context")
            if tc is not None:
                # TraceContext is a TypedDict in LangFuse SDK v3
                captured_trace_ids.append(tc["trace_id"])

        mock_client.create_event.side_effect = capture_event

        def worker():
            with patch("langfuse.get_client", return_value=mock_client), \
                 patch("langgraph.config.get_config", return_value=None):
                emit_langfuse_event(None, "worker.event", "DEFAULT", {})

        with trace_context(trace_id="threadpool-parent-trace"):
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(worker).result(timeout=5)

        assert captured_trace_ids == ["threadpool-parent-trace"]
