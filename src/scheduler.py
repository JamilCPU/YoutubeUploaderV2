"""Polling scheduler — orchestrates scan → upload → cleanup every N hours."""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta

from rich.console import Console
from rich.table import Table

from src import auth, queue_manager as qm
from src.cleanup import delete_uploaded_video, move_to_subfolder
from src.duration import get_duration_hours, is_ffprobe_available
from src.models import AppConfig, RunSummary
from src.processing import wait_for_processing
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
    log.info(
        "Daily quota: %d used, %d remaining",
        config.max_daily_uploads - remaining_quota,
        remaining_quota,
    )
    if remaining_quota == 0:
        log.warning("Daily upload quota exhausted — skipping this run (resets at midnight UTC)")
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
        log.info("No pending videos found in %s", config.watch_directory)
        console.print("[dim]No pending videos found.[/dim]")
        return summary

    to_upload = pending[: min(config.uploads_per_run, remaining_quota)]
    log.info(
        "Queue: %d pending, uploading %d this run (%d skipped for quota/limit)",
        len(pending),
        len(to_upload),
        len(pending) - len(to_upload),
    )
    console.print(
        f"[bold]Found {len(pending)} pending video(s). "
        f"Uploading up to {len(to_upload)} this run.[/bold]"
    )

    if not is_ffprobe_available():
        console.print(
            "[yellow]Warning: ffprobe not found — cannot check video duration "
            "before upload. Install ffmpeg to enable pre-upload duration checks.[/yellow]"
        )

    for video in to_upload:
        if _shutdown_requested:
            break

        console.rule(f"[bold blue]{video.name}[/bold blue]")
        console.print(
            f"  Size: [cyan]{video.size_gb:.2f} GB[/cyan]  |  "
            f"Modified: [dim]{video.modified_time:%Y-%m-%d %H:%M:%S}[/dim]"
        )

        # --- Phase 1: Pre-upload duration check ---
        duration_hours = get_duration_hours(video.path)
        if duration_hours is not None:
            console.print(f"  Duration: [cyan]{duration_hours:.1f}h[/cyan]")
            if duration_hours > config.max_duration_hours:
                log.warning(
                    "Video %s is %.1fh (max %.1fh) — too long, moving to too_long/",
                    video.name,
                    duration_hours,
                    config.max_duration_hours,
                )
                console.print(
                    f"  [yellow]Too long ({duration_hours:.1f}h > "
                    f"{config.max_duration_hours}h) — moving to too_long/[/yellow]"
                )
                state = qm.mark_too_long(state, video.path, duration_hours)
                qm.save_state(state)
                moved, dest = move_to_subfolder(video.path, "too_long")
                if moved:
                    console.print(f"  [dim]Moved to {dest}[/dim]")
                else:
                    console.print("  [red]Failed to move file[/red]")
                summary.failed += 1
                continue

        log.info(
            "Starting: %s (%.2f GB, modified %s)",
            video.name,
            video.size_gb,
            video.modified_time.strftime("%Y-%m-%d %H:%M:%S"),
        )
        state = qm.mark_uploading(state, video.path)
        qm.save_state(state)

        # Build the Rich progress display.
        # Manually start/stop so a rendering error on teardown (e.g. Unicode
        # encoding failure on Windows) cannot crash the process before we save state.
        rich_progress = make_rich_progress()
        tracker = UploadProgressTracker(video.size_bytes, video.name)
        rich_progress.start()
        try:
            task_id = rich_progress.add_task(
                description=video.name,
                total=video.size_bytes,
            )
            callback = make_progress_callback(tracker, rich_progress, task_id)
            result = upload_video(service, video, config, progress_callback=callback)
        finally:
            try:
                rich_progress.stop()
            except Exception as render_exc:
                log.warning("Rich progress rendering error (ignored): %s", render_exc)

        if result.success:
            state = qm.mark_uploaded(state, video.path, result.video_id, result.title)
            qm.save_state(state)
            summary.uploaded += 1
            log.info("Upload complete: %s -> %s", result.title, result.youtube_url)

            console.print(
                f"  [green]Uploaded:[/green] {result.title}\n"
                f"  [dim]{result.youtube_url}[/dim]"
            )

            # --- Phase 3: Post-upload processing verification ---
            console.print(
                f"  [dim]Waiting for YouTube processing "
                f"(up to {config.processing_timeout_minutes}m)...[/dim]"
            )
            proc_result = wait_for_processing(
                service,
                result.video_id,
                timeout_minutes=config.processing_timeout_minutes,
            )

            if proc_result.status == "processed":
                console.print("  [green]Processing complete[/green]")
                if config.delete_after_upload:
                    deleted, state = delete_uploaded_video(video.path, state)
                    qm.save_state(state)
                    if deleted:
                        summary.deleted += 1

            elif proc_result.status == "rejected":
                reason = proc_result.rejection_reason or "unknown"
                log.error(
                    "YouTube rejected %s: %s", video.name, reason,
                )
                console.print(
                    f"  [red]Rejected by YouTube:[/red] {reason}"
                )
                state = qm.mark_processing_failed(
                    state, video.path, result.video_id, f"Rejected: {reason}"
                )
                qm.save_state(state)
                moved, dest = move_to_subfolder(video.path, "processing_failed")
                if moved:
                    console.print(f"  [dim]Moved to {dest}[/dim]")

            elif proc_result.status == "failed":
                reason = proc_result.failure_reason or "unknown"
                log.error(
                    "YouTube processing failed for %s: %s", video.name, reason,
                )
                console.print(
                    f"  [red]Processing failed:[/red] {reason}"
                )
                state = qm.mark_processing_failed(
                    state, video.path, result.video_id, f"Processing failed: {reason}"
                )
                qm.save_state(state)
                moved, dest = move_to_subfolder(video.path, "processing_failed")
                if moved:
                    console.print(f"  [dim]Moved to {dest}[/dim]")

            else:
                # "stuck" or "unknown" — don't delete, keep the local file safe
                log.warning(
                    "Processing status unclear for %s (status=%s) — keeping local file",
                    video.name,
                    proc_result.status,
                )
                console.print(
                    f"  [yellow]Processing status: {proc_result.status} "
                    f"— keeping local file[/yellow]"
                )
                state = qm.mark_processing_failed(
                    state, video.path, result.video_id,
                    f"Processing stalled (status: {proc_result.status})",
                )
                qm.save_state(state)
                moved, dest = move_to_subfolder(video.path, "processing_failed")
                if moved:
                    console.print(f"  [dim]Moved to {dest}[/dim]")
        else:
            state = qm.mark_failed(state, video.path, result.error_message)
            qm.save_state(state)
            summary.failed += 1
            log.error("Upload failed: %s — %s", video.name, result.error_message)
            console.print(f"  [red]Failed:[/red] {result.error_message}")

    _print_run_summary(summary, config)
    return summary


