"""Video splitter — watches for long videos and splits them into uploadable segments.

Runs as a separate process from the uploader. Scans the watch directory for
videos exceeding the configured max duration and splits them into lossless
segments using ffmpeg's stream copy (no re-encoding).

Split files are named ``<original-stem> Part N.<ext>`` so the uploader's title
parser produces titles like ``02/28/2026 - 10:39 AM Part 1``.
"""

from __future__ import annotations

import logging
import math
import re
import signal
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console

from src.duration import get_duration_hours, get_video_duration_seconds, is_ffprobe_available
from src.models import AppConfig
from src.scanner import scan_for_videos

log = logging.getLogger(__name__)
console = Console()

_shutdown_requested = False

# Segment duration target: 11 hours (safely under 11.5h YouTube limit)
_DEFAULT_SEGMENT_HOURS = 11.0


def _handle_signal(signum, frame) -> None:  # type: ignore[type-arg]
    global _shutdown_requested
    _shutdown_requested = True
    console.print("\n[yellow]Shutdown requested — finishing current operation...[/yellow]")


def split_video(
    file_path: Path,
    segment_hours: float = _DEFAULT_SEGMENT_HOURS,
) -> list[Path]:
    """Split a video into segments of *segment_hours* using ffmpeg stream copy.

    Returns a list of output file paths on success. Returns an empty list on
    failure. Never raises.

    The original file is NOT deleted — the caller decides what to do with it.
    """
    duration_seconds = get_video_duration_seconds(file_path)
    if duration_seconds is None:
        log.error("Cannot determine duration of %s — skipping split", file_path.name)
        return []

    segment_seconds = segment_hours * 3600
    num_segments = _calculate_segment_count(duration_seconds, segment_seconds)

    if num_segments <= 1:
        log.info("%s does not need splitting (%.1fh)", file_path.name, duration_seconds / 3600)
        return []

    log.info(
        "Splitting %s (%.1fh) into %d segments of ~%.1fh each",
        file_path.name,
        duration_seconds / 3600,
        num_segments,
        segment_hours,
    )

    output_paths: list[Path] = []
    stem = file_path.stem
    suffix = file_path.suffix
    parent = file_path.parent

    for i in range(num_segments):
        part_num = i + 1
        start_seconds = i * segment_seconds
        output_name = f"{stem} Part {part_num}{suffix}"
        output_path = parent / output_name

        if output_path.exists():
            log.warning("Output file already exists, skipping: %s", output_path.name)
            output_paths.append(output_path)
            continue

        log.info(
            "  Segment %d/%d: start=%.0fs, max_duration=%.0fs -> %s",
            part_num,
            num_segments,
            start_seconds,
            segment_seconds,
            output_name,
        )

        # Write to a .tmp file first so the uploader's scanner never sees a
        # partially-written segment (glob patterns don't match .tmp).
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        success = _run_ffmpeg_split(
            input_path=file_path,
            output_path=tmp_path,
            start_seconds=start_seconds,
            duration_seconds=segment_seconds,
        )

        if not success:
            log.error(
                "ffmpeg failed on segment %d of %s — aborting split",
                part_num,
                file_path.name,
            )
            _cleanup_partial_split(output_paths, tmp_path)
            return []

        # Atomic rename within same volume — uploader only sees the final name
        tmp_path.rename(output_path)
        output_paths.append(output_path)

    log.info("Split complete: %s -> %d segments", file_path.name, len(output_paths))
    return output_paths


def _run_ffmpeg_split(
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> bool:
    """Run a single ffmpeg split command. Returns True on success."""
    # -ss before -i = input seeking (fast, keyframe-aligned). With -c copy this
    # is the correct approach — output seeking would decode the entire prefix and
    # still cut on a keyframe. A short overlap (up to one keyframe interval,
    # typically 2-5s for OBS recordings) may occur at segment boundaries.
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start_seconds),
        "-i", str(input_path),
        "-t", str(duration_seconds),
        "-c", "copy",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour max per segment (stream copy is fast)
        )
        if result.returncode != 0:
            log.error("ffmpeg error: %s", result.stderr[-500:] if result.stderr else "no output")
            return False
        return True
    except subprocess.TimeoutExpired:
        log.error("ffmpeg timed out splitting %s at offset %.0fs", input_path.name, start_seconds)
        return False
    except (OSError, FileNotFoundError) as exc:
        log.error("ffmpeg execution failed: %s", exc)
        return False


def _cleanup_partial_split(completed: list[Path], failed: Path) -> None:
    """Remove partial split outputs after a failure."""
    # Remove the failed segment if it was partially written
    if failed.exists():
        try:
            failed.unlink()
            log.info("Removed partial segment: %s", failed.name)
        except OSError as exc:
            log.error(
                "Failed to remove partial segment: %s (%s) — manual cleanup required",
                failed.name,
                exc,
            )

    # Remove previously completed segments
    for path in completed:
        try:
            path.unlink()
            log.info("Removed segment (rollback): %s", path.name)
        except OSError as exc:
            log.error(
                "Failed to remove segment during rollback: %s (%s) — manual cleanup required",
                path.name,
                exc,
            )


