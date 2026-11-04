"""Shared pytest fixtures for YoutubeUploaderV2 tests."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.models import AppConfig, VideoFile
from datetime import datetime


@pytest.fixture
def tmp_watch_dir(tmp_path: Path) -> Path:
    return tmp_path / "recordings"


@pytest.fixture
def old_video(tmp_watch_dir: Path) -> Path:
    """An .mp4 file that is older than the recency threshold."""
    tmp_watch_dir.mkdir(parents=True, exist_ok=True)
    p = tmp_watch_dir / "2024-01-15 14-30-00.mp4"
    p.write_bytes(b"\x00" * 1024)
    # Set mtime to 2 hours ago
    old_time = time.time() - 7200
    import os
    os.utime(p, (old_time, old_time))
    return p


@pytest.fixture
def recent_video(tmp_watch_dir: Path) -> Path:
    """An .mp4 file modified within the last minute (still being written)."""
    tmp_watch_dir.mkdir(parents=True, exist_ok=True)
    p = tmp_watch_dir / "2024-01-15 15-00-00.mp4"
    p.write_bytes(b"\x00" * 1024)
    return p


@pytest.fixture
def sample_video_file(old_video: Path) -> VideoFile:
    stat = old_video.stat()
    return VideoFile(
        path=old_video,
        size_bytes=stat.st_size,
        modified_time=datetime.fromtimestamp(stat.st_mtime),
    )


@pytest.fixture
def sample_config(tmp_watch_dir: Path) -> AppConfig:
    tmp_watch_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        watch_directory=tmp_watch_dir,
        poll_interval_hours=3,
        max_daily_uploads=6,
        uploads_per_run=2,
        chunk_size_mb=10,
        recent_write_threshold_minutes=30,
        delete_after_upload=True,
        privacy_status="private",
        video_extensions=(".mp4",),
        max_duration_hours=11.5,
        processing_timeout_minutes=1,  # fast for tests
        client_id="test-client-id",
        client_secret="test-client-secret",
        project_id="test-project",
    )


@pytest.fixture
def tmp_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_path = tmp_path / "upload_state.json"
    import src.queue_manager as qm
    monkeypatch.setattr(qm, "STATE_PATH", state_path)
    return state_path


@pytest.fixture
def mock_youtube_service() -> MagicMock:
    service = MagicMock()
    insert_mock = MagicMock()
    insert_mock.next_chunk.return_value = (None, {"id": "test-video-id-123"})
    service.videos.return_value.insert.return_value = insert_mock
    return service
