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

# Files stuck in UPLOADING for longer than this are reset to FAILED
_STUCK_UPLOAD_TIMEOUT_SECONDS = 2 * 60 * 60  # 2 hours


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
    """Reset all FAILED uploads back to PENDING."""
    updated_uploads = {}
    for key, entry in state["uploads"].items():
        if entry.get("status") == STATUS_FAILED:
            updated_uploads[key] = {**entry, "status": STATUS_PENDING}
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
    }
    for entry in state["uploads"].values():
        status = entry.get("status", STATUS_PENDING)
        counts[status] = counts.get(status, 0) + 1
    return counts


def is_already_processed(state: dict, file_path: Path) -> bool:
    status = get_file_status(state, file_path)
    return status in (STATUS_UPLOADED, STATUS_DELETED)


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
                "Upload for %s has been stuck in UPLOADING for >2h — resetting to FAILED",
                Path(key).name,
            )
            entry["status"] = STATUS_FAILED
            entry["error"] = "Upload timed out (stuck detection)"
