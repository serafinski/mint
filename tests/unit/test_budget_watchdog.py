"""Tests for the shared eval budget watchdog."""

from __future__ import annotations

import logging
import sys
import time

import pytest

from scripts._budget_watchdog import BudgetExceeded, budget_watchdog


def _logger() -> logging.Logger:
    return logging.getLogger("test_budget_watchdog")


def test_zero_seconds_is_noop():
    ran = False

    with budget_watchdog(0, name="noop", logger_=_logger()):
        ran = True

    assert ran is True


@pytest.mark.skipif(sys.platform == "win32", reason="SIGALRM is unix-only")
def test_fires_on_timeout():
    with pytest.raises(BudgetExceeded, match="exceeded 1s budget"):
        with budget_watchdog(1, name="sleeper", logger_=_logger()):
            time.sleep(2)


@pytest.mark.skipif(sys.platform == "win32", reason="SIGALRM is unix-only")
def test_does_not_fire_on_fast_block():
    with budget_watchdog(5, name="fast", logger_=_logger()):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="SIGALRM is unix-only")
def test_not_swallowed_by_broad_exception_handler():
    """BudgetExceeded inherits BaseException, so broad Exception handlers miss it."""
    swallowed = False

    with pytest.raises(BudgetExceeded, match="exceeded 1s budget"):
        with budget_watchdog(1, name="swallow-guard", logger_=_logger()):
            try:
                time.sleep(2)
            except Exception:  # noqa: BLE001
                swallowed = True

    assert swallowed is False
