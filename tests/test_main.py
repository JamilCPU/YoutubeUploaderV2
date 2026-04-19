"""Tests for src/__main__.py CLI dispatch."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.__main__ as main_module
from src.__main__ import _cmd_reset_failed, _cmd_status, _setup_logging, main


def test_setup_logging_info(capsys) -> None:
    """Verify _setup_logging runs without error at INFO level."""
    _setup_logging(verbose=False)


def test_setup_logging_debug() -> None:
    """Verify _setup_logging runs without error at DEBUG level."""
    _setup_logging(verbose=True)


def test_main_status_subcommand(sample_config, tmp_state_file, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["youtube-uploader", "status"])
    with (
        patch("src.__main__.load_config", return_value=sample_config),
        patch("src.__main__.print_status") as mock_print,
    ):
        main()
        mock_print.assert_called_once_with(sample_config)


def test_main_run_once_subcommand(sample_config, tmp_state_file, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["youtube-uploader", "run-once"])
    mock_service = MagicMock()
    with (
        patch("src.__main__.load_config", return_value=sample_config),
        patch("src.__main__.auth.get_youtube_service", return_value=mock_service),
        patch("src.__main__.run_once") as mock_run,
    ):
        main()
        mock_run.assert_called_once_with(sample_config, mock_service)


def test_main_auth_subcommand(sample_config, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["youtube-uploader", "auth"])
    mock_service = MagicMock()
    with (
        patch("src.__main__.load_config", return_value=sample_config),
        patch("src.__main__.auth.get_youtube_service", return_value=mock_service),
        patch("builtins.print") as mock_print,
    ):
        main()
        mock_print.assert_called_once()


def test_main_reset_failed_subcommand(sample_config, tmp_state_file, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["youtube-uploader", "reset-failed"])
    with patch("src.__main__.load_config", return_value=sample_config):
        main()  # Should run without error


def test_main_defaults_to_run(sample_config, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["youtube-uploader"])
    with (
        patch("src.__main__.load_config", return_value=sample_config),
        patch("src.__main__.run_forever") as mock_run,
    ):
        main()
        mock_run.assert_called_once_with(sample_config)


def test_main_exits_on_file_not_found(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["youtube-uploader", "status"])
    with (
        patch("src.__main__.load_config", side_effect=FileNotFoundError("no config")),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 1


def test_main_exits_on_value_error(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["youtube-uploader", "status"])
    with (
        patch("src.__main__.load_config", side_effect=ValueError("bad config")),
        pytest.raises(SystemExit) as exc_info,
    ):
        main()
    assert exc_info.value.code == 1


def test_cmd_reset_failed_reports_count(sample_config, tmp_state_file) -> None:
    import src.queue_manager as qm
    from pathlib import Path

    state = qm.load_state()
    for i in range(3):
        p = Path(f"/fake{i}.mp4")
        state = qm.mark_failed(state, p, "error")
    qm.save_state(state)

    import argparse
    args = argparse.Namespace(config=None)

    with patch("builtins.print") as mock_print:
        _cmd_reset_failed(args)
        mock_print.assert_called_once()
        assert "3" in mock_print.call_args[0][0]
