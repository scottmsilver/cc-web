"""
google_service — Gmail/Drive API client builders.

OAuth tokens are managed by the silver-oauth broker (see broker_client.py).
This module just wraps `googleapiclient.discovery.build` so callers don't need
to import it directly.
"""

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

# Reference list of scopes the broker should grant for cchost. The broker
# enforces what's actually allowed; this just documents what we need.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def build_gmail_service(credentials: Credentials) -> Resource:
    return build("gmail", "v1", credentials=credentials)


def build_drive_service(credentials: Credentials) -> Resource:
    return build("drive", "v3", credentials=credentials)
