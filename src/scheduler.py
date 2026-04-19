"""Polling scheduler — orchestrates scan → upload → cleanup every N hours."""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from src import auth, queue_manager as qm
from src.cleanup import delete_uploaded_video
from src.models import AppConfig, RunSummary
from src.progress import format_eta, make_progress_callback, make_rich_progress, UploadProgressTracker
from src.scanner import scan_for_videos
from src.uploader import upload_video

log = logging.getLogger(__name__)
console = Console()

_shutdown_requested = False


def _handle_signal(signum, frame) -> None:  # type: ignore[type-arg]
    global _shutdown_requested
    _shutdown_requested = True
    console.print("\n[yellow]Shutdown requested — finishing current operation...[/yellow]")


def run_once(config: AppConfig, service) -> RunSummary:
    """Execute one full scan → upload → cleanup cycle."""
    summary = RunSummary()
    state = qm.load_state()

    remaining_quota = qm.daily_uploads_remaining(state, config.max_daily_uploads)
    if remaining_quota == 0:
        console.print("[yellow]Daily upload quota exhausted. Skipping this run.[/yellow]")
        return summary

    videos = scan_for_videos(
        config.watch_directory,
        config.video_extensions,
        config.recent_write_threshold_seconds,
    )

    # Filter out already-processed files
    pending = [v for v in videos if not qm.is_already_processed(state, v.path)]

    if not pending:
        console.print("[dim]No pending videos found.[/dim]")
        return summary

    to_upload = pending[: min(config.uploads_per_run, remaining_quota)]
    console.print(
        f"[bold]Found {len(pending)} pending video(s). "
        f"Uploading up to {len(to_upload)} this run.[/bold]"
    )

    for video in to_upload:
        if _shutdown_requested:
            break

        console.rule(f"[bold blue]{video.name}[/bold blue]")
        console.print(
            f"  Size: [cyan]{video.size_gb:.2f} GB[/cyan]  |  "
            f"Modified: [dim]{video.modified_time:%Y-%m-%d %H:%M:%S}[/dim]"
        )

        state = qm.mark_uploading(state, video.path)
        qm.save_state(state)

        # Build the Rich progress display
        rich_progress = make_rich_progress()
        tracker = UploadProgressTracker(video.size_bytes, video.name)

        with rich_progress:
            task_id = rich_progress.add_task(
                description=video.name,
                total=video.size_bytes,
            )
            callback = make_progress_callback(tracker, rich_progress, task_id)
            result = upload_video(service, video, config, progress_callback=callback)

        if result.success:
            state = qm.mark_uploaded(state, video.path, result.video_id, result.title)
            qm.save_state(state)
            summary.uploaded += 1

            console.print(
                f"  [green]Uploaded:[/green] {result.title}\n"
                f"  [dim]{result.youtube_url}[/dim]"
            )

            if config.delete_after_upload:
                deleted, state = delete_uploaded_video(video.path, state)
                qm.save_state(state)
                if deleted:
                    summary.deleted += 1
        else:
            state = qm.mark_failed(state, video.path, result.error_message)
            qm.save_state(state)
            summary.failed += 1
            console.print(f"  [red]Failed:[/red] {result.error_message}")

    _print_run_summary(summary, config)
    return summary


def run_forever(config: AppConfig) -> None:
    """Run the upload daemon indefinitely, polling every poll_interval_hours."""
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    console.print(
        f"[bold green]YoutubeUploaderV2 started[/bold green]\n"
        f"  Watch directory: [cyan]{config.watch_directory}[/cyan]\n"
        f"  Poll interval:   [cyan]{config.poll_interval_hours}h[/cyan]\n"
        f"  Privacy:         [cyan]{config.privacy_status}[/cyan]\n"
        f"  Delete on upload:[cyan] {config.delete_after_upload}[/cyan]"
    )

    service = auth.get_youtube_service(config)

    while not _shutdown_requested:
        now = datetime.now()
        console.rule(f"[bold]Run at {now:%Y-%m-%d %H:%M:%S}[/bold]")

        try:
            run_once(config, service)
        except Exception as exc:
            log.error("Unexpected error during run: %s", exc, exc_info=True)
            console.print(f"[red]Unexpected error: {exc}[/red]")

        if _shutdown_requested:
            break

        next_run = datetime.now() + timedelta(hours=config.poll_interval_hours)
        sleep_seconds = config.poll_interval_hours * 3600
        console.print(
            f"\n[dim]Next run at {next_run:%Y-%m-%d %H:%M:%S} "
            f"(sleeping {config.poll_interval_hours}h)[/dim]"
        )

        # Sleep in small increments to remain responsive to shutdown signals
        _interruptible_sleep(sleep_seconds)

    console.print("[bold yellow]Shutdown complete.[/bold yellow]")


def _interruptible_sleep(seconds: float) -> None:
    """Sleep for *seconds* but wake every 5 seconds to check for shutdown."""
    end = time.monotonic() + seconds
    while not _shutdown_requested:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(5.0, remaining))


def print_status(config: AppConfig) -> None:
    """Print a summary table of the current queue state."""
    state = qm.load_state()
    counts = qm.get_queue_summary(state)
    remaining = qm.daily_uploads_remaining(state, config.max_daily_uploads)

    table = Table(title="Upload Queue Status", show_header=True, header_style="bold magenta")
    table.add_column("Status", style="cyan")
    table.add_column("Count", justify="right")

    for status, count in counts.items():
        table.add_row(status.capitalize(), str(count))

    console.print(table)
    console.print(f"\nDaily quota remaining: [bold]{remaining}[/bold] / {config.max_daily_uploads}")


def _print_run_summary(summary: RunSummary, config: AppConfig) -> None:
    console.print(
        f"\n[bold]Run complete:[/bold] "
        f"uploaded=[green]{summary.uploaded}[/green]  "
        f"failed=[red]{summary.failed}[/red]  "
        f"deleted=[yellow]{summary.deleted}[/yellow]"
    )
