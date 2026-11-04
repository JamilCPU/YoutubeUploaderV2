"""Upload state persistence and daily quota tracking."""

from __future__ import annotations

import json
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

STATE_PATH = Path("upload_state.json")

# Status values
STATUS_PENDING = "pending"
STATUS_UPLOADING = "uploading"
STATUS_UPLOADED = "uploaded"
STATUS_FAILED = "failed"
STATUS_DELETED = "deleted"
STATUS_TOO_LONG = "too_long"
STATUS_PROCESSING_FAILED = "processing_failed"

# Files stuck in UPLOADING for longer than this are reset to FAILED.
# Set to 24h so even very large files (48+ GB at ~2.5 MB/s = ~5.5h) are not
# prematurely timed out, which would cause them to be re-uploaded as duplicates.
_STUCK_UPLOAD_TIMEOUT_SECONDS = 24 * 60 * 60  # 24 hours


def load_state() -> dict:
    """Load state from disk, returning an empty state dict if not found."""
    if not STATE_PATH.exists():
        return _empty_state()
    try:
        data = json.loads(STATE_PATH.read_text())
        _recover_stuck_uploads(data)
        return data
    except (json.JSONDecodeError, KeyError) as exc:
        log.warning("Corrupt state file (%s) — starting fresh", exc)
        return _empty_state()


def save_state(state: dict) -> None:
    """Atomically write state to disk."""
    content = json.dumps(state, indent=2, default=str)
    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=STATE_PATH.parent,
        delete=False,
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    tmp_path.replace(STATE_PATH)


def mark_uploading(state: dict, file_path: Path) -> dict:
    key = str(file_path)
    return {
        **state,
        "uploads": {
            **state["uploads"],
            key: {
                "status": STATUS_UPLOADING,
                "started_at": datetime.utcnow().isoformat(),
                "video_id": None,
                "error": None,
            },
        },
    }


def mark_uploaded(state: dict, file_path: Path, video_id: str, title: str) -> dict:
    key = str(file_path)
    today = _today_key()
    daily_counts = dict(state.get("daily_counts", {}))
    daily_counts[today] = daily_counts.get(today, 0) + 1

    return {
        **state,
        "daily_counts": daily_counts,
        "uploads": {
            **state["uploads"],
            key: {
                "status": STATUS_UPLOADED,
                "completed_at": datetime.utcnow().isoformat(),
                "video_id": video_id,
                "title": title,
                "error": None,
            },
        },
    }


def mark_failed(state: dict, file_path: Path, error: str) -> dict:
    key = str(file_path)
    return {
        **state,
        "uploads": {
            **state["uploads"],
            key: {
                "status": STATUS_FAILED,
                "failed_at": datetime.utcnow().isoformat(),
                "video_id": None,
                "error": error,
            },
        },
    }


def mark_too_long(state: dict, file_path: Path, duration_hours: float | None) -> dict:
    key = str(file_path)
    return {
        **state,
        "uploads": {
            **state["uploads"],
            key: {
                "status": STATUS_TOO_LONG,
                "detected_at": datetime.utcnow().isoformat(),
                "duration_hours": duration_hours,
                "error": f"Video duration ({duration_hours:.1f}h) exceeds maximum"
                if duration_hours is not None
                else "Video duration exceeds maximum (unknown duration)",
            },
        },
    }


def mark_processing_failed(
    state: dict, file_path: Path, video_id: str | None, reason: str
) -> dict:
    key = str(file_path)
    return {
        **state,
        "uploads": {
            **state["uploads"],
            key: {
                "status": STATUS_PROCESSING_FAILED,
                "failed_at": datetime.utcnow().isoformat(),
                "video_id": video_id,
                "error": reason,
            },
        },
    }


