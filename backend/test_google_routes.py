"""Endpoint tests for google_routes with mocked Google APIs."""

import os
from unittest.mock import MagicMock, patch

import server
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials
from google_service import SCOPES

client = TestClient(server.app)


def _mock_credentials():
    creds = MagicMock(spec=Credentials)
    creds.valid = True
    creds.expired = False
    creds.token = "fake-token"
    creds.refresh_token = "fake-refresh"
    creds.token_uri = "https://oauth2.googleapis.com/token"
    creds.client_id = "cid"
    creds.client_secret = "csecret"
    creds.scopes = SCOPES
    return creds


# ---------------------------------------------------------------------------
# Auth status
# ---------------------------------------------------------------------------


class TestAuthStatus:
    def test_auth_status_not_connected(self):
        with patch("google_routes.google_service.TokenManager") as MockTM:
            MockTM.return_value.load.return_value = None
            resp = client.get("/api/auth/google/status")
        assert resp.status_code == 200
        assert resp.json() == {"connected": False}

    def test_auth_status_connected(self):
        creds = _mock_credentials()
        mock_gmail = MagicMock()
        mock_gmail.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "test@example.com"
        }

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service", return_value=mock_gmail),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.get("/api/auth/google/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["connected"] is True
        assert data["email"] == "test@example.com"


# ---------------------------------------------------------------------------
# Gmail scan
# ---------------------------------------------------------------------------


class TestGmailScan:
    def test_scan_not_authenticated(self):
        with patch("google_routes.google_service.TokenManager") as MockTM:
            MockTM.return_value.load.return_value = None
            resp = client.post("/api/gmail/scan", json={"query": "has:attachment"})
        assert resp.status_code == 401

    def test_scan_returns_threads(self):
        creds = _mock_credentials()
        mock_gmail = MagicMock()

        # messages.list returns one message stub
        mock_gmail.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "msg1", "threadId": "thread1"}]
        }

        # threads.get returns thread metadata
        mock_gmail.users.return_value.threads.return_value.get.return_value.execute.return_value = {
            "messages": [
                {
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Draw Request #5"},
                            {"name": "From", "value": "contractor@example.com"},
                            {"name": "Date", "value": "2026-04-01"},
                        ],
                        "parts": [{"filename": "invoice.pdf", "body": {"attachmentId": "a1"}}],
                    }
                }
            ]
        }

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service", return_value=mock_gmail),
            patch("google_routes._load_analyzed_threads", return_value={}),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.post("/api/gmail/scan", json={"query": "has:attachment"})

        assert resp.status_code == 200
        threads = resp.json()
        assert len(threads) == 1
        assert threads[0]["id"] == "thread1"
        assert threads[0]["subject"] == "Draw Request #5"
        assert threads[0]["attachment_count"] == 1

    def test_scan_empty_results(self):
        creds = _mock_credentials()
        mock_gmail = MagicMock()
        mock_gmail.users.return_value.messages.return_value.list.return_value.execute.return_value = {"messages": []}

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service", return_value=mock_gmail),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.post("/api/gmail/scan", json={"query": "has:attachment"})

        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# Gmail search
# ---------------------------------------------------------------------------


class TestGmailSearch:
    def test_search_returns_results(self):
        creds = _mock_credentials()
        mock_gmail = MagicMock()

        mock_gmail.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "msg10", "threadId": "thread10"}]
        }
        mock_gmail.users.return_value.messages.return_value.get.return_value.execute.return_value = {
            "id": "msg10",
            "threadId": "thread10",
            "snippet": "Invoice attached",
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Monthly Invoice"},
                    {"name": "From", "value": "vendor@example.com"},
                    {"name": "Date", "value": "2026-03-15"},
                ],
                "parts": [],
            },
        }

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service", return_value=mock_gmail),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.post("/api/gmail/search", json={"query": "invoice"})

        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 1
        assert results[0]["subject"] == "Monthly Invoice"
        assert results[0]["snippet"] == "Invoice attached"


# ---------------------------------------------------------------------------
# Download attachments
# ---------------------------------------------------------------------------