def run_forever(config: AppConfig) -> None:
    """Run the upload daemon indefinitely, polling every poll_interval_hours."""
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    console.print(
        f"[bold green]YoutubeUploaderV2 started[/bold green]\n"
        f"  Watch directory:    [cyan]{config.watch_directory}[/cyan]\n"
        f"  Poll interval:      [cyan]{config.poll_interval_hours}h[/cyan]\n"
        f"  Privacy:            [cyan]{config.privacy_status}[/cyan]\n"
        f"  Delete on upload:   [cyan]{config.delete_after_upload}[/cyan]\n"
        f"  Max duration:       [cyan]{config.max_duration_hours}h[/cyan]\n"
        f"  Processing timeout: [cyan]{config.processing_timeout_minutes}m[/cyan]"
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
        log.info("Sleeping until next run at %s", next_run.strftime("%Y-%m-%d %H:%M:%S"))
        console.print(
            f"\n[dim]Next run at {next_run:%Y-%m-%d %H:%M:%S} "
            f"(sleeping {config.poll_interval_hours}h)[/dim]"
        )

        # Sleep in small increments to remain responsive to shutdown signals
        _interruptible_sleep(sleep_seconds)

    console.print("[bold yellow]Shutdown complete.[/bold yellow]")


def _interruptible_sleep(seconds: float) -> None:
    """Sleep for *seconds*, logging a heartbeat every 15 minutes."""
    end = time.monotonic() + seconds
    last_heartbeat = time.monotonic()
    heartbeat_interval = 15 * 60  # 15 minutes

    while not _shutdown_requested:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break

        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_interval:
            remaining_mins = int(remaining / 60)
            log.info("Waiting for next run — %d minute(s) remaining", remaining_mins)
            last_heartbeat = now

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
