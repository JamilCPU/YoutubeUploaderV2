"""Tests for src/queue_manager.py."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import src.queue_manager as qm


def test_load_state_empty(tmp_state_file: Path) -> None:
    state = qm.load_state()
    assert state == {"uploads": {}, "daily_counts": {}}


def test_load_state_corrupt(tmp_state_file: Path) -> None:
    tmp_state_file.write_text("not valid json{{")
    state = qm.load_state()
    assert state == {"uploads": {}, "daily_counts": {}}


def test_mark_uploading(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/video.mp4")
    state = qm.mark_uploading(state, p)
    assert qm.get_file_status(state, p) == qm.STATUS_UPLOADING


def test_mark_uploaded(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/video.mp4")
    state = qm.mark_uploading(state, p)
    state = qm.mark_uploaded(state, p, "vid123", "My Title")
    assert qm.get_file_status(state, p) == qm.STATUS_UPLOADED
    assert state["uploads"][str(p)]["video_id"] == "vid123"


def test_mark_failed(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/video.mp4")
    state = qm.mark_failed(state, p, "Network error")
    assert qm.get_file_status(state, p) == qm.STATUS_FAILED
    assert "Network error" in state["uploads"][str(p)]["error"]


def test_mark_deleted(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/video.mp4")
    state = qm.mark_uploading(state, p)
    state = qm.mark_uploaded(state, p, "vid123", "Title")
    state = qm.mark_deleted(state, p)
    assert qm.get_file_status(state, p) == qm.STATUS_DELETED


def test_daily_quota_tracking(tmp_state_file: Path) -> None:
    state = qm.load_state()
    assert qm.daily_uploads_remaining(state, 6) == 6

    p = Path("/some/video.mp4")
    state = qm.mark_uploading(state, p)
    state = qm.mark_uploaded(state, p, "v1", "T1")
    assert qm.daily_uploads_remaining(state, 6) == 5


def test_daily_quota_exhausted(tmp_state_file: Path) -> None:
    state = qm.load_state()
    for i in range(6):
        p = Path(f"/video{i}.mp4")
        state = qm.mark_uploading(state, p)
        state = qm.mark_uploaded(state, p, f"id{i}", f"T{i}")
    assert qm.daily_uploads_remaining(state, 6) == 0


def test_is_already_processed_uploaded(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/video.mp4")
    state = qm.mark_uploading(state, p)
    state = qm.mark_uploaded(state, p, "vid", "Title")
    assert qm.is_already_processed(state, p) is True


def test_is_already_processed_uploading(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/video.mp4")
    state = qm.mark_uploading(state, p)
    assert qm.is_already_processed(state, p) is True


def test_is_already_processed_pending(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/video.mp4")
    assert qm.is_already_processed(state, p) is False


def test_reset_failed(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/video.mp4")
    state = qm.mark_failed(state, p, "err")
    state = qm.reset_failed(state)
    assert qm.get_file_status(state, p) == qm.STATUS_PENDING


def test_reset_failed_clears_possibly_uploaded(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/video.mp4")
    state = qm.mark_failed(state, p, "err")
    # Manually set possibly_uploaded flag (as stuck detector would)
    state["uploads"][str(p)]["possibly_uploaded"] = True
    assert qm.is_already_processed(state, p) is True  # blocked before reset
    state = qm.reset_failed(state)
    assert state["uploads"][str(p)].get("possibly_uploaded") is None
    assert qm.is_already_processed(state, p) is False  # allowed after reset


def test_possibly_uploaded_blocks_retry(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/video.mp4")
    state = qm.mark_failed(state, p, "timed out")
    state["uploads"][str(p)]["possibly_uploaded"] = True
    # Must not be retried automatically even though status is FAILED
    assert qm.is_already_processed(state, p) is True


def test_save_and_reload(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/video.mp4")
    state = qm.mark_uploading(state, p)
    state = qm.mark_uploaded(state, p, "vid", "Title")
    qm.save_state(state)

    reloaded = qm.load_state()
    assert qm.get_file_status(reloaded, p) == qm.STATUS_UPLOADED


def test_stuck_upload_recovery(tmp_path: Path, tmp_state_file: Path) -> None:
    # Manually write a state with a stuck UPLOADING entry (started 25 hours ago)
    stuck_path = tmp_path / "stuck.mp4"
    stuck_key = str(stuck_path)
    stuck_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    state_data = {
        "uploads": {
            stuck_key: {
                "status": qm.STATUS_UPLOADING,
                "started_at": stuck_time,
            }
        },
        "daily_counts": {},
    }
    tmp_state_file.write_text(json.dumps(state_data))
    state = qm.load_state()
    assert qm.get_file_status(state, stuck_path) == qm.STATUS_FAILED
    assert state["uploads"][stuck_key].get("possibly_uploaded") is True
    # Must not be auto-retried even though status is FAILED
    assert qm.is_already_processed(state, stuck_path) is True


# ---------------------------------------------------------------------------
# mark_too_long / mark_processing_failed / is_already_processed extensions
# ---------------------------------------------------------------------------


def test_mark_too_long(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/long_video.mp4")
    state = qm.mark_too_long(state, p, 13.5)

    assert qm.get_file_status(state, p) == qm.STATUS_TOO_LONG
    entry = state["uploads"][str(p)]
    assert entry["duration_hours"] == 13.5
    assert "13.5" in entry["error"]


def test_mark_processing_failed(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/bad_video.mp4")
    state = qm.mark_processing_failed(state, p, "vid999", "codec not supported")

    assert qm.get_file_status(state, p) == qm.STATUS_PROCESSING_FAILED
    entry = state["uploads"][str(p)]
    assert entry["video_id"] == "vid999"
    assert entry["error"] == "codec not supported"


def test_is_already_processed_too_long(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/long_video.mp4")
    state = qm.mark_too_long(state, p, 14.0)

    assert qm.is_already_processed(state, p) is True


def test_is_already_processed_processing_failed(tmp_state_file: Path) -> None:
    state = qm.load_state()
    p = Path("/some/bad_video.mp4")
    state = qm.mark_processing_failed(state, p, "vid888", "rejected")

    assert qm.is_already_processed(state, p) is True
