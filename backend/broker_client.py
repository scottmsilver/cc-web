"""
broker_client — thin client for the silver-oauth broker.

Replaces local Google OAuth token management. The broker holds refresh tokens
encrypted in Firestore, and returns fresh access tokens on demand.

Configuration (env):
  SILVER_OAUTH_BROKER_URL   e.g. https://auth.oursilverfamily.com
  SILVER_OAUTH_BEARER       shared bearer for /token + /accounts
"""

import logging
import os

import requests
from google.oauth2.credentials import Credentials

logger = logging.getLogger("cchost")


def _broker_url() -> str:
    url = os.environ.get("SILVER_OAUTH_BROKER_URL", "").rstrip("/")
    if not url:
        raise RuntimeError("SILVER_OAUTH_BROKER_URL not set")
    return url


def _bearer() -> str:
    bearer = os.environ.get("SILVER_OAUTH_BEARER", "").strip()
    if not bearer:
        raise RuntimeError("SILVER_OAUTH_BEARER not set")
    return bearer


def is_configured() -> bool:
    return bool(os.environ.get("SILVER_OAUTH_BROKER_URL")) and bool(os.environ.get("SILVER_OAUTH_BEARER"))


def credentials_for(email: str, scope: str) -> Credentials:
    """
    Fetch a fresh access token for (email, scope) from the broker and wrap it
    as google.oauth2.credentials.Credentials. The broker handles refresh, so
    callers should treat the returned credentials as short-lived: don't cache,
    just call this again if you need a new request later.
    """
    if not email:
        raise ValueError("email required")
    r = requests.get(
        f"{_broker_url()}/token",
        params={"email": email, "scope": scope},
        headers={"Authorization": f"Bearer {_bearer()}"},
        timeout=15,
    )
    if r.status_code == 404:
        raise PermissionError(f"no broker tokens for {email}")
    if r.status_code == 403:
        raise PermissionError(f"scope {scope} not granted for {email}")
    r.raise_for_status()
    data = r.json()
    # Token is already fresh; we don't need refresh-token machinery on this side.
    return Credentials(token=data["access_token"])


def list_accounts() -> list[dict]:
    r = requests.get(
        f"{_broker_url()}/accounts",
        headers={"Authorization": f"Bearer {_bearer()}"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def start_url(return_url: str, scope: str) -> str:
    """Build the URL the user's browser should be redirected to in order to start OAuth."""
    from urllib.parse import urlencode

    qs = urlencode({"return_url": return_url, "scope": scope})
    return f"{_broker_url()}/start?{qs}"


def revoke(email: str) -> None:
    r = requests.delete(
        f"{_broker_url()}/accounts/{email}",
        headers={"Authorization": f"Bearer {_bearer()}"},
        timeout=15,
    )
    if r.status_code == 404:
        return
    r.raise_for_status()
