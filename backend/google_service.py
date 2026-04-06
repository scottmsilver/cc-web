"""
google_service — Google OAuth token management + Gmail/Drive service builders.

Manages OAuth2 tokens stored as JSON at ~/.cchost/google-tokens.json with
0600 permissions (same approach as gcloud/aws/gh). Provides helpers to create
OAuth flows, exchange authorization codes, and build Gmail/Drive API clients.

Used by google_routes.py for the Draw Inbox feature.
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import Resource, build

logger = logging.getLogger("cchost")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
]

_DEFAULT_TOKEN_DIR = os.path.join(Path.home(), ".cchost")
_TOKEN_FILENAME = "google-tokens.json"
_CLIENT_SECRET_FILENAME = "client_secret.json"


class TokenManager:
    """Manages Google OAuth tokens stored as JSON with 0600 permissions."""

    def __init__(self, token_dir: Optional[str] = None):
        self._token_dir = token_dir or _DEFAULT_TOKEN_DIR
        os.makedirs(self._token_dir, mode=0o700, exist_ok=True)

    @property
    def _token_path(self) -> str:
        return os.path.join(self._token_dir, _TOKEN_FILENAME)

    def save(self, credentials: Credentials) -> None:
        """Serialize credentials to JSON and write with 0600 permissions."""
        data = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": list(credentials.scopes) if credentials.scopes else SCOPES,
        }
        path = self._token_path

        # Write to temp file then rename for atomicity
        tmp_path = path + ".tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
        except Exception:
            os.unlink(tmp_path)
            raise
        os.rename(tmp_path, path)
        logger.info("Saved Google OAuth tokens to %s", path)

    def load(self) -> Optional[Credentials]:
        """Load credentials from disk. Returns None if file is missing or corrupted."""
        path = self._token_path
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return Credentials(
                token=data["token"],
                refresh_token=data.get("refresh_token"),
                token_uri=data.get("token_uri"),
                client_id=data.get("client_id"),
                client_secret=data.get("client_secret"),
                scopes=data.get("scopes", SCOPES),
            )
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("Failed to load Google tokens from %s: %s", path, exc)
            return None

    def clear(self) -> None:
        """Delete the token file."""
        path = self._token_path
        try:
            os.unlink(path)
            logger.info("Cleared Google OAuth tokens at %s", path)
        except FileNotFoundError:
            pass

    def refresh_if_needed(self, credentials: Credentials) -> Optional[Credentials]:
        """Refresh credentials if expired. Returns refreshed creds, or None if revoked."""
        if credentials.valid:
            return credentials
        if not credentials.expired or not credentials.refresh_token:
            return None
        try:
            credentials.refresh(Request())
            self.save(credentials)
            logger.info("Refreshed Google OAuth token")
            return credentials
        except Exception as exc:
            logger.warning("Token refresh failed (likely revoked): %s", exc)
            self.clear()
            return None


# ============================================================
# OAuth helpers
# ============================================================


def get_oauth_flow(redirect_uri: str) -> Flow:
    """Create an OAuth flow from the client secrets file at ~/.cchost/client_secret.json."""
    client_secret_path = os.path.join(_DEFAULT_TOKEN_DIR, _CLIENT_SECRET_FILENAME)
    flow = Flow.from_client_secrets_file(
        client_secret_path,
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    return flow


def exchange_code(flow: Flow, code: str) -> Credentials:
    """Exchange an authorization code for credentials."""
    flow.fetch_token(code=code)
    return flow.credentials


# ============================================================
# Service builders
# ============================================================


def build_gmail_service(credentials: Credentials) -> Resource:
    """Build a Gmail API v1 service client."""
    return build("gmail", "v1", credentials=credentials)


def build_drive_service(credentials: Credentials) -> Resource:
    """Build a Google Drive API v3 service client."""
    return build("drive", "v3", credentials=credentials)
