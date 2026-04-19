"""Configuration loader for YoutubeUploaderV2."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.models import AppConfig

_DEFAULT_CONFIG_PATH = Path("config.yaml")
_EXAMPLE_CONFIG_PATH = Path("config.example.yaml")

REQUIRED_ENV_VARS = ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_PROJECT_ID")


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load and validate configuration from YAML file and environment variables.

    Raises:
        FileNotFoundError: If no config file is found.
        ValueError: If required env vars are missing or watch_directory does not exist.
    """
    load_dotenv()

    path = _resolve_config_path(config_path)
    raw = _load_yaml(path)
    credentials = _load_credentials()
    return _build_config(raw, credentials)


def _resolve_config_path(config_path: Path | None) -> Path:
    if config_path is not None:
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        return config_path

    if _DEFAULT_CONFIG_PATH.exists():
        return _DEFAULT_CONFIG_PATH

    if _EXAMPLE_CONFIG_PATH.exists():
        return _EXAMPLE_CONFIG_PATH

    raise FileNotFoundError(
        "No config file found. Copy config.example.yaml to config.yaml and edit it."
    )


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        data = yaml.safe_load(f)
    return data or {}


def _load_credentials() -> dict[str, str]:
    missing = [v for v in REQUIRED_ENV_VARS if not os.getenv(v)]
    if missing:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            "Copy .env.example to .env and fill in your YouTube API credentials."
        )
    return {
        "client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "project_id": os.environ["YOUTUBE_PROJECT_ID"],
    }


def _build_config(raw: dict, credentials: dict[str, str]) -> AppConfig:
    watch_dir_raw = raw.get("watch_directory")
    if not watch_dir_raw:
        raise ValueError("'watch_directory' is required in config.yaml")

    watch_directory = Path(watch_dir_raw)
    if not watch_directory.exists():
        raise ValueError(f"watch_directory does not exist: {watch_directory}")

    extensions = tuple(raw.get("video_extensions", [".mp4", ".mkv", ".avi", ".mov", ".webm"]))

    return AppConfig(
        watch_directory=watch_directory,
        poll_interval_hours=int(raw.get("poll_interval_hours", 3)),
        max_daily_uploads=int(raw.get("max_daily_uploads", 6)),
        uploads_per_run=int(raw.get("uploads_per_run", 2)),
        chunk_size_mb=int(raw.get("chunk_size_mb", 10)),
        recent_write_threshold_minutes=int(raw.get("recent_write_threshold_minutes", 30)),
        delete_after_upload=bool(raw.get("delete_after_upload", True)),
        privacy_status=str(raw.get("privacy_status", "private")),
        video_extensions=extensions,
        client_id=credentials["client_id"],
        client_secret=credentials["client_secret"],
        project_id=credentials["project_id"],
    )
