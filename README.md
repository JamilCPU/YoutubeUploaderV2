# YouTube Uploader V2

A polling daemon that watches a folder for long video recordings and uploads them to YouTube through the Data API v3, keeping track of progress so it can resume and respect the daily upload quota. A companion splitter process can cut videos that are too long for YouTube into lossless segments with ffmpeg, and a processing check confirms YouTube has finished processing a video before the local file is deleted. It replaces a simpler first version.

## Stack

- Python 3.11+
- Google API client (`google-api-python-client`, `google-auth-oauthlib`) for OAuth and resumable uploads
- `rich` (console output and logging), `pyyaml`, `python-dotenv`
- ffmpeg / ffprobe (external, only needed for the splitter)
- pytest with coverage (80% minimum enforced), black, ruff, mypy

## Running

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env              # fill in YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET, YOUTUBE_PROJECT_ID
cp config.example.yaml config.yaml   # set watch_directory and limits

python -m src auth                # one-time interactive OAuth flow, saves token.json
python -m src run-once            # single scan/upload cycle
python -m src run                 # polling daemon (default every few hours)
python -m src status              # queue summary
python -m src reset-failed        # mark failed uploads as pending again

python -m src split-once          # single splitter cycle (needs ffmpeg/ffprobe on PATH)
python -m src split-watch         # splitter daemon

pytest
```

`.env`, `config.yaml`, `token.json` and `upload_state.json` hold credentials or local state and are git-ignored. OAuth client credentials come from your own Google Cloud project with the YouTube Data API enabled.

Windows helpers `register_task.ps1` and `register_splitter_task.ps1` register scheduled tasks that start the uploader and splitter at logon. They contain hard-coded interpreter and folder paths; edit them for your machine first.

## Project layout

- `src/scanner.py` - finds candidate video files, skipping ones still being written
- `src/queue_manager.py`, `src/models.py` - persistent upload queue and config models
- `src/uploader.py`, `src/auth.py` - resumable chunked upload and OAuth handling
- `src/scheduler.py` - polling loop, daily quota accounting, status output
- `src/splitter.py`, `src/duration.py` - ffmpeg stream-copy splitting and ffprobe duration checks
- `src/processing.py` - waits for YouTube to finish processing before cleanup
- `src/cleanup.py`, `src/progress.py` - post-upload deletion and progress display
- `tests/` - unit tests for each module

## Status

Work in progress. The uploader is in regular personal use; the splitter, processing verification and duration modules were added most recently and are still being refined.
