"""Resumable YouTube upload engine."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from src.models import AppConfig, UploadProgress, UploadResult, VideoFile
from src.progress import ProgressCallback, UploadProgressTracker

log = logging.getLogger(__name__)

# HTTP status codes that are safe to retry
_RETRIABLE_STATUS_CODES = {500, 502, 503, 504}
_MAX_RETRIES = 5
_BACKOFF_BASE = 2.0  # seconds


def upload_video(
    service,
    video_file: VideoFile,
    config: AppConfig,
    progress_callback: ProgressCallback | None = None,
) -> UploadResult:
    """Upload a single video file to YouTube using resumable uploads.

    Returns an UploadResult indicating success or failure. Never raises.
    """
    log.info(
        "Starting upload: %s (%.2f GB)",
        video_file.name,
        video_file.size_gb,
    )

    title = _title_from_filename(video_file.path)
    body = {
        "snippet": {
            "title": title,
            "description": "",
            "tags": [],
            "categoryId": "22",  # People & Blogs
        },
        "status": {
            "privacyStatus": config.privacy_status,
        },
    }

    media = MediaFileUpload(
        str(video_file.path),
        chunksize=config.chunk_size_bytes,
        resumable=True,
        mimetype="video/*",
    )

    try:
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        video_id = _execute_resumable(request, video_file, progress_callback)
    except Exception as exc:
        log.error("Upload failed for %s: %s", video_file.name, exc)
        return UploadResult(
            file_path=video_file.path,
            success=False,
            error_message=str(exc),
        )
    finally:
        # Close the file handle so the OS (especially Windows) can delete the file
        try:
            media._fd.close()
        except Exception:
            pass

    log.info(
        "Upload complete: %s -> https://www.youtube.com/watch?v=%s",
        video_file.name,
        video_id,
    )
    return UploadResult(
        file_path=video_file.path,
        success=True,
        video_id=video_id,
        title=title,
        uploaded_at=datetime.utcnow(),
    )


def _execute_resumable(
    request,
    video_file: VideoFile,
    progress_callback: ProgressCallback | None,
) -> str:
    """Drive the resumable upload loop with exponential backoff retries.

    Returns the YouTube video ID on success. Raises on unrecoverable failure.
    """
    tracker = UploadProgressTracker(video_file.size_bytes, video_file.name)
    retries = 0
    response = None

    while response is None:
        try:
            status, response = request.next_chunk()
        except HttpError as exc:
            if exc.resp.status in _RETRIABLE_STATUS_CODES and retries < _MAX_RETRIES:
                retries += 1
                wait = _BACKOFF_BASE ** retries
                log.warning(
                    "HTTP %s — retry %d/%d in %.0fs",
                    exc.resp.status,
                    retries,
                    _MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue
            raise

        if status is not None and progress_callback is not None:
            progress = tracker.update(status.resumable_progress)
            progress_callback(progress)

        # Reset retry counter on successful chunk
        retries = 0

    if "id" not in response:
        raise ValueError(f"Upload response missing video ID: {response}")

    return response["id"]


def _title_from_filename(path: Path) -> str:
    """Convert a filename like '2024-01-15 14-30-00.mp4' to '01/15/2024 - 2:30 PM'.

    Falls back to the stem (filename without extension) if parsing fails.
    """
    stem = path.stem
    try:
        dt = datetime.strptime(stem, "%Y-%m-%d %H-%M-%S")
        return dt.strftime("%m/%d/%Y - %I:%M %p")
    except ValueError:
        pass

    try:
        dt = datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S")
        return dt.strftime("%m/%d/%Y - %I:%M %p")
    except ValueError:
        pass

    return stem
