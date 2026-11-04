"""Tests for src/cleanup.py."""

from __future__ import annotations

from pathlib import Path

import pytest

import src.queue_manager as qm
from src.cleanup import delete_uploaded_video, move_to_subfolder


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
    monkeypatch.setattr("src.cleanup.time.sleep", lambda _: None)

    success, new_state = delete_uploaded_video(video, state)

    assert success is False
    assert qm.get_file_status(new_state, video) == qm.STATUS_UPLOADED


def test_retries_then_succeeds_on_transient_lock(
    tmp_path: Path, tmp_state_file: Path, monkeypatch
) -> None:
    """Deletion should succeed if the file lock clears on a later retry."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    state = qm.load_state()
    state = qm.mark_uploading(state, video)
    state = qm.mark_uploaded(state, video, "vid123", "Title")

    call_count = 0
    original_unlink = Path.unlink

    def fail_first_two(self, missing_ok=False):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise OSError("WinError 32: file in use")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fail_first_two)
    monkeypatch.setattr("src.cleanup.time.sleep", lambda _: None)

    success, new_state = delete_uploaded_video(video, state)

    assert success is True
    assert not video.exists()
    assert qm.get_file_status(new_state, video) == qm.STATUS_DELETED
    assert call_count == 3


# ---------------------------------------------------------------------------
# move_to_subfolder
# ---------------------------------------------------------------------------


def test_move_to_subfolder_success(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    success, dest = move_to_subfolder(video, "uploaded")

    assert success is True
    assert dest is not None
    assert dest == tmp_path / "uploaded" / "video.mp4"
    assert dest.exists()
    assert not video.exists()


def test_move_to_subfolder_creates_directory(tmp_path: Path) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    subfolder = tmp_path / "new_subfolder"
    assert not subfolder.exists()

    success, dest = move_to_subfolder(video, "new_subfolder")

    assert success is True
    assert subfolder.exists()
    assert subfolder.is_dir()


def test_move_to_subfolder_handles_name_collision(tmp_path: Path) -> None:
    subfolder = tmp_path / "uploaded"
    subfolder.mkdir()

    # Create a file at the destination first
    existing = subfolder / "video.mp4"
    existing.write_bytes(b"\x01" * 50)

    # Create the source file
    video = tmp_path / "video.mp4"
    video.write_bytes(b"\x00" * 100)

    success, dest = move_to_subfolder(video, "uploaded")

    assert success is True
    assert dest is not None
    # Should have a numeric suffix due to collision
    assert dest.name == "video (1).mp4"
    assert dest.exists()
    # Original destination file should still exist untouched
    assert existing.exists()


def test_move_to_subfolder_nonexistent_file(tmp_path: Path) -> None:
    video = tmp_path / "nonexistent.mp4"

    success, dest = move_to_subfolder(video, "uploaded")

    assert success is False
    assert dest is None
