"""Probe video duration using ffprobe."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_ffprobe_available: bool | None = None


def is_ffprobe_available() -> bool:
    """Check whether ffprobe can be executed.

    The result is cached in a module-level variable so the check only
    runs once per process.
    """
    global _ffprobe_available
    if _ffprobe_available is not None:
        return _ffprobe_available
    try:
        subprocess.run(
            ["ffprobe", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        _ffprobe_available = True
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        _ffprobe_available = False
    return _ffprobe_available


def get_video_duration_seconds(path: Path) -> float | None:
    """Return the duration of a video file in seconds, or None on failure.

    Uses ffprobe to extract the container duration.  Returns None if
    ffprobe is not installed, the file cannot be probed, or the
    subprocess times out (30 s).
    """
    if not is_ffprobe_available():
        log.warning("ffprobe is not available; cannot determine video duration")
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning("ffprobe failed for %s: %s", path, result.stderr.strip())
            return None
        return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        log.warning("Could not determine duration for %s: %s", path, exc)
        return None


def get_duration_hours(path: Path) -> float | None:
    """Return the duration of a video file in hours, or None on failure."""
    seconds = get_video_duration_seconds(path)
    if seconds is None:
        return None
    return seconds / 3600.0
