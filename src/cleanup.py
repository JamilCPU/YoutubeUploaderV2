"""Post-upload file cleanup — deletes local video files after confirmed upload."""

from __future__ import annotations

import logging
from pathlib import Path

from src import queue_manager as qm

log = logging.getLogger(__name__)


def delete_uploaded_video(file_path: Path, state: dict) -> tuple[bool, dict]:
    """Delete a local video file that has been successfully uploaded.

    Safety checks:
    - File must have UPLOADED status in state.
    - File must still exist on disk.

    Returns (success, updated_state). Never raises.
    """
    status = qm.get_file_status(state, file_path)
    if status != qm.STATUS_UPLOADED:
        log.error(
            "Refusing to delete %s — expected status UPLOADED, got %s",
            file_path.name,
            status,
        )
        return False, state

    if not file_path.exists():
        log.warning("File already gone from disk: %s — marking as deleted", file_path.name)
        new_state = qm.mark_deleted(state, file_path)
        return True, new_state

    size_mb = file_path.stat().st_size / (1024 * 1024)
    try:
        file_path.unlink()
    except OSError as exc:
        log.error("Failed to delete %s: %s", file_path.name, exc)
        return False, state

    log.info("Deleted local file: %s (%.1f MB freed)", file_path.name, size_mb)
    new_state = qm.mark_deleted(state, file_path)
    return True, new_state
