"""Tests for src/scanner.py."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from src.scanner import scan_for_videos


THRESHOLD = 1800  # 30 minutes


def test_returns_old_files(tmp_watch_dir: Path, old_video: Path) -> None:
    results = scan_for_videos(tmp_watch_dir, (".mp4",), THRESHOLD)
    assert len(results) == 1
    assert results[0].path == old_video


def test_skips_recent_files(tmp_watch_dir: Path, recent_video: Path) -> None:
    results = scan_for_videos(tmp_watch_dir, (".mp4",), THRESHOLD)
    assert results == []


def test_skips_wrong_extension(tmp_watch_dir: Path, old_video: Path) -> None:
    results = scan_for_videos(tmp_watch_dir, (".mkv",), THRESHOLD)
    assert results == []


def test_sorts_oldest_first(tmp_watch_dir: Path) -> None:
    tmp_watch_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()

    newer = tmp_watch_dir / "newer.mp4"
    newer.write_bytes(b"\x00")
    os.utime(newer, (now - 3600, now - 3600))

    older = tmp_watch_dir / "older.mp4"
    older.write_bytes(b"\x00")
    os.utime(older, (now - 7200, now - 7200))

    results = scan_for_videos(tmp_watch_dir, (".mp4",), THRESHOLD)
    assert results[0].path == older
    assert results[1].path == newer


def test_empty_directory(tmp_watch_dir: Path) -> None:
    tmp_watch_dir.mkdir(parents=True, exist_ok=True)
    assert scan_for_videos(tmp_watch_dir, (".mp4",), THRESHOLD) == []


def test_multiple_extensions(tmp_watch_dir: Path) -> None:
    tmp_watch_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for name in ("a.mp4", "b.mkv", "c.txt"):
        p = tmp_watch_dir / name
        p.write_bytes(b"\x00")
        os.utime(p, (now - 7200, now - 7200))

    results = scan_for_videos(tmp_watch_dir, (".mp4", ".mkv"), THRESHOLD)
    names = {r.path.name for r in results}
    assert "a.mp4" in names
    assert "b.mkv" in names
    assert "c.txt" not in names