def mark_deleted(state: dict, file_path: Path) -> dict:
    key = str(file_path)
    entry = dict(state["uploads"].get(key, {}))
    entry["status"] = STATUS_DELETED
    entry["deleted_at"] = datetime.utcnow().isoformat()
    return {
        **state,
        "uploads": {**state["uploads"], key: entry},
    }


def reset_failed(state: dict) -> dict:
    """Reset all FAILED uploads back to PENDING.

    Also clears the possibly_uploaded flag so that files which were timed out
    by the stuck detector can be re-tried after the operator has verified they
    are not already on YouTube.
    """
    updated_uploads = {}
    for key, entry in state["uploads"].items():
        if entry.get("status") == STATUS_FAILED:
            new_entry = {**entry, "status": STATUS_PENDING}
            new_entry.pop("possibly_uploaded", None)
            updated_uploads[key] = new_entry
        else:
            updated_uploads[key] = entry
    return {**state, "uploads": updated_uploads}


def get_file_status(state: dict, file_path: Path) -> str | None:
    return state["uploads"].get(str(file_path), {}).get("status")


def daily_uploads_remaining(state: dict, max_daily: int) -> int:
    today = _today_key()
    used = state.get("daily_counts", {}).get(today, 0)
    return max(0, max_daily - used)


def get_queue_summary(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {
        STATUS_PENDING: 0,
        STATUS_UPLOADING: 0,
        STATUS_UPLOADED: 0,
        STATUS_FAILED: 0,
        STATUS_DELETED: 0,
        STATUS_TOO_LONG: 0,
        STATUS_PROCESSING_FAILED: 0,
    }
    for entry in state["uploads"].values():
        status = entry.get("status", STATUS_PENDING)
        counts[status] = counts.get(status, 0) + 1
    return counts


def is_already_processed(state: dict, file_path: Path) -> bool:
    """Return True if the file should be skipped this run.

    Files in UPLOADING state are skipped — they are either actively being
    uploaded by another process, or they are stuck and will be reset to FAILED
    by the stuck-upload recovery on the next load_state() call (after 24h).

    Files with possibly_uploaded=True are also skipped — these were reset from
    UPLOADING to FAILED by the stuck detector, but may have actually completed
    on YouTube's side. Auto-retrying them would create duplicate videos. The
    operator must explicitly call reset_failed() (or delete the state entry)
    after verifying the video's status on YouTube.
    """
    entry = state["uploads"].get(str(file_path), {})
    status = entry.get("status")
    if status in (
        STATUS_UPLOADING,
        STATUS_UPLOADED,
        STATUS_DELETED,
        STATUS_TOO_LONG,
        STATUS_PROCESSING_FAILED,
    ):
        return True
    if entry.get("possibly_uploaded"):
        return True
    return False


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _empty_state() -> dict:
    return {"uploads": {}, "daily_counts": {}}


def _recover_stuck_uploads(state: dict) -> None:
    """Reset uploads stuck in UPLOADING state for too long."""
    now = time.time()
    for key, entry in state["uploads"].items():
        if entry.get("status") != STATUS_UPLOADING:
            continue
        started_at_str = entry.get("started_at")
        if not started_at_str:
            continue
        try:
            # Treat the stored ISO string as UTC (it was written by utcnow())
            started_at = (
                datetime.fromisoformat(started_at_str)
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except ValueError:
            continue

        if now - started_at > _STUCK_UPLOAD_TIMEOUT_SECONDS:
            log.warning(
                "Upload for %s has been stuck in UPLOADING for >24h - resetting to FAILED. "
                "possibly_uploaded=True set: verify on YouTube before calling reset_failed().",
                Path(key).name,
            )
            entry["status"] = STATUS_FAILED
            entry["error"] = "Upload timed out (stuck detection)"
            # Mark as possibly already on YouTube so auto-retry is blocked.
            # The operator must verify on YouTube and then call reset_failed()
            # (or py -m src reset-failed) before the file will be re-uploaded.
            entry["possibly_uploaded"] = True
