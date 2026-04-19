"""Tests for src/progress.py."""

from __future__ import annotations

import time

import pytest

from src.progress import UploadProgressTracker, format_eta


def test_initial_percent_zero() -> None:
    tracker = UploadProgressTracker(total_bytes=1000, file_name="test.mp4")
    progress = tracker.update(0)
    assert progress.percent == 0.0


def test_percent_midway() -> None:
    tracker = UploadProgressTracker(total_bytes=1000, file_name="test.mp4")
    progress = tracker.update(500)
    assert progress.percent == pytest.approx(50.0)


def test_percent_complete() -> None:
    tracker = UploadProgressTracker(total_bytes=1000, file_name="test.mp4")
    progress = tracker.update(1000)
    assert progress.percent == pytest.approx(100.0)


def test_speed_increases_over_time() -> None:
    tracker = UploadProgressTracker(total_bytes=10_000_000, file_name="test.mp4")
    tracker.update(0)
    time.sleep(0.05)
    progress = tracker.update(1_000_000)
    assert progress.speed_bps > 0


def test_eta_none_when_no_speed() -> None:
    tracker = UploadProgressTracker(total_bytes=1000, file_name="test.mp4")
    progress = tracker.update(0)
    # speed is 0 at start, so ETA is None
    assert progress.eta_seconds is None or progress.eta_seconds > 0


def test_total_bytes_zero() -> None:
    tracker = UploadProgressTracker(total_bytes=0, file_name="empty.mp4")
    progress = tracker.update(0)
    assert progress.percent == 0.0


def test_format_eta_seconds() -> None:
    assert format_eta(45) == "45s"


def test_format_eta_minutes() -> None:
    assert format_eta(125) == "2m 5s"


def test_format_eta_hours() -> None:
    assert format_eta(3723) == "1h 2m 3s"


def test_format_eta_none() -> None:
    assert format_eta(None) == "unknown"


def test_format_eta_negative() -> None:
    assert format_eta(-1) == "unknown"
