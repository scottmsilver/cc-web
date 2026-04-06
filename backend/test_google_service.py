"""Unit tests for google_service.TokenManager."""

import os
import stat
from unittest.mock import MagicMock, patch

from google.oauth2.credentials import Credentials
from google_service import SCOPES, TokenManager


def _make_credentials(
    token="test-access-token",
    refresh_token="test-refresh-token",
    token_uri="https://oauth2.googleapis.com/token",
    client_id="test-client-id",
    client_secret="test-client-secret",
    scopes=None,
):
    return Credentials(
        token=token,
        refresh_token=refresh_token,
        token_uri=token_uri,
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes or SCOPES,
    )


class TestTokenManagerSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))
        creds = _make_credentials()
        tm.save(creds)

        loaded = tm.load()
        assert loaded is not None
        assert loaded.token == "test-access-token"
        assert loaded.refresh_token == "test-refresh-token"
        assert loaded.token_uri == "https://oauth2.googleapis.com/token"
        assert loaded.client_id == "test-client-id"
        assert loaded.client_secret == "test-client-secret"

    def test_load_missing_file(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))
        assert tm.load() is None

    def test_load_corrupted_file(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))
        token_path = os.path.join(str(tmp_path), "google-tokens.json")
        with open(token_path, "w") as f:
            f.write("not valid json {{{")
        assert tm.load() is None

    def test_clear_tokens(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))
        creds = _make_credentials()
        tm.save(creds)
        assert tm.load() is not None

        tm.clear()
        assert tm.load() is None

    def test_file_permissions(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))
        creds = _make_credentials()
        tm.save(creds)

        token_path = os.path.join(str(tmp_path), "google-tokens.json")
        file_stat = os.stat(token_path)
        mode = stat.S_IMODE(file_stat.st_mode)
        assert mode == 0o600


class TestTokenManagerRefresh:
    def test_refresh_valid_token(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))
        creds = MagicMock(spec=Credentials)
        creds.valid = True

        result = tm.refresh_if_needed(creds)
        assert result is creds

    def test_refresh_expired_token(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))
        creds = MagicMock(spec=Credentials)
        creds.valid = False
        creds.expired = True
        creds.refresh_token = "some-refresh-token"
        creds.token = "new-access-token"
        creds.token_uri = "https://oauth2.googleapis.com/token"
        creds.client_id = "cid"
        creds.client_secret = "csecret"
        creds.scopes = SCOPES

        with patch("google_service.Request") as mock_request_cls:
            result = tm.refresh_if_needed(creds)

        assert result is creds
        creds.refresh.assert_called_once_with(mock_request_cls.return_value)

    def test_refresh_revoked_token(self, tmp_path):
        tm = TokenManager(token_dir=str(tmp_path))

        # Save tokens first so we can verify they get cleared
        tm.save(_make_credentials())
        assert tm.load() is not None

        creds = MagicMock(spec=Credentials)
        creds.valid = False
        creds.expired = True
        creds.refresh_token = "revoked-token"
        creds.refresh.side_effect = Exception("Token has been revoked")

        result = tm.refresh_if_needed(creds)
        assert result is None
        # Tokens should be cleared after revocation
        assert tm.load() is None