def _calculate_segment_count(total_seconds: float, segment_seconds: float) -> int:
    """Calculate the number of segments needed (ceiling division)."""
    if total_seconds <= segment_seconds:
        return 1
    return math.ceil(total_seconds / segment_seconds)


def scan_and_split(config: AppConfig) -> int:
    """Scan the watch directory and split any videos exceeding max duration.

    Returns the number of videos successfully split.
    """
    if not is_ffprobe_available():
        console.print("[red]ffprobe is not available — cannot determine video durations.[/red]")
        console.print("[dim]Install ffmpeg: winget install Gyan.FFmpeg[/dim]")
        return 0

    # Check ffmpeg availability too
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        console.print("[red]ffmpeg is not available — cannot split videos.[/red]")
        console.print("[dim]Install ffmpeg: winget install Gyan.FFmpeg[/dim]")
        return 0

    videos = scan_for_videos(
        config.watch_directory,
        config.video_extensions,
        config.recent_write_threshold_seconds,
    )

    split_count = 0
    segment_hours = min(config.max_duration_hours, _DEFAULT_SEGMENT_HOURS)

    for video in videos:
        if _shutdown_requested:
            break

        # Skip files that are already split segments (contain "Part")
        if _is_part_file(video.path):
            continue

        # Skip files that already have split segments alongside them
        if _has_split_segments(video.path):
            log.debug("Skipping %s — split segments already exist", video.name)
            continue

        duration_hours = get_duration_hours(video.path)
        if duration_hours is None:
            continue

        if duration_hours <= config.max_duration_hours:
            continue

        console.rule(f"[bold blue]{video.name}[/bold blue]")
        console.print(
            f"  Size: [cyan]{video.size_gb:.2f} GB[/cyan]  |  "
            f"Duration: [cyan]{duration_hours:.1f}h[/cyan]  |  "
            f"Segments: [cyan]~{math.ceil(duration_hours / segment_hours)}[/cyan]"
        )

        segments = split_video(video.path, segment_hours=segment_hours)

        if segments:
            split_count += 1
            console.print(
                f"  [green]Split into {len(segments)} segments:[/green]"
            )
            for seg in segments:
                seg_duration = get_duration_hours(seg)
                dur_str = f" ({seg_duration:.1f}h)" if seg_duration else ""
                console.print(f"    [dim]{seg.name}{dur_str}[/dim]")

            # Move original to split_originals/ subfolder
            from src.cleanup import move_to_subfolder

            moved, dest = move_to_subfolder(video.path, "split_originals")
            if moved:
                console.print(f"  [dim]Original moved to {dest}[/dim]")
            else:
                console.print("  [yellow]Could not move original — leaving in place[/yellow]")
        else:
            console.print(f"  [red]Split failed for {video.name}[/red]")

    return split_count


def run_splitter_once(config: AppConfig) -> int:
    """Execute one scan-and-split cycle."""
    console.rule(f"[bold]Splitter run at {datetime.now():%Y-%m-%d %H:%M:%S}[/bold]")
    count = scan_and_split(config)
    console.print(
        f"\n[bold]Splitter complete:[/bold] "
        f"split=[green]{count}[/green] video(s)"
    )
    return count


def run_splitter_forever(config: AppConfig) -> None:
    """Run the splitter daemon, polling every poll_interval_hours."""
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    console.print(
        f"[bold green]Video Splitter started[/bold green]\n"
        f"  Watch directory:  [cyan]{config.watch_directory}[/cyan]\n"
        f"  Max duration:     [cyan]{config.max_duration_hours}h[/cyan]\n"
        f"  Segment target:   [cyan]{min(config.max_duration_hours, _DEFAULT_SEGMENT_HOURS)}h[/cyan]\n"
        f"  Poll interval:    [cyan]{config.poll_interval_hours}h[/cyan]"
    )

    while not _shutdown_requested:
        try:
            run_splitter_once(config)
        except Exception as exc:
            log.error("Unexpected error during split run: %s", exc, exc_info=True)
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

        _interruptible_sleep(sleep_seconds)

    console.print("[bold yellow]Splitter shutdown complete.[/bold yellow]")


def _is_part_file(path: Path) -> bool:
    """Return True if the filename contains a 'Part N' suffix."""
    return bool(re.search(r"\bPart\s+\d+$", path.stem))


def _has_split_segments(path: Path) -> bool:
    """Return True if any Part N segment already exists for this video."""
    pattern = re.compile(
        re.escape(path.stem) + r" Part \d+" + re.escape(path.suffix) + "$"
    )
    return any(pattern.match(p.name) for p in path.parent.iterdir() if p.is_file())


def _interruptible_sleep(seconds: float) -> None:
    """Sleep in small increments to remain responsive to shutdown signals."""
    end = time.monotonic() + seconds
    while not _shutdown_requested:
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(5.0, remaining))
