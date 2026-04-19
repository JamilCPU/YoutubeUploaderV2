"""Additional tests for scheduler utility functions."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import src.queue_manager as qm
from src.models import RunSummary
from src.scheduler import _interruptible_sleep, _print_run_summary, print_status


def test_print_status_empty(sample_config, tmp_state_file) -> None:
    """print_status should run without error on an empty queue."""
    print_status(sample_config)


def test_print_status_with_entries(sample_config, tmp_state_file) -> None:
    state = qm.load_state()
    p = Path("/some/video.mp4")
    state = qm.mark_uploading(state, p)
    state = qm.mark_uploaded(state, p, "vid123", "Title")
    qm.save_state(state)
    print_status(sample_config)


def test_print_run_summary_zeroes(sample_config) -> None:
    summary = RunSummary()
    _print_run_summary(summary, sample_config)


def test_print_run_summary_with_counts(sample_config) -> None:
    summary = RunSummary(uploaded=3, failed=1, deleted=3)
    _print_run_summary(summary, sample_config)


def test_interruptible_sleep_short() -> None:
    """Sleep for a very short time to verify it doesn't hang."""
    import src.scheduler as sched
    sched._shutdown_requested = False
    _interruptible_sleep(0.01)
    sched._shutdown_requested = False  # reset


def test_interruptible_sleep_exits_on_shutdown() -> None:
    """Sleep should exit early when shutdown is requested."""
    import src.scheduler as sched
    sched._shutdown_requested = True
    _interruptible_sleep(60)  # Would take 60s without shutdown check
    sched._shutdown_requested = False  # reset
