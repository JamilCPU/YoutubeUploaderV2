"""CLI entry point for YoutubeUploaderV2.

Usage:
    python -m src                   # Start polling daemon (same as 'run')
    python -m src run               # Start polling daemon
    python -m src run-once          # Single scan/upload cycle then exit
    python -m src status            # Print queue summary and exit
    python -m src auth              # Run interactive OAuth flow and save token
    python -m src reset-failed      # Reset all failed uploads to pending
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.logging import RichHandler

from src import auth, queue_manager as qm
from src.config import load_config
from src.scheduler import print_status, run_forever, run_once


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    # Suppress noisy third-party loggers
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _cmd_run(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config) if args.config else None)
    run_forever(config)


def _cmd_run_once(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config) if args.config else None)
    service = auth.get_youtube_service(config)
    run_once(config, service)


def _cmd_status(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config) if args.config else None)
    print_status(config)


def _cmd_auth(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config) if args.config else None)
    service = auth.get_youtube_service(config)
    print("Authentication successful. Token saved.")


def _cmd_reset_failed(args: argparse.Namespace) -> None:
    state = qm.load_state()
    before = qm.get_queue_summary(state)
    state = qm.reset_failed(state)
    qm.save_state(state)
    after = qm.get_queue_summary(state)
    reset_count = before.get("failed", 0) - after.get("failed", 0)
    print(f"Reset {reset_count} failed upload(s) to pending.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="youtube-uploader",
        description="Upload long videos to YouTube via the Data API v3.",
    )
    parser.add_argument("--config", metavar="PATH", help="Path to config.yaml")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("run", help="Start the polling daemon (default)")
    subparsers.add_parser("run-once", help="Run one upload cycle then exit")
    subparsers.add_parser("status", help="Print queue summary")
    subparsers.add_parser("auth", help="Run interactive OAuth flow")
    subparsers.add_parser("reset-failed", help="Reset failed uploads to pending")

    args = parser.parse_args()
    _setup_logging(args.verbose)

    command = args.command or "run"
    dispatch = {
        "run": _cmd_run,
        "run-once": _cmd_run_once,
        "status": _cmd_status,
        "auth": _cmd_auth,
        "reset-failed": _cmd_reset_failed,
    }

    try:
        dispatch[command](args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
