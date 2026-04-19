"""Rich progress tracking for YouTube uploads."""

from __future__ import annotations

import time
from collections import deque
from typing import Callable

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from src.models import UploadProgress

console = Console()

# Sliding window size for speed calculation (number of samples)
_SPEED_WINDOW = 10


class UploadProgressTracker:
    """Tracks upload progress and emits UploadProgress instances."""

    def __init__(self, total_bytes: int, file_name: str) -> None:
        self._total_bytes = total_bytes
        self._file_name = file_name
        self._start_time = time.monotonic()
        self._last_bytes = 0

        # (timestamp, bytes_uploaded) pairs for speed calculation
        self._samples: deque[tuple[float, int]] = deque(maxlen=_SPEED_WINDOW)
        self._samples.append((self._start_time, 0))

    def update(self, bytes_uploaded: int) -> UploadProgress:
        """Record a new byte count and return the current progress snapshot."""
        now = time.monotonic()
        self._samples.append((now, bytes_uploaded))
        speed = self._calculate_speed()
        elapsed = now - self._start_time

        return UploadProgress(
            bytes_uploaded=bytes_uploaded,
            total_bytes=self._total_bytes,
            speed_bps=speed,
            elapsed_seconds=elapsed,
        )

    def _calculate_speed(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        oldest_time, oldest_bytes = self._samples[0]
        newest_time, newest_bytes = self._samples[-1]
        dt = newest_time - oldest_time
        if dt <= 0:
            return 0.0
        return (newest_bytes - oldest_bytes) / dt


def make_rich_progress() -> Progress:
    """Create a Rich Progress instance configured for large file uploads."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        "[progress.percentage]{task.percentage:>5.1f}%",
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    )


ProgressCallback = Callable[[UploadProgress], None]


def make_progress_callback(
    tracker: UploadProgressTracker,
    rich_progress: Progress,
    rich_task_id: TaskID,
) -> ProgressCallback:
    """Return a callback that updates both the tracker and the Rich progress bar."""

    def callback(progress: UploadProgress) -> None:
        rich_progress.update(
            rich_task_id,
            completed=progress.bytes_uploaded,
            total=progress.total_bytes,
        )

    return callback


def format_eta(eta_seconds: float | None) -> str:
    if eta_seconds is None or eta_seconds < 0:
        return "unknown"
    eta = int(eta_seconds)
    hours, remainder = divmod(eta, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"
