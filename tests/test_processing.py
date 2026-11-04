"""Tests for src/processing.py."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from src.processing import (
    ProcessingResult,
    check_processing_status,
    wait_for_processing,
)


def _make_service(response: dict) -> MagicMock:
    """Build a mock YouTube service that returns *response* from videos().list().execute()."""
    service = MagicMock()
    service.videos.return_value.list.return_value.execute.return_value = response
    return service


def _make_http_error(status: int = 403) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=b"API Error")


# ---------------------------------------------------------------------------
# check_processing_status
# ---------------------------------------------------------------------------


def test_check_processing_status_processed() -> None:
    service = _make_service(
        {
            "items": [
                {
                    "status": {"uploadStatus": "processed"},
                    "processingDetails": {},
                }
            ]
        }
    )
    result = check_processing_status(service, "vid123")

    assert result.video_id == "vid123"
    assert result.status == "processed"
    assert result.upload_status == "processed"


def test_check_processing_status_rejected() -> None:
    service = _make_service(
        {
            "items": [
                {
                    "status": {
                        "uploadStatus": "rejected",
                        "rejectionReason": "tooLong",
                    },
                    "processingDetails": {},
                }
            ]
        }
    )
    result = check_processing_status(service, "vid123")

    assert result.status == "rejected"
    assert result.upload_status == "rejected"
    assert result.rejection_reason == "tooLong"


def test_check_processing_status_failed() -> None:
    service = _make_service(
        {
            "items": [
                {
                    "status": {
                        "uploadStatus": "failed",
                        "failureReason": "codec",
                    },
                    "processingDetails": {},
                }
            ]
        }
    )
    result = check_processing_status(service, "vid123")

    assert result.status == "failed"
    assert result.upload_status == "failed"
    assert result.failure_reason == "codec"


def test_check_processing_status_still_processing() -> None:
    service = _make_service(
        {
            "items": [
                {
                    "status": {"uploadStatus": "uploaded"},
                    "processingDetails": {},
                }
            ]
        }
    )
    result = check_processing_status(service, "vid123")

    assert result.status == "processing"
    assert result.upload_status == "uploaded"


def test_check_processing_status_api_error() -> None:
    service = MagicMock()
    service.videos.return_value.list.return_value.execute.side_effect = (
        _make_http_error(403)
    )

    result = check_processing_status(service, "vid123")

    assert result.status == "unknown"
    assert result.video_id == "vid123"
    assert len(result.failure_reason) > 0


def test_check_processing_status_empty_items() -> None:
    service = _make_service({"items": []})

    result = check_processing_status(service, "vid123")

    assert result.status == "unknown"
    assert result.video_id == "vid123"


# ---------------------------------------------------------------------------
# wait_for_processing
# ---------------------------------------------------------------------------


def test_wait_for_processing_immediate_success() -> None:
    processed = ProcessingResult(
        video_id="vid123", status="processed", upload_status="processed"
    )

    with patch(
        "src.processing.check_processing_status", return_value=processed
    ) as mock_check:
        result = wait_for_processing(
            MagicMock(), "vid123", timeout_minutes=5, shutdown_flag=False
        )

    assert result.status == "processed"
    mock_check.assert_called_once()


def test_wait_for_processing_eventual_success() -> None:
    processing = ProcessingResult(
        video_id="vid123", status="processing", upload_status="uploaded"
    )
    processed = ProcessingResult(
        video_id="vid123", status="processed", upload_status="processed"
    )

    with (
        patch(
            "src.processing.check_processing_status",
            side_effect=[processing, processed],
        ),
        patch("src.processing.time.sleep"),
        patch("src.processing.time.monotonic") as mock_monotonic,
    ):
        # Timeline: start=0, first check=0, after sleep=1, second check=1
        # deadline = 0 + 5*60 = 300; always under deadline
        mock_monotonic.side_effect = [
            0,     # deadline calculation: time.monotonic() + timeout*60
            0,     # first loop: check_processing_status elapsed calc
            0,     # first loop: deadline check (time.monotonic() >= deadline)
            0,     # sleep_end calculation
            300,   # inner sleep loop: time.monotonic() < sleep_end -> exit
            1,     # second iteration: _is_shutdown check uses monotonic indirectly? No.
            1,     # second loop: elapsed calc
            1,     # second loop: deadline check (not needed, returns processed)
        ]

        result = wait_for_processing(
            MagicMock(), "vid123", timeout_minutes=5, shutdown_flag=False
        )

    assert result.status == "processed"


def test_wait_for_processing_timeout() -> None:
    processing = ProcessingResult(
        video_id="vid123", status="processing", upload_status="uploaded"
    )

    with (
        patch(
            "src.processing.check_processing_status", return_value=processing
        ),
        patch("src.processing.time.sleep"),
        patch("src.processing.time.monotonic") as mock_monotonic,
    ):
        # timeout_minutes=0 means deadline = start + 0 = start
        # First call: deadline = monotonic() + 0 = 100
        # Second call (elapsed calc): 100
        # Third call (deadline check): 100 >= 100 -> timeout
        mock_monotonic.side_effect = [
            100,   # deadline = 100 + 0*60 = 100
            100,   # elapsed calculation
            100,   # deadline check: 100 >= 100 -> True, return stuck
        ]

        result = wait_for_processing(
            MagicMock(), "vid123", timeout_minutes=0, shutdown_flag=False
        )

    assert result.status == "stuck"
    assert result.video_id == "vid123"
