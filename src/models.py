"""Domain models for YoutubeUploaderV2."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    watch_directory: Path
    poll_interval_hours: int = 3
    max_daily_uploads: int = 6
    uploads_per_run: int = 2
    chunk_size_mb: int = 10
    recent_write_threshold_minutes: int = 30
    delete_after_upload: bool = True
    privacy_status: str = "private"
    video_extensions: tuple[str, ...] = (".mp4", ".mkv", ".avi", ".mov", ".webm")
    max_duration_hours: float = 11.5
    processing_timeout_minutes: int = 120

    # OAuth credentials (loaded from env vars)
    client_id: str = ""
    client_secret: str = ""
    project_id: str = ""

    @property
    def chunk_size_bytes(self) -> int:
        return self.chunk_size_mb * 1024 * 1024

    @property
    def recent_write_threshold_seconds(self) -> int:
        return self.recent_write_threshold_minutes * 60


@dataclass(frozen=True)
class VideoFile:
    path: Path
    size_bytes: int
    modified_time: datetime

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 * 1024 * 1024)


@dataclass(frozen=True)
class UploadProgress:
    bytes_uploaded: int
    total_bytes: int
    speed_bps: float  # bytes per second
    elapsed_seconds: float

    @property
    def percent(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return (self.bytes_uploaded / self.total_bytes) * 100

    @property
    def eta_seconds(self) -> float | None:
        remaining = self.total_bytes - self.bytes_uploaded
        if self.speed_bps <= 0:
            return None
        return remaining / self.speed_bps

    @property
    def speed_mb(self) -> float:
        return self.speed_bps / (1024 * 1024)


@dataclass(frozen=True)
class UploadResult:
    file_path: Path
    success: bool
    video_id: str = ""
    title: str = ""
    uploaded_at: datetime = field(default_factory=datetime.utcnow)
    error_message: str = ""

    @property
    def youtube_url(self) -> str:
        if self.video_id:
            return f"https://www.youtube.com/watch?v={self.video_id}"
        return ""


@dataclass
class RunSummary:
    uploaded: int = 0
    failed: int = 0
    skipped_quota: int = 0
    skipped_recent: int = 0
    deleted: int = 0
    next_run_at: datetime | None = None
