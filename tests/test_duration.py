"""Tests for src/duration.py."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.duration as duration_mod
from src.duration import (
    get_duration_hours,
    get_video_duration_seconds,
    is_ffprobe_available,
)


@pytest.fixture(autouse=True)
def _reset_ffprobe_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level _ffprobe_available cache before every test."""
    monkeypatch.setattr(duration_mod, "_ffprobe_available", None)


def test_get_video_duration_seconds_success(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00")

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "3600.5\n"
    mock_result.stderr = ""

    with (
        patch.object(duration_mod, "is_ffprobe_available", return_value=True),
        patch("subprocess.run", return_value=mock_result) as mock_run,
    ):
        result = get_video_duration_seconds(video)

    assert result == 3600.5
    mock_run.assert_called_once()


def test_get_video_duration_seconds_ffprobe_not_available(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00")

    with patch.object(duration_mod, "is_ffprobe_available", return_value=False):
        result = get_video_duration_seconds(video)

    assert result is None


def test_get_video_duration_seconds_ffprobe_error(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00")

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "Error probing file"

    with (
        patch.object(duration_mod, "is_ffprobe_available", return_value=True),
        patch("subprocess.run", return_value=mock_result),
    ):
        result = get_video_duration_seconds(video)

    assert result is None


def test_get_video_duration_seconds_timeout(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00")

    with (
        patch.object(duration_mod, "is_ffprobe_available", return_value=True),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=30),
        ),
    ):
        result = get_video_duration_seconds(video)

    assert result is None


def test_get_duration_hours(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00")

    with patch.object(duration_mod, "get_video_duration_seconds", return_value=5400.0):
        result = get_duration_hours(video)

    assert result == 1.5


def test_get_duration_hours_none(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00")

    with patch.object(duration_mod, "get_video_duration_seconds", return_value=None):
        result = get_duration_hours(video)

    assert result is None


def test_is_ffprobe_available_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling is_ffprobe_available twice should only invoke subprocess once."""
    mock_run = MagicMock()
    monkeypatch.setattr("subprocess.run", mock_run)

    # First call: subprocess is invoked, caches the result
    result1 = is_ffprobe_available()
    assert mock_run.call_count == 1

    # Second call: cached, subprocess not called again
    result2 = is_ffprobe_available()
    assert mock_run.call_count == 1

    assert result1 == result2
