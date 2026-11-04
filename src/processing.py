"""YouTube video processing status verification.

Polls the YouTube Data API v3 to confirm a video has been fully processed
before allowing local file deletion.  YouTube can stall on "processing will
begin shortly" or reject with "video too long" — this module guards against
premature cleanup in both cases.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from googleapiclient.errors import HttpError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingResult:
    """Immutable snapshot of a video's processing state."""

    video_id: str
    status: str  # "processed", "failed", "rejected", "stuck", "processing", "unknown"
    upload_status: str = ""  # raw uploadStatus from API
    failure_reason: str = ""
    rejection_reason: str = ""


def check_processing_status(service: Any, video_id: str) -> ProcessingResult:
    """Query the YouTube Data API for the current processing state of *video_id*.

    Returns a :class:`ProcessingResult` — never raises.  If the API call itself
    fails (network error, quota exceeded, etc.) the result will have status
    ``"unknown"`` with the exception message in *failure_reason*.
    """
    try:
        response = (
            service.videos()
            .list(id=video_id, part="status,processingDetails")
            .execute()
        )
    except HttpError as exc:
        log.warning("API error checking processing for %s: %s", video_id, exc)
        return ProcessingResult(
            video_id=video_id,
            status="unknown",
            failure_reason=str(exc),
        )
    except Exception as exc:
        log.warning("Unexpected error checking processing for %s: %s", video_id, exc)
        return ProcessingResult(
            video_id=video_id,
            status="unknown",
            failure_reason=str(exc),
        )

    items = response.get("items", [])
    if not items:
        log.warning("No items returned for video %s — may have been deleted", video_id)
        return ProcessingResult(video_id=video_id, status="unknown")

    item = items[0]
    status_obj = item.get("status", {})
    upload_status = status_obj.get("uploadStatus", "")
    failure_reason = status_obj.get("failureReason", "")
    rejection_reason = status_obj.get("rejectionReason", "")

    if upload_status == "processed":
        return ProcessingResult(
            video_id=video_id,
            status="processed",
            upload_status=upload_status,
        )

    if upload_status == "failed":
        return ProcessingResult(
            video_id=video_id,
            status="failed",
            upload_status=upload_status,
            failure_reason=failure_reason,
        )

    if upload_status == "rejected":
        return ProcessingResult(
            video_id=video_id,
            status="rejected",
            upload_status=upload_status,
            rejection_reason=rejection_reason,
        )

    if upload_status in ("uploaded", ""):
        # "uploaded" = still processing; empty = transient API state
        return ProcessingResult(
            video_id=video_id,
            status="processing",
            upload_status=upload_status,
        )

    # "deleted" or any unexpected value
    return ProcessingResult(
        video_id=video_id,
        status="unknown",
        upload_status=upload_status,
    )


def wait_for_processing(
    service: Any,
    video_id: str,
    timeout_minutes: int = 120,
    poll_interval_seconds: int = 60,
    *,
    shutdown_flag: Any | None = None,
) -> ProcessingResult:
    """Poll :func:`check_processing_status` until the video reaches a terminal state.

    Terminal states that cause an immediate return:
    - ``"processed"`` — video is ready
    - ``"failed"`` — YouTube could not process the video
    - ``"rejected"`` — video was rejected (e.g. too long)

    Transient states that keep polling:
    - ``"processing"`` — upload acknowledged, still transcoding
    - ``"unknown"`` — API hiccup, will retry

    If *timeout_minutes* elapses without a terminal state the function returns
    a result with status ``"stuck"``.

    *shutdown_flag* can be any object with a truthy evaluation (e.g. a
    ``threading.Event`` via ``is_set()``, or a module-level ``bool``).  When
    ``None`` the function imports ``_shutdown_requested`` from
    :mod:`src.scheduler` as a default.
    """
    deadline = time.monotonic() + timeout_minutes * 60

    while True:
        # Respect shutdown requests
        if _is_shutdown(shutdown_flag):
            log.info("Shutdown requested — aborting processing wait for %s", video_id)
            return ProcessingResult(video_id=video_id, status="unknown")

        result = check_processing_status(service, video_id)
        elapsed_minutes = int((time.monotonic() - (deadline - timeout_minutes * 60)) / 60)

        log.info(
            "Processing check for %s: %s (%dm / %dm)",
            video_id,
            result.status,
            elapsed_minutes,
            timeout_minutes,
        )

        if result.status in ("processed", "failed", "rejected"):
            return result

        if time.monotonic() >= deadline:
            log.warning(
                "Processing timeout for %s after %d minutes",
                video_id,
                timeout_minutes,
            )
            return ProcessingResult(
                video_id=video_id,
                status="stuck",
                upload_status=result.upload_status,
            )

        # Sleep in small increments so we can check shutdown promptly
        sleep_end = time.monotonic() + poll_interval_seconds
        while time.monotonic() < sleep_end:
            if _is_shutdown(shutdown_flag):
                break
            time.sleep(min(5.0, sleep_end - time.monotonic()))


def _is_shutdown(flag: Any | None) -> bool:
    """Check whether a graceful shutdown has been requested.

    Supports:
    - ``None`` — falls back to ``src.scheduler._shutdown_requested``
    - callable (e.g. ``threading.Event.is_set``) — calls it
    - plain bool / truthy value — evaluates directly
    """
    if flag is None:
        try:
            from src.scheduler import _shutdown_requested
            return _shutdown_requested  # type: ignore[return-value]
        except ImportError:
            return False
    if callable(flag):
        return bool(flag())
    return bool(flag)
