"""SIGALRM-based budget watchdog for long-running eval tasks.

Security audit ext12 #119 (2026-04-21): the longitudinal runner called
``pipeline.invoke(...)`` in a per-task loop with no timeout. A single hung
Docker container, stalled LangFuse API call, or DeepAgent plateau at
``recursion_limit=50`` could freeze the campaign with no abort signal.

This module provides the watchdog primitive used by
``scripts/run_longitudinal.py`` for per-task fail-fast behavior. The pilot
and recurrence runners currently keep local condition-level watchdogs with
similar semantics. Unix-only: SIGALRM is unavailable on Windows; on
unsupported platforms the context manager is a no-op with a single WARNING
so the caller sees the degradation.
"""

from __future__ import annotations

import contextlib
import logging
import signal
import time
from collections.abc import Iterator


class BudgetExceeded(BaseException):
    """Raised by ``budget_watchdog`` when the wrapped block exceeds the budget.

    Named to match ``scripts.run_pilot.BudgetExceeded`` (ext10 #103), but
    intentionally not re-exported from there. ``run_longitudinal.py`` catches
    this module's exception class; ``run_pilot.py`` keeps its local
    condition-level watchdog and exception.

    Inherits from ``BaseException`` rather than ``Exception`` so a watchdog
    abort cannot be silently swallowed by broad third-party ``except
    Exception:`` handlers. This exact failure mode occurred in the
    longitudinal smoke on 2026-04-22: the SIGALRM fired while OpenTelemetry
    export code was running, OTLP export caught the timeout as a generic
    Exception, logged it, and the task kept going past the supposed hard
    budget.
    """


@contextlib.contextmanager
def budget_watchdog(
    seconds: int,
    *,
    name: str,
    logger_: logging.Logger,
) -> Iterator[None]:
    """Raise ``BudgetExceeded`` if the wrapped block exceeds ``seconds``.

    Args:
        seconds: Budget in seconds. ``<= 0`` disables the watchdog entirely
            (yields immediately with no signal handler installed). Use
            ``0`` when the caller wants to make "no watchdog" an explicit
            configuration decision rather than a silent default.
        name: Human-readable label included in the ``BudgetExceeded``
            message. Examples: ``"task-3-T07"``, ``"condition-full"``.
        logger_: Logger for WARN / INFO messages (platform fallback
            warning + post-block elapsed-time log).

    Implementation note: SIGALRM only fires in the main Python thread.
    Synchronous per-task / per-condition loops run in main so an
    interpreter-level stall (LLM call, Docker exec, file I/O) interrupts
    cleanly. A C-extension stall that releases the GIL (e.g., a stuck
    ``subprocess.run``) fires the signal handler when the GIL returns —
    usually within seconds of the underlying call returning. Sub-second
    precision isn't the point; preventing multi-hour silent stalls is.
    """
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        if seconds > 0 and not hasattr(signal, "SIGALRM"):
            logger_.warning(
                "budget_watchdog requested (%ds) for '%s' but signal.SIGALRM "
                "is unavailable on this platform — skipping watchdog. A hung "
                "subprocess will block without bound.",
                seconds, name,
            )
        yield
        return

    def _on_alarm(_sig: int, _frame: object) -> None:
        raise BudgetExceeded(
            f"'{name}' exceeded {seconds}s budget — watchdog fired "
            f"(security audit ext12 #119)"
        )

    prev_handler = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(seconds)
    t_start = time.monotonic()
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev_handler)
        elapsed = time.monotonic() - t_start
        logger_.info(
            "budget_watchdog('%s'): elapsed %.1fs / budget %ds (%.1f%%)",
            name, elapsed, seconds, 100.0 * elapsed / max(seconds, 1),
        )
