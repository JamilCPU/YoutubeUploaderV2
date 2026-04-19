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
