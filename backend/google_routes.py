"""
google_routes — FastAPI router for Google/Gmail/Drive endpoints.

Provides OAuth login flow, Gmail scanning/searching, attachment downloading,
draft creation, and Google Docs creation. Mounted into server.py via
app.include_router(router).

Endpoints:
  GET    /api/auth/google                                  OAuth redirect
  GET    /api/auth/google/callback                         OAuth callback
  GET    /api/auth/google/status                           Connection status
  POST   /api/gmail/scan                                   Scan Gmail for draw emails
  POST   /api/gmail/search                                 Simple Gmail search
  POST   /api/inbox/analyze/{thread_id}                    Atomic analyze (create session + download + run)
  POST   /api/sessions/{id}/gmail/download/{thread_id}     Download thread attachments
  POST   /api/sessions/{id}/gmail/draft                    Create Gmail draft
  POST   /api/sessions/{id}/drive/doc                      Create Google Doc
"""

import base64
import json
import logging
import os
import threading
import uuid
import zipfile
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path
from typing import Optional

import google_service
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field

logger = logging.getLogger("cchost")

router = APIRouter()

# ---------------------------------------------------------------------------
# Config (no hardcoded URLs)
# ---------------------------------------------------------------------------

_FRONTEND_URL = os.environ.get("CCHOST_FRONTEND_URL", "http://localhost:3000")
_API_BASE_URL = os.environ.get("CCHOST_API_URL", "http://localhost:8420")
_REDIRECT_URI = f"{_API_BASE_URL}/api/auth/google/callback"

_CCHOST_DIR = os.path.join(Path.home(), ".cchost")
_ANALYZED_THREADS_PATH = os.path.join(_CCHOST_DIR, "analyzed-threads.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_analyzed_threads() -> dict:
    """Load the analyzed-threads.json tracking file."""
    try:
        with open(_ANALYZED_THREADS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_analyzed_threads(data: dict) -> None:
    """Save the analyzed-threads.json tracking file."""
    os.makedirs(_CCHOST_DIR, exist_ok=True)
    with open(_ANALYZED_THREADS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def _get_authenticated_credentials() -> google_service.Credentials:
    """Load and refresh Google credentials, or raise 401."""
    tm = google_service.TokenManager()
    creds = tm.load()
    if creds is None:
        raise HTTPException(status_code=401, detail="Gmail not connected")
    creds = tm.refresh_if_needed(creds)
    if creds is None:
        raise HTTPException(status_code=401, detail="Gmail token expired or revoked")
    return creds


def _gmail_api_call(func):
    """Execute a Gmail API call, translating HttpError to HTTPException."""
    try:
        return func()
    except HttpError as exc:
        status = exc.resp.status if hasattr(exc, "resp") else 500
        raise HTTPException(status_code=status, detail=str(exc))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class GmailScanRequest(BaseModel):
    query: str = "has:attachment newer_than:90d"


class GmailSearchRequest(BaseModel):
    query: str


class GmailDraftRequest(BaseModel):
    thread_id: str


class ThreadSummary(BaseModel):
    id: str
    subject: str = ""
    sender: str = ""
    date: str = ""
    message_count: int = 1
    attachment_count: int = 0
    analyzed: bool = False


class SearchThreadSummary(BaseModel):
    id: str
    subject: str = ""
    sender: str = ""
    date: str = ""
    snippet: str = ""


class AnalyzeResponse(BaseModel):
    session_id: str
    run_id: str


class DownloadResponse(BaseModel):
    files: list[str] = Field(default_factory=list)


class DraftResponse(BaseModel):
    draft_id: str
    message: str = "Draft created"


class DocResponse(BaseModel):
    doc_id: str
    doc_url: str


# ---------------------------------------------------------------------------
# Gmail message parsing helpers
# ---------------------------------------------------------------------------


def _get_header(headers: list[dict], name: str) -> str:
    """Extract a header value from Gmail message headers."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _count_attachments(payload: dict) -> int:
    """Count attachments in a Gmail message payload (recursive)."""
    count = 0
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("filename"):
            count += 1
        count += _count_attachments(part)
    return count


def _extract_attachment_parts(payload: dict) -> list[dict]:
    """Recursively extract all attachment parts from a Gmail message payload."""
    attachments = []
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            attachments.append(part)
        attachments.extend(_extract_attachment_parts(part))
    return attachments


# ===========================================================================
# GLOBAL ENDPOINTS
# ===========================================================================


@router.get("/api/auth/google")
def google_oauth_start():
    """Generate OAuth URL and redirect browser to Google consent screen."""
    try:
        flow = google_service.get_oauth_flow(_REDIRECT_URI)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return RedirectResponse(url=auth_url)
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="client_secret.json not found at ~/.cchost/client_secret.json",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"OAuth flow error: {exc}")


@router.get("/api/auth/google/callback")
def google_oauth_callback(code: Optional[str] = None, error: Optional[str] = None):
    """Exchange auth code for tokens, save, redirect to frontend."""
    if error:
        return RedirectResponse(url=f"{_FRONTEND_URL}?tab=inbox&gmail_error={error}")

    if not code:
        return RedirectResponse(url=f"{_FRONTEND_URL}?tab=inbox&gmail_error=no_code")

    try:
        flow = google_service.get_oauth_flow(_REDIRECT_URI)
        credentials = google_service.exchange_code(flow, code)
        tm = google_service.TokenManager()
        tm.save(credentials)
        return RedirectResponse(url=f"{_FRONTEND_URL}?tab=inbox")
    except Exception as exc:
        logger.exception("OAuth callback error")
        return RedirectResponse(url=f"{_FRONTEND_URL}?tab=inbox&gmail_error={str(exc)[:200]}")


@router.get("/api/auth/google/status")
def google_auth_status():
    """Check if Gmail is connected and return email address."""
    tm = google_service.TokenManager()
    creds = tm.load()
    if creds is None:
        return {"connected": False}

    creds = tm.refresh_if_needed(creds)
    if creds is None:
        return {"connected": False}

    try:
        gmail = google_service.build_gmail_service(creds)
        profile = gmail.users().getProfile(userId="me").execute()
        return {"connected": True, "email": profile.get("emailAddress", "")}
    except HttpError:
        return {"connected": False}
    except Exception:
        return {"connected": False}


@router.post("/api/gmail/scan", response_model=list[ThreadSummary])
def gmail_scan(req: GmailScanRequest):
    """Search Gmail for draw-related emails and return thread summaries."""
    creds = _get_authenticated_credentials()
    gmail = google_service.build_gmail_service(creds)

    # Search for messages matching query
    results = _gmail_api_call(lambda: gmail.users().messages().list(userId="me", q=req.query, maxResults=50).execute())
    messages = results.get("messages", [])
    if not messages:
        return []

    analyzed = _load_analyzed_threads()

    # Group messages by thread, fetch first message of each thread for metadata
    seen_threads: dict[str, dict] = {}
    for msg_stub in messages:
        thread_id = msg_stub.get("threadId", msg_stub["id"])
        if thread_id in seen_threads:
            continue

        msg = _gmail_api_call(
            lambda tid=thread_id: gmail.users()
            .threads()
            .get(
                userId="me",
                id=tid,
                format="full",
            )
            .execute()
        )

        thread_messages = msg.get("messages", [])
        if not thread_messages:
            continue

        first_msg = thread_messages[0]
        headers = first_msg.get("payload", {}).get("headers", [])

        # Count attachments across all messages in thread
        total_attachments = 0
        for tmsg in thread_messages:
            total_attachments += _count_attachments(tmsg.get("payload", {}))

        seen_threads[thread_id] = ThreadSummary(
            id=thread_id,
            subject=_get_header(headers, "Subject"),
            sender=_get_header(headers, "From"),
            date=_get_header(headers, "Date"),
            message_count=len(thread_messages),
            attachment_count=total_attachments,
            analyzed=thread_id in analyzed,
        )

    return list(seen_threads.values())


@router.post("/api/gmail/search", response_model=list[SearchThreadSummary])
def gmail_search(req: GmailSearchRequest):
    """Simple Gmail search for mid-run skill queries."""
    creds = _get_authenticated_credentials()
    gmail = google_service.build_gmail_service(creds)

    results = _gmail_api_call(lambda: gmail.users().messages().list(userId="me", q=req.query, maxResults=20).execute())
    messages = results.get("messages", [])
    if not messages:
        return []

    summaries = []
    for msg_stub in messages:
        msg = _gmail_api_call(
            lambda mid=msg_stub["id"]: gmail.users()
            .messages()
            .get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            )
            .execute()
        )
        headers = msg.get("payload", {}).get("headers", [])
        summaries.append(
            SearchThreadSummary(
                id=msg.get("threadId", msg["id"]),
                subject=_get_header(headers, "Subject"),
                sender=_get_header(headers, "From"),
                date=_get_header(headers, "Date"),
                snippet=msg.get("snippet", ""),
            )
        )

    return summaries


# ===========================================================================
# COMPOSITE ENDPOINT
# ===========================================================================


@router.post("/api/inbox/analyze/{thread_id}", response_model=AnalyzeResponse)
def analyze_thread(thread_id: str):
    """Atomic create-session + download attachments + start analyzer run."""
    # Import host and run_manager from server (avoids circular at module level)
    from server import host, run_manager

    creds = _get_authenticated_credentials()

    # 1. Create a new session
    session_id = f"inbox-{uuid.uuid4().hex[:8]}"
    working_dir = f"/tmp/cchost-ui/{session_id}"
    os.makedirs(working_dir, exist_ok=True)

    try:
        session = host.create(session_id, working_dir=working_dir)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # 2. Download attachments
    inbox_dir = os.path.join(working_dir, "inbox", thread_id)
    os.makedirs(inbox_dir, exist_ok=True)
    _download_thread_attachments(creds, thread_id, inbox_dir)

    # 3. Mark thread as analyzed
    analyzed = _load_analyzed_threads()
    analyzed[thread_id] = {"session_id": session_id, "analyzed_at": _utcnow_iso()}
    _save_analyzed_threads(analyzed)

    # 4. Start the analyzer run
    run = run_manager.create_run(session_id)
    message = f"/invoice:analyzer inbox/{thread_id}/"

    worker = threading.Thread(
        target=run_manager.execute_send_run,
        args=(run.run_id, session, message, 600),
        daemon=True,
    )
    worker.start()

    return AnalyzeResponse(session_id=session_id, run_id=run.run_id)


# ===========================================================================
# SESSION-SCOPED ENDPOINTS
# ===========================================================================


@router.post(
    "/api/sessions/{session_id}/gmail/download/{thread_id}",
    response_model=DownloadResponse,
)
def download_thread_attachments(session_id: str, thread_id: str):
    """Download all attachments from a Gmail thread into the session working dir."""
    from server import host

    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    creds = _get_authenticated_credentials()

    inbox_dir = os.path.join(session.working_dir, "inbox", thread_id)
    os.makedirs(inbox_dir, exist_ok=True)

    files = _download_thread_attachments(creds, thread_id, inbox_dir)

    # Mark thread as analyzed
    analyzed = _load_analyzed_threads()
    analyzed[thread_id] = {"session_id": session_id, "analyzed_at": _utcnow_iso()}
    _save_analyzed_threads(analyzed)

    return DownloadResponse(files=files)


@router.post("/api/sessions/{session_id}/gmail/draft", response_model=DraftResponse)
def create_gmail_draft(session_id: str, req: GmailDraftRequest):
    """Create a Gmail draft reply from the session's generated email file."""
    from server import host

    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    creds = _get_authenticated_credentials()

    # Find the email file in the session working dir
    email_text = _find_email_file(session.working_dir)
    if email_text is None:
        raise HTTPException(
            status_code=404,
            detail="No email file found (expected *_draw_email.md or email_to_gc.txt)",
        )

    # Get the original thread to extract reply metadata
    gmail = google_service.build_gmail_service(creds)

    thread = _gmail_api_call(
        lambda: gmail.users()
        .threads()
        .get(
            userId="me",
            id=req.thread_id,
            format="metadata",
            metadataHeaders=["Subject", "From", "To", "Message-ID"],
        )
        .execute()
    )

    thread_messages = thread.get("messages", [])
    if not thread_messages:
        raise HTTPException(status_code=404, detail="Thread not found or empty")

    last_msg = thread_messages[-1]
    headers = last_msg.get("payload", {}).get("headers", [])
    subject = _get_header(headers, "Subject")
    original_from = _get_header(headers, "From")
    message_id = _get_header(headers, "Message-ID")

    # Build the reply
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    mime_msg = MIMEText(email_text)
    mime_msg["to"] = original_from
    mime_msg["subject"] = subject
    if message_id:
        mime_msg["In-Reply-To"] = message_id
        mime_msg["References"] = message_id

    raw_message = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")

    draft = _gmail_api_call(
        lambda: gmail.users()
        .drafts()
        .create(
            userId="me",
            body={
                "message": {
                    "raw": raw_message,
                    "threadId": req.thread_id,
                },
            },
        )
        .execute()
    )

    return DraftResponse(draft_id=draft["id"])


@router.post("/api/sessions/{session_id}/drive/doc", response_model=DocResponse)
def create_google_doc(session_id: str):
    """Create a Google Doc from the session's audit files."""
    from server import host

    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    creds = _get_authenticated_credentials()

    # Collect audit content from session files
    content_parts = []
    working_dir = session.working_dir

    # Look for email file
    email_text = _find_email_file(working_dir)
    if email_text:
        content_parts.append("=== Email to GC ===\n\n" + email_text)

    # Look for insight summary
    insight_text = _find_insight_file(working_dir)
    if insight_text:
        content_parts.append("=== Homeowner Insight Summary ===\n\n" + insight_text)

    if not content_parts:
        raise HTTPException(
            status_code=404,
            detail="No audit files found in session working directory",
        )

    doc_body = "\n\n---\n\n".join(content_parts)

    # Create Google Doc via Drive API
    drive = google_service.build_drive_service(creds)

    file_metadata = {
        "name": f"Draw Audit — {session_id}",
        "mimeType": "application/vnd.google-apps.document",
    }

    # Upload as plain text and convert to Google Doc
    from googleapiclient.http import MediaInMemoryUpload

    media = MediaInMemoryUpload(
        doc_body.encode("utf-8"),
        mimetype="text/plain",
        resumable=False,
    )

    try:
        doc = (
            drive.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id,webViewLink",
            )
            .execute()
        )
    except HttpError as exc:
        status = exc.resp.status if hasattr(exc, "resp") else 500
        raise HTTPException(status_code=status, detail=str(exc))

    return DocResponse(
        doc_id=doc["id"],
        doc_url=doc.get("webViewLink", f"https://docs.google.com/document/d/{doc['id']}/edit"),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _download_thread_attachments(
    creds: google_service.Credentials,
    thread_id: str,
    dest_dir: str,
) -> list[str]:
    """Download all attachments from a Gmail thread to dest_dir. Returns filenames."""
    gmail = google_service.build_gmail_service(creds)

    thread = _gmail_api_call(lambda: gmail.users().threads().get(userId="me", id=thread_id, format="full").execute())

    downloaded_files: list[str] = []
    for msg in thread.get("messages", []):
        attachment_parts = _extract_attachment_parts(msg.get("payload", {}))
        for part in attachment_parts:
            filename = part["filename"]
            attachment_id = part["body"]["attachmentId"]

            # Stream attachment data from Gmail API
            att = _gmail_api_call(
                lambda mid=msg["id"], aid=attachment_id: gmail.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=mid, id=aid)
                .execute()
            )

            data = base64.urlsafe_b64decode(att["data"])

            # Sanitize filename to prevent path traversal
            filename = os.path.basename(filename)
            if not filename:
                continue

            # Write to disk immediately
            filepath = os.path.join(dest_dir, filename)
            with open(filepath, "wb") as f:
                f.write(data)

            logger.info("Downloaded attachment: %s (%d bytes)", filename, len(data))

            # Handle ZIP files: extract contents alongside the zip
            if filename.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(BytesIO(data)) as zf:
                        for member in zf.namelist():
                            # Skip directories and hidden files
                            if member.endswith("/") or member.startswith("__MACOSX"):
                                continue
                            member_name = os.path.basename(member)
                            if not member_name:
                                continue
                            member_path = os.path.join(dest_dir, member_name)
                            with zf.open(member) as src, open(member_path, "wb") as dst:
                                dst.write(src.read())
                            downloaded_files.append(member_name)
                            logger.info("Extracted from ZIP: %s", member_name)
                except zipfile.BadZipFile:
                    logger.warning("Could not extract ZIP: %s", filename)

            downloaded_files.append(filename)

    return downloaded_files


def _find_email_file(working_dir: str) -> Optional[str]:
    """Find and read an email file from the working directory."""
    import glob as glob_mod

    # Try *_draw_email.md first, then email_to_gc.txt
    patterns = [
        os.path.join(working_dir, "*_draw_email.md"),
        os.path.join(working_dir, "**", "*_draw_email.md"),
        os.path.join(working_dir, "email_to_gc.txt"),
        os.path.join(working_dir, "**", "email_to_gc.txt"),
    ]
    for pattern in patterns:
        matches = glob_mod.glob(pattern, recursive=True)
        if matches:
            with open(matches[0]) as f:
                return f.read()
    return None


def _find_insight_file(working_dir: str) -> Optional[str]:
    """Find and read an insight summary file from the working directory."""
    import glob as glob_mod

    patterns = [
        os.path.join(working_dir, "*_insight_summary.md"),
        os.path.join(working_dir, "**", "*_insight_summary.md"),
        os.path.join(working_dir, "*_homeowner_summary.md"),
        os.path.join(working_dir, "**", "*_homeowner_summary.md"),
    ]
    for pattern in patterns:
        matches = glob_mod.glob(pattern, recursive=True)
        if matches:
            with open(matches[0]) as f:
                return f.read()
    return None