class TestDownloadAttachments:
    def test_download_attachments(self, tmp_path):
        creds = _mock_credentials()
        mock_gmail = MagicMock()

        import base64

        file_data = base64.urlsafe_b64encode(b"PDF-CONTENT-HERE").decode()

        mock_gmail.users.return_value.threads.return_value.get.return_value.execute.return_value = {
            "messages": [
                {
                    "id": "msg1",
                    "payload": {
                        "parts": [
                            {
                                "filename": "invoice.pdf",
                                "body": {"attachmentId": "att1"},
                            }
                        ]
                    },
                }
            ]
        }
        mock_gmail.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
            "data": file_data
        }

        # Create a fake session
        fake_session = MagicMock()
        fake_session.working_dir = str(tmp_path)

        fake_host = MagicMock()
        fake_host.get.return_value = fake_session

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service", return_value=mock_gmail),
            patch("server.host", fake_host),
            patch("google_routes._load_analyzed_threads", return_value={}),
            patch("google_routes._save_analyzed_threads"),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.post("/api/sessions/sess-1/gmail/download/thread99")

        assert resp.status_code == 200
        data = resp.json()
        assert "invoice.pdf" in data["files"]
        # Verify file was written to disk
        inbox_dir = os.path.join(str(tmp_path), "inbox", "thread99")
        assert os.path.exists(os.path.join(inbox_dir, "invoice.pdf"))

    def test_download_session_not_found(self):
        fake_host = MagicMock()
        fake_host.get.side_effect = KeyError("no-such-session")

        with patch("server.host", fake_host):
            resp = client.post("/api/sessions/no-such-session/gmail/download/thread1")

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Session not found"


# ---------------------------------------------------------------------------
# Gmail draft
# ---------------------------------------------------------------------------


class TestGmailDraft:
    def test_draft_creates_reply(self, tmp_path):
        creds = _mock_credentials()
        mock_gmail = MagicMock()

        # threads.get for reply metadata
        mock_gmail.users.return_value.threads.return_value.get.return_value.execute.return_value = {
            "messages": [
                {
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Draw Request"},
                            {"name": "From", "value": "gc@example.com"},
                            {"name": "To", "value": "owner@example.com"},
                            {"name": "Message-ID", "value": "<msg-id-123@mail>"},
                        ]
                    }
                }
            ]
        }
        # drafts.create
        mock_gmail.users.return_value.drafts.return_value.create.return_value.execute.return_value = {"id": "draft-abc"}

        # Write an email file in the session working dir
        email_path = os.path.join(str(tmp_path), "01_draw_email.md")
        with open(email_path, "w") as f:
            f.write("Hi, please find attached the audit results.")

        fake_session = MagicMock()
        fake_session.working_dir = str(tmp_path)
        fake_host = MagicMock()
        fake_host.get.return_value = fake_session

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service", return_value=mock_gmail),
            patch("server.host", fake_host),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.post(
                "/api/sessions/sess-1/gmail/draft",
                json={"thread_id": "thread-xyz"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["draft_id"] == "draft-abc"

    def test_draft_no_email_file(self, tmp_path):
        creds = _mock_credentials()

        fake_session = MagicMock()
        fake_session.working_dir = str(tmp_path)
        fake_host = MagicMock()
        fake_host.get.return_value = fake_session

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service"),
            patch("server.host", fake_host),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.post(
                "/api/sessions/sess-1/gmail/draft",
                json={"thread_id": "thread-xyz"},
            )

        assert resp.status_code == 404
        assert "No email file found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Composite analyze endpoint
# ---------------------------------------------------------------------------


class TestAnalyzeComposite:
    def test_analyze_composite(self, tmp_path):
        creds = _mock_credentials()
        mock_gmail = MagicMock()

        import base64

        file_data = base64.urlsafe_b64encode(b"ATTACHMENT").decode()

        mock_gmail.users.return_value.threads.return_value.get.return_value.execute.return_value = {
            "messages": [
                {
                    "id": "msg1",
                    "payload": {
                        "parts": [
                            {
                                "filename": "doc.pdf",
                                "body": {"attachmentId": "att1"},
                            }
                        ]
                    },
                }
            ]
        }
        mock_gmail.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
            "data": file_data
        }

        # Mock host.create to return a fake session
        fake_session = MagicMock()
        fake_session.working_dir = str(tmp_path)

        fake_host = MagicMock()
        fake_host.create.return_value = fake_session

        # Mock run_manager
        fake_run = MagicMock()
        fake_run.run_id = "run-123"
        fake_run_manager = MagicMock()
        fake_run_manager.create_run.return_value = fake_run

        with (
            patch("google_routes.google_service.TokenManager") as MockTM,
            patch("google_routes.google_service.build_gmail_service", return_value=mock_gmail),
            patch("server.host", fake_host),
            patch("server.run_manager", fake_run_manager),
            patch("google_routes._load_analyzed_threads", return_value={}),
            patch("google_routes._save_analyzed_threads"),
        ):
            MockTM.return_value.load.return_value = creds
            MockTM.return_value.refresh_if_needed.return_value = creds
            resp = client.post("/api/inbox/analyze/thread-42")

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run-123"
        assert "session_id" in data
        assert data["session_id"].startswith("inbox-")
