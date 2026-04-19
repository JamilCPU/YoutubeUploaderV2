"""Tests for src/uploader.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from src.models import AppConfig
from src.uploader import _title_from_filename, upload_video


def _make_http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=b"Server Error")


def test_upload_success(sample_video_file, sample_config: AppConfig) -> None:
    service = MagicMock()
    insert_mock = MagicMock()
    # Simulate: first call returns (status, None), second returns (None, response)
    insert_mock.next_chunk.side_effect = [
        (MagicMock(resumable_progress=512), None),
        (None, {"id": "abc123"}),
    ]
    service.videos.return_value.insert.return_value = insert_mock

    result = upload_video(service, sample_video_file, sample_config)

    assert result.success is True
    assert result.video_id == "abc123"
    assert result.file_path == sample_video_file.path


def test_upload_retries_on_retriable_error(sample_video_file, sample_config: AppConfig) -> None:
    service = MagicMock()
    insert_mock = MagicMock()
    insert_mock.next_chunk.side_effect = [
        _make_http_error(503),
        (None, {"id": "retried-id"}),
    ]
    service.videos.return_value.insert.return_value = insert_mock

    with patch("src.uploader.time.sleep"):  # Don't actually sleep in tests
        result = upload_video(service, sample_video_file, sample_config)

    assert result.success is True
    assert result.video_id == "retried-id"


def test_upload_fails_on_non_retriable_error(sample_video_file, sample_config: AppConfig) -> None:
    service = MagicMock()
    insert_mock = MagicMock()
    insert_mock.next_chunk.side_effect = _make_http_error(403)
    service.videos.return_value.insert.return_value = insert_mock

    result = upload_video(service, sample_video_file, sample_config)

    assert result.success is False
    assert result.video_id == ""
    assert "403" in result.error_message or len(result.error_message) > 0


def test_upload_fails_after_max_retries(sample_video_file, sample_config: AppConfig) -> None:
    service = MagicMock()
    insert_mock = MagicMock()
    insert_mock.next_chunk.side_effect = _make_http_error(500)
    service.videos.return_value.insert.return_value = insert_mock

    with patch("src.uploader.time.sleep"):
        result = upload_video(service, sample_video_file, sample_config)

    assert result.success is False


def test_title_from_standard_filename() -> None:
    path = Path("2024-01-15 14-30-00.mp4")
    assert _title_from_filename(path) == "01/15/2024 - 02:30 PM"


def test_title_from_underscore_filename() -> None:
    path = Path("2024-01-15_14-30-00.mp4")
    assert _title_from_filename(path) == "01/15/2024 - 02:30 PM"


def test_title_fallback_to_stem() -> None:
    path = Path("my-custom-recording.mp4")
    assert _title_from_filename(path) == "my-custom-recording"


def test_upload_calls_progress_callback(sample_video_file, sample_config: AppConfig) -> None:
    service = MagicMock()
    insert_mock = MagicMock()
    status_mock = MagicMock()
    status_mock.resumable_progress = 512
    insert_mock.next_chunk.side_effect = [
        (status_mock, None),
        (None, {"id": "vid-id"}),
    ]
    service.videos.return_value.insert.return_value = insert_mock

    callback_calls = []

    def callback(progress):
        callback_calls.append(progress)

    upload_video(service, sample_video_file, sample_config, progress_callback=callback)
    assert len(callback_calls) >= 1
