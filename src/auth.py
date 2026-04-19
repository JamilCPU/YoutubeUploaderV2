"""OAuth 2.0 authentication for the YouTube Data API v3."""

from __future__ import annotations

import logging
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from src.models import AppConfig

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_PATH = Path("token.json")


def get_youtube_service(config: AppConfig) -> Resource:
    """Authenticate and return an authenticated YouTube API service resource."""
    credentials = _load_or_refresh_credentials()
    if credentials is None:
        credentials = _authenticate_interactive(config)
    return build("youtube", "v3", credentials=credentials)


def _load_or_refresh_credentials() -> Credentials | None:
    """Load cached credentials from token.json, refreshing if expired."""
    if not TOKEN_PATH.exists():
        return None

    try:
        credentials = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except Exception as exc:
        log.warning("Failed to load token.json: %s — will re-authenticate", exc)
        return None

    if credentials.valid:
        return credentials

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            _save_credentials(credentials)
            log.info("OAuth token refreshed successfully")
            return credentials
        except RefreshError as exc:
            log.warning("Token refresh failed (%s) — will re-authenticate", exc)
            return None

    return None


def _authenticate_interactive(config: AppConfig) -> Credentials:
    """Run the local OAuth 2.0 flow to obtain credentials (opens browser)."""
    log.info("Starting interactive OAuth flow — a browser window will open")
    client_config = _build_client_config(config)
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    credentials = flow.run_local_server(port=0)
    _save_credentials(credentials)
    log.info("Authentication successful — token saved to %s", TOKEN_PATH)
    return credentials


def _save_credentials(credentials: Credentials) -> None:
    TOKEN_PATH.write_text(credentials.to_json())


def _build_client_config(config: AppConfig) -> dict:
    return {
        "installed": {
            "client_id": config.client_id,
            "client_secret": config.client_secret,
            "project_id": config.project_id,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }
