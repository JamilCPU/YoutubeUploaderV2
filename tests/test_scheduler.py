"""Integration tests for src/scheduler.py run_once()."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.queue_manager as qm
from src.scheduler import run_once


def _setup_service_for_upload() -> MagicMock:
    service = MagicMock()
    insert_mock = MagicMock()
    insert_mock.next_chunk.return_value = (None, {"id": "test-id-999"})
    service.videos.return_value.insert.return_value = insert_mock
    # Mock the videos().list() call used by processing verification
    list_mock = MagicMock()
    list_mock.execute.return_value = {
        "items": [{"status": {"uploadStatus": "processed"}}]
    }
    service.videos.return_value.list.return_value = list_mock
    return service


def test_run_once_uploads_old_video(
    tmp_watch_dir: Path,
    old_video: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    service = _setup_service_for_upload()
    summary = run_once(sample_config, service)
    assert summary.uploaded == 1


def test_run_once_skips_already_uploaded(
    tmp_watch_dir: Path,
    old_video: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    # Pre-populate state as uploaded
    state = qm.load_state()
    state = qm.mark_uploading(state, old_video)
    state = qm.mark_uploaded(state, old_video, "existing-id", "Title")
    qm.save_state(state)

    service = _setup_service_for_upload()
    summary = run_once(sample_config, service)
    assert summary.uploaded == 0
    service.videos.return_value.insert.assert_not_called()


def test_run_once_respects_quota(
    tmp_watch_dir: Path,
    old_video: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    # Exhaust the daily quota
    state = qm.load_state()
    for i in range(sample_config.max_daily_uploads):
        p = Path(f"/dummy{i}.mp4")
        state = qm.mark_uploading(state, p)
        state = qm.mark_uploaded(state, p, f"id{i}", f"T{i}")
    qm.save_state(state)

    service = _setup_service_for_upload()
    summary = run_once(sample_config, service)
    assert summary.uploaded == 0
    service.videos.return_value.insert.assert_not_called()


def test_run_once_deletes_file_after_upload(
    tmp_watch_dir: Path,
    old_video: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    service = _setup_service_for_upload()
    assert old_video.exists()
    run_once(sample_config, service)
    assert not old_video.exists()


def test_run_once_no_videos(
    tmp_watch_dir: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    service = _setup_service_for_upload()
    summary = run_once(sample_config, service)
    assert summary.uploaded == 0
    service.videos.return_value.insert.assert_not_called()


def test_run_once_too_long_moves_to_subfolder(
    tmp_watch_dir: Path,
    old_video: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    """Videos exceeding max_duration_hours are moved to too_long/ and not uploaded."""
    service = _setup_service_for_upload()

    with patch("src.scheduler.get_duration_hours", return_value=14.0):
        summary = run_once(sample_config, service)

    assert summary.failed == 1
    assert summary.uploaded == 0
    service.videos.return_value.insert.assert_not_called()
    # Original should be moved
    assert not old_video.exists()
    assert (tmp_watch_dir / "too_long" / old_video.name).exists()


def test_run_once_processing_rejected_moves_to_subfolder(
    tmp_watch_dir: Path,
    old_video: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    """If YouTube rejects a video, it should be moved to processing_failed/."""
    service = MagicMock()
    insert_mock = MagicMock()
    insert_mock.next_chunk.return_value = (None, {"id": "test-id-999"})
    service.videos.return_value.insert.return_value = insert_mock
    # Return rejected status
    list_mock = MagicMock()
    list_mock.execute.return_value = {
        "items": [{"status": {"uploadStatus": "rejected", "rejectionReason": "tooLong"}}]
    }
    service.videos.return_value.list.return_value = list_mock

    summary = run_once(sample_config, service)
    assert summary.uploaded == 1
    # File should be moved to processing_failed/
    assert not old_video.exists()
    assert (tmp_watch_dir / "processing_failed" / old_video.name).exists()


def test_run_once_processing_failed_moves_to_subfolder(
    tmp_watch_dir: Path,
    old_video: Path,
    sample_config,
    tmp_state_file: Path,
) -> None:
    """If YouTube processing fails, video should be moved to processing_failed/."""
    service = MagicMock()
    insert_mock = MagicMock()
    insert_mock.next_chunk.return_value = (None, {"id": "test-id-999"})
    service.videos.return_value.insert.return_value = insert_mock
    list_mock = MagicMock()
    list_mock.execute.return_value = {
        "items": [{"status": {"uploadStatus": "failed", "failureReason": "codec"}}]
    }
    service.videos.return_value.list.return_value = list_mock

    summary = run_once(sample_config, service)
    assert summary.uploaded == 1
    assert not old_video.exists()
    assert (tmp_watch_dir / "processing_failed" / old_video.name).exists()
