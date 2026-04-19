"""Tests for src/cleanup.py."""

from __future__ import annotations

from pathlib import Path

import pytest

import src.queue_manager as qm
from src.cleanup import delete_uploaded_video


def test_deletes_uploaded_file(tmp_path: Path, tmp_state_file: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    state = qm.load_state()
    state = qm.mark_uploading(state, video)
    state = qm.mark_uploaded(state, video, "vid123", "Title")

    success, new_state = delete_uploaded_video(video, state)

    assert success is True
    assert not video.exists()
    assert qm.get_file_status(new_state, video) == qm.STATUS_DELETED


def test_refuses_to_delete_non_uploaded(tmp_path: Path, tmp_state_file: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    state = qm.load_state()
    state = qm.mark_uploading(state, video)  # status is UPLOADING, not UPLOADED

    success, new_state = delete_uploaded_video(video, state)

    assert success is False
    assert video.exists()  # file should not be deleted


def test_handles_already_deleted_file(tmp_path: Path, tmp_state_file: Path) -> None:
    video = tmp_path / "video.mp4"
    # Don't create the file — it's already gone

    state = qm.load_state()
    state = qm.mark_uploading(state, video)
    state = qm.mark_uploaded(state, video, "vid123", "Title")

    success, new_state = delete_uploaded_video(video, state)

    assert success is True
    assert qm.get_file_status(new_state, video) == qm.STATUS_DELETED


def test_handles_permission_error(tmp_path: Path, tmp_state_file: Path, monkeypatch) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    state = qm.load_state()
    state = qm.mark_uploading(state, video)
    state = qm.mark_uploaded(state, video, "vid123", "Title")

    def raise_oserror(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "unlink", raise_oserror)

    success, new_state = delete_uploaded_video(video, state)

    assert success is False
    assert qm.get_file_status(new_state, video) == qm.STATUS_UPLOADED
