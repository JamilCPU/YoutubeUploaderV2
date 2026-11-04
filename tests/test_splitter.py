"""Tests for src/splitter.py."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models import AppConfig
from src.splitter import (
    _calculate_segment_count,
    _has_split_segments,
    _is_part_file,
    scan_and_split,
    split_video,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_old_video(directory: Path, name: str = "2026-01-01 10-00-00.mp4") -> Path:
    """Create a fake video file with an old mtime so the scanner considers it ready."""
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / name
    p.write_bytes(b"\x00" * 1024)
    old_time = time.time() - 7200  # 2 hours ago
    os.utime(p, (old_time, old_time))
    return p


def _ok_subprocess_result() -> MagicMock:
    """A successful subprocess.CompletedProcess mock."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    return result


def _fail_subprocess_result() -> MagicMock:
    """A failed subprocess.CompletedProcess mock."""
    result = MagicMock()
    result.returncode = 1
    result.stdout = ""
    result.stderr = "ffmpeg error"
    return result


# ---------------------------------------------------------------------------
# split_video tests
# ---------------------------------------------------------------------------


class TestSplitVideo:
    def test_split_video_creates_segments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """14h video -> 2 segments (Part 1 and Part 2) with 11h default."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        video = _make_old_video(tmp_path)

        def _ffmpeg_creates_tmp(*args, **kwargs):
            """Simulate ffmpeg writing the .tmp output file."""
            cmd = args[0]
            out = Path(cmd[-1])
            out.write_bytes(b"\x00" * 100)
            return _ok_subprocess_result()

        with (
            patch("src.splitter.get_video_duration_seconds", return_value=50400),
            patch("src.splitter.subprocess.run", side_effect=_ffmpeg_creates_tmp),
        ):
            result = split_video(video)

        assert len(result) == 2
        assert "Part 1" in result[0].name
        assert "Part 2" in result[1].name

    def test_split_video_three_segments(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """25h video with segment_hours=11 -> 3 segments."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        video = _make_old_video(tmp_path)

        def _ffmpeg_creates_tmp(*args, **kwargs):
            cmd = args[0]
            out = Path(cmd[-1])
            out.write_bytes(b"\x00" * 100)
            return _ok_subprocess_result()

        with (
            patch("src.splitter.get_video_duration_seconds", return_value=90000),
            patch("src.splitter.subprocess.run", side_effect=_ffmpeg_creates_tmp),
        ):
            result = split_video(video, segment_hours=11)

        assert len(result) == 3
        for i, path in enumerate(result, 1):
            assert f"Part {i}" in path.name

    def test_split_video_no_split_needed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """10h video with segment_hours=11 -> no split needed (empty list)."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        video = _make_old_video(tmp_path)

        with patch("src.splitter.get_video_duration_seconds", return_value=36000):
            result = split_video(video, segment_hours=11)

        assert result == []

    def test_split_video_ffmpeg_fails_cleans_up(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ffmpeg fails on segment 1, cleanup removes partial .tmp output."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        video = _make_old_video(tmp_path)

        with (
            patch("src.splitter.get_video_duration_seconds", return_value=50400),
            patch("src.splitter.subprocess.run", return_value=_fail_subprocess_result()),
        ):
            result = split_video(video)

        # Should return empty list on failure
        assert result == []
        # No .tmp or final segment files should remain
        remaining = list(tmp_path.glob("*Part*"))
        assert remaining == []

    def test_split_video_no_ffprobe(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When duration cannot be determined, return empty list."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        video = _make_old_video(tmp_path)

        with patch("src.splitter.get_video_duration_seconds", return_value=None):
            result = split_video(video)

        assert result == []


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestIsPartFile:
    def test_part_file_returns_true(self) -> None:
        assert _is_part_file(Path("2026-01-01 10-00-00 Part 1.mp4")) is True

    def test_non_part_file_returns_false(self) -> None:
        assert _is_part_file(Path("2026-01-01 10-00-00.mp4")) is False


class TestHasSplitSegments:
    def test_returns_true_when_part1_exists(self, tmp_path: Path) -> None:
        original = tmp_path / "2026-01-01 10-00-00.mp4"
        original.write_bytes(b"\x00")
        part1 = tmp_path / "2026-01-01 10-00-00 Part 1.mp4"
        part1.write_bytes(b"\x00")

        assert _has_split_segments(original) is True

    def test_returns_true_when_only_part2_exists(self, tmp_path: Path) -> None:
        """Even if Part 1 was deleted, Part 2 existing should prevent re-split."""
        original = tmp_path / "2026-01-01 10-00-00.mp4"
        original.write_bytes(b"\x00")
        part2 = tmp_path / "2026-01-01 10-00-00 Part 2.mp4"
        part2.write_bytes(b"\x00")

        assert _has_split_segments(original) is True

    def test_returns_false_when_no_parts(self, tmp_path: Path) -> None:
        original = tmp_path / "2026-01-01 10-00-00.mp4"
        original.write_bytes(b"\x00")

        assert _has_split_segments(original) is False


class TestCalculateSegmentCount:
    def test_no_split_needed(self) -> None:
        assert _calculate_segment_count(36000, 39600) == 1

    def test_two_segments(self) -> None:
        assert _calculate_segment_count(50400, 39600) == 2

    def test_three_segments(self) -> None:
        assert _calculate_segment_count(90000, 39600) == 3


# ---------------------------------------------------------------------------
# scan_and_split tests
# ---------------------------------------------------------------------------


class TestScanAndSplit:
    def _make_config(self, watch_dir: Path) -> AppConfig:
        return AppConfig(
            watch_directory=watch_dir,
            max_duration_hours=11.5,
            video_extensions=(".mp4",),
            recent_write_threshold_minutes=30,
            client_id="test",
            client_secret="test",
            project_id="test",
        )

    def test_scan_and_split_skips_part_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file named 'video Part 1.mp4' should be skipped (it's already a part file)."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        watch_dir = tmp_path / "recordings"
        _make_old_video(watch_dir, name="video Part 1.mp4")

        config = self._make_config(watch_dir)

        with (
            patch("src.splitter.is_ffprobe_available", return_value=True),
            patch("src.splitter.subprocess.run", return_value=_ok_subprocess_result()),
            patch("src.splitter.get_duration_hours", return_value=15.0),
            patch("src.splitter.get_video_duration_seconds", return_value=54000),
        ):
            result = scan_and_split(config)

        assert result == 0

    def test_scan_and_split_splits_long_video(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A video exceeding max_duration_hours should be split and original moved."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        watch_dir = tmp_path / "recordings"
        video = _make_old_video(watch_dir)

        config = self._make_config(watch_dir)

        def _ffmpeg_creates_tmp(*args, **kwargs):
            cmd = args[0]
            out = Path(cmd[-1])
            out.write_bytes(b"\x00" * 100)
            return _ok_subprocess_result()

        with (
            patch("src.splitter.is_ffprobe_available", return_value=True),
            patch("src.splitter.subprocess.run", side_effect=_ffmpeg_creates_tmp),
            patch("src.splitter.get_duration_hours", return_value=14.0),
            patch("src.splitter.get_video_duration_seconds", return_value=50400),
            patch("src.cleanup.move_to_subfolder", return_value=(True, watch_dir / "split_originals" / video.name)),
        ):
            result = scan_and_split(config)

        assert result == 1

    def test_scan_and_split_skips_short_video(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A video under max_duration_hours should not be split."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        watch_dir = tmp_path / "recordings"
        _make_old_video(watch_dir)

        config = self._make_config(watch_dir)

        with (
            patch("src.splitter.is_ffprobe_available", return_value=True),
            patch("src.splitter.subprocess.run", return_value=_ok_subprocess_result()),
            patch("src.splitter.get_duration_hours", return_value=8.0),
        ):
            result = scan_and_split(config)

        assert result == 0

    def test_scan_and_split_no_ffprobe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ffprobe is unavailable, scan_and_split returns 0."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        watch_dir = tmp_path / "recordings"
        _make_old_video(watch_dir)

        config = self._make_config(watch_dir)

        with patch("src.splitter.is_ffprobe_available", return_value=False):
            result = scan_and_split(config)

        assert result == 0

    def test_scan_and_split_skips_already_split(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Videos that already have Part 1 siblings are skipped."""
        monkeypatch.setattr("src.splitter._shutdown_requested", False)
        watch_dir = tmp_path / "recordings"
        _make_old_video(watch_dir)
        # Create Part 1 sibling
        (watch_dir / "2026-01-01 10-00-00 Part 1.mp4").write_bytes(b"\x00" * 512)
        old_time = time.time() - 7200
        os.utime(watch_dir / "2026-01-01 10-00-00 Part 1.mp4", (old_time, old_time))

        config = self._make_config(watch_dir)

        with (
            patch("src.splitter.is_ffprobe_available", return_value=True),
            patch("src.splitter.subprocess.run", return_value=_ok_subprocess_result()),
            patch("src.splitter.get_duration_hours", return_value=14.0),
        ):
            result = scan_and_split(config)

        assert result == 0
