"""Directory scanner — finds video files ready to upload."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from src.models import VideoFile

log = logging.getLogger(__name__)


def scan_for_videos(
    watch_dir: Path,
    extensions: tuple[str, ...],
    recency_threshold_seconds: int,
) -> list[VideoFile]:
    """Scan *watch_dir* for video files that are ready to upload.

    A file is considered ready if it has not been modified within
    *recency_threshold_seconds* (i.e., OBS has finished writing it).

    Returns a list of VideoFile sorted oldest-first by modification time.
    """
    now = time.time()
    candidates: list[VideoFile] = []

    for ext in extensions:
        for path in watch_dir.glob(f"*{ext}"):
            if not path.is_file():
                continue

            try:
                stat = path.stat()
            except OSError as exc:
                log.warning("Cannot stat %s: %s — skipping", path, exc)
                continue

            age_seconds = now - stat.st_mtime
            if age_seconds < recency_threshold_seconds:
                log.debug(
                    "Skipping %s — modified %.0f seconds ago (threshold: %ds)",
                    path.name,
                    age_seconds,
                    recency_threshold_seconds,
                )
                continue

            candidates.append(
                VideoFile(
                    path=path,
                    size_bytes=stat.st_size,
                    modified_time=datetime.fromtimestamp(stat.st_mtime),
                )
            )

    candidates.sort(key=lambda v: v.modified_time)
    log.info("Found %d video(s) ready to upload in %s", len(candidates), watch_dir)
    return candidates
