"""Post-upload file cleanup — deletes or moves local video files."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from src import queue_manager as qm

log = logging.getLogger(__name__)

# On Windows, the MediaFileUpload file handle may still be open briefly after
# upload_video() returns. Retry deletion a few times before giving up.
_DELETE_RETRIES = 5
_DELETE_RETRY_DELAY = 2.0  # seconds


def move_to_subfolder(file_path: Path, subfolder_name: str) -> tuple[bool, Path | None]:
    """Move a video file to a subfolder within its parent directory.

    Creates the subfolder if it doesn't exist. If a file with the same name
    already exists in the destination, appends a numeric suffix.

    Returns (success, destination_path). Never raises.
    """
    dest_dir = file_path.parent / subfolder_name
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("Failed to create directory %s: %s", dest_dir, exc)
        return False, None

    dest_path = dest_dir / file_path.name
    # Handle name collisions
    if dest_path.exists():
        stem = file_path.stem
        suffix = file_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{stem} ({counter}){suffix}"
            counter += 1

    try:
        size_gb = file_path.stat().st_size / (1024 ** 3)
        shutil.move(str(file_path), str(dest_path))
        log.info("Moved %s -> %s (%.1f GB)", file_path.name, dest_path, size_gb)
        return True, dest_path
    except OSError as exc:
        log.error("Failed to move %s to %s: %s", file_path.name, dest_path, exc)
        return False, None


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

    last_exc: OSError | None = None
    for attempt in range(1, _DELETE_RETRIES + 1):
        try:
            file_path.unlink()
            log.info(
                "Deleted local file: %s (%.1f MB freed)",
                file_path.name,
                size_mb,
            )
            new_state = qm.mark_deleted(state, file_path)
            return True, new_state
        except OSError as exc:
            last_exc = exc
            if attempt < _DELETE_RETRIES:
                log.warning(
                    "Delete attempt %d/%d failed for %s (%s) — retrying in %.0fs",
                    attempt,
                    _DELETE_RETRIES,
                    file_path.name,
                    exc,
                    _DELETE_RETRY_DELAY,
                )
                time.sleep(_DELETE_RETRY_DELAY)

    log.error(
        "Failed to delete %s after %d attempts: %s",
        file_path.name,
        _DELETE_RETRIES,
        last_exc,
    )
    return False, state
