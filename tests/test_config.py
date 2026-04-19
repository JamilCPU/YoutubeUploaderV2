"""Tests for src/config.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import src.config as config_module
from src.config import _build_config, _load_credentials, _resolve_config_path


def _write_yaml(path: Path, data: dict) -> None:
    path.write_text(yaml.dump(data))


def test_resolve_config_path_explicit(tmp_path: Path) -> None:
    cfg = tmp_path / "my.yaml"
    cfg.write_text("watch_directory: .\n")
    assert _resolve_config_path(cfg) == cfg


def test_resolve_config_path_explicit_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _resolve_config_path(tmp_path / "nonexistent.yaml")


def test_resolve_config_path_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    default = tmp_path / "config.yaml"
    default.write_text("watch_directory: .\n")
    monkeypatch.setattr(config_module, "_DEFAULT_CONFIG_PATH", default)
    monkeypatch.setattr(config_module, "_EXAMPLE_CONFIG_PATH", tmp_path / "none.yaml")
    assert _resolve_config_path(None) == default


def test_resolve_config_path_example_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    example = tmp_path / "config.example.yaml"
    example.write_text("watch_directory: .\n")
    monkeypatch.setattr(config_module, "_DEFAULT_CONFIG_PATH", tmp_path / "none.yaml")
    monkeypatch.setattr(config_module, "_EXAMPLE_CONFIG_PATH", example)
    assert _resolve_config_path(None) == example


def test_resolve_config_path_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module, "_DEFAULT_CONFIG_PATH", tmp_path / "no1.yaml")
    monkeypatch.setattr(config_module, "_EXAMPLE_CONFIG_PATH", tmp_path / "no2.yaml")
    with pytest.raises(FileNotFoundError):
        _resolve_config_path(None)


def test_load_credentials_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "cid")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("YOUTUBE_PROJECT_ID", "proj")
    creds = _load_credentials()
    assert creds["client_id"] == "cid"
    assert creds["client_secret"] == "csecret"
    assert creds["project_id"] == "proj"


def test_load_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YOUTUBE_CLIENT_ID", raising=False)
    monkeypatch.delenv("YOUTUBE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("YOUTUBE_PROJECT_ID", raising=False)
    with pytest.raises(ValueError, match="Missing required environment variables"):
        _load_credentials()


def test_build_config_missing_watch_directory() -> None:
    with pytest.raises(ValueError, match="watch_directory"):
        _build_config({}, {"client_id": "x", "client_secret": "y", "project_id": "z"})


def test_build_config_nonexistent_watch_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        _build_config(
            {"watch_directory": str(tmp_path / "nonexistent")},
            {"client_id": "x", "client_secret": "y", "project_id": "z"},
        )


def test_build_config_valid(tmp_path: Path) -> None:
    watch = tmp_path / "recordings"
    watch.mkdir()
    cfg = _build_config(
        {"watch_directory": str(watch), "privacy_status": "unlisted"},
        {"client_id": "cid", "client_secret": "cs", "project_id": "proj"},
    )
    assert cfg.watch_directory == watch
    assert cfg.privacy_status == "unlisted"
    assert cfg.client_id == "cid"
