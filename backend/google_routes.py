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
from fastapi import APIRouter, HTTPException, Request
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


def _infer_scheme(request) -> str:
    """Infer the external scheme. Tailscale serve terminates TLS without setting x-forwarded-proto."""
    if request.headers.get("x-forwarded-proto"):
        return request.headers["x-forwarded-proto"]
    host = request.headers.get("host", "")
    port = host.split(":")[-1] if ":" in host else ""
    # Tailscale serve uses ports 443, 8443, 10000 for HTTPS
    if port in ("443", "8443", "10000") or ".ts.net" in host:
        return "https"
    return "http"


def _dynamic_redirect_uri(request) -> str:
    """Build redirect URI from the incoming request's Host header."""
    host = request.headers.get("host", "localhost:8420")
    scheme = _infer_scheme(request)
    return f"{scheme}://{host}/api/auth/google/callback"


def _dynamic_frontend_url(request) -> str:
    """Build frontend URL from the incoming request's Host header."""
    host = request.headers.get("host", "localhost:3000")
    hostname = host.split(":")[0]
    scheme = _infer_scheme(request)
    if scheme == "https":
        return os.environ.get("CCHOST_FRONTEND_URL", f"https://{hostname}")
    return os.environ.get("CCHOST_FRONTEND_URL", f"{scheme}://{hostname}:3000")


_CCHOST_DIR = os.path.join(Path.home(), ".cchost")
_DOWNLOADED_THREADS_PATH = os.path.join(_CCHOST_DIR, "downloaded-threads.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_downloaded_threads() -> dict:
    """Load the downloaded-threads.json tracking file."""
    try:
        with open(_DOWNLOADED_THREADS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_downloaded_threads(data: dict) -> None:
    """Save the downloaded-threads.json tracking file."""
    os.makedirs(_CCHOST_DIR, exist_ok=True)
    with open(_DOWNLOADED_THREADS_PATH, "w") as f:
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
    downloaded: bool = False


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
def google_oauth_start(request: Request):
    """Generate OAuth URL and redirect browser to Google consent screen."""
    try:
        redirect_uri = _dynamic_redirect_uri(request)
        flow = google_service.get_oauth_flow(redirect_uri)
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
def google_oauth_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    """Exchange auth code for tokens, save, redirect to frontend."""
    frontend_url = _dynamic_frontend_url(request)
    if error:
        return RedirectResponse(url=f"{frontend_url}?gmail_error={error}")

    if not code:
        return RedirectResponse(url=f"{frontend_url}?gmail_error=no_code")

    try:
        redirect_uri = _dynamic_redirect_uri(request)
        flow = google_service.get_oauth_flow(redirect_uri)
        credentials = google_service.exchange_code(flow, code)
        tm = google_service.TokenManager()
        tm.save(credentials)
        return RedirectResponse(url=f"{frontend_url}?gmail_connected=true")
    except Exception as exc:
        logger.exception("OAuth callback error")
        return RedirectResponse(url=f"{frontend_url}?gmail_error={str(exc)[:200]}")


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

    downloaded = _load_downloaded_threads()

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
            downloaded=thread_id in downloaded,
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

    # 3. Mark thread as downloaded
    downloaded = _load_downloaded_threads()
    downloaded[thread_id] = {"session_id": session_id, "downloaded_at": _utcnow_iso()}
    _save_downloaded_threads(downloaded)

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

    # Write gmail-source.json for downstream draft threading
    gmail = google_service.build_gmail_service(creds)
    thread_data = _gmail_api_call(
        lambda: gmail.users()
        .threads()
        .get(
            userId="me",
            id=thread_id,
            format="metadata",
            metadataHeaders=["From"],
        )
        .execute()
    )
    sender = ""
    for msg in thread_data.get("messages", [])[:1]:
        sender = _get_header(msg.get("payload", {}).get("headers", []), "From")

    source_path = os.path.join(session.working_dir, "gmail-source.json")
    # Merge with existing gmail-source.json (may have multiple thread selections)
    existing_source: dict = {}
    try:
        with open(source_path) as f:
            existing_source = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    existing_ids = existing_source.get("thread_ids", [])
    if thread_id not in existing_ids:
        existing_ids.append(thread_id)
    existing_source["thread_ids"] = existing_ids
    if sender:
        existing_source["sender"] = sender

    with open(source_path, "w") as f:
        json.dump(existing_source, f, indent=2)

    # Mark thread as downloaded
    downloaded = _load_downloaded_threads()
    downloaded[thread_id] = {"session_id": session_id, "downloaded_at": _utcnow_iso()}
    _save_downloaded_threads(downloaded)

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

    gmail = google_service.build_gmail_service(creds)

    # If no thread_id, create an unthreaded draft
    if not req.thread_id:
        mime_msg = MIMEText(email_text)
        mime_msg["subject"] = "Invoice Review"
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        draft = _gmail_api_call(
            lambda: gmail.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
        )
        return DraftResponse(draft_id=draft["id"], message="Draft created (not threaded)")

    # Get the original thread to extract reply metadata
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


def _extract_header(headers: list[dict], name: str) -> str:
    """Extract a header value from Gmail message headers."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_body_text(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        text = _extract_body_text(part)
        if text:
            return text
    return ""


def _extract_body_html(payload: dict) -> str:
    """Recursively extract HTML body from a Gmail message payload."""
    mime = payload.get("mimeType", "")
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        html = _extract_body_html(part)
        if html:
            return html
    return ""


def _extract_inline_images(payload: dict) -> list[dict]:
    """Recursively find inline image parts (Content-ID referenced)."""
    images = []
    mime = payload.get("mimeType", "")
    if mime.startswith("image/"):
        cid = ""
        for h in payload.get("headers", []):
            if h.get("name", "").lower() == "content-id":
                cid = h.get("value", "").strip("<>")
        att_id = payload.get("body", {}).get("attachmentId")
        filename = payload.get("filename", "")
        if att_id and (cid or not filename):
            # Inline image (has Content-ID or no filename = embedded)
            images.append(
                {
                    "attachmentId": att_id,
                    "mimeType": mime,
                    "cid": cid,
                    "filename": filename or f"inline_{len(images)}.{mime.split('/')[-1]}",
                }
            )
    for part in payload.get("parts", []):
        images.extend(_extract_inline_images(part))
    return images


def _download_thread_attachments(
    creds: google_service.Credentials,
    thread_id: str,
    dest_dir: str,
) -> list[str]:
    """Download full email content from a Gmail thread to dest_dir. Returns filenames.

    Saves:
    - thread.json: structured metadata (headers, text body, HTML body per message)
    - thread.eml: raw RFC 2822 MIME for each message (message_0.eml, message_1.eml, ...)
    - Inline images (image_*.png/jpg)
    - Named attachments (PDFs, ZIPs, etc.)
    """
    gmail = google_service.build_gmail_service(creds)

    thread = _gmail_api_call(lambda: gmail.users().threads().get(userId="me", id=thread_id, format="full").execute())

    downloaded_files: list[str] = []
    thread_metadata: list[dict] = []

    for msg_idx, msg in enumerate(thread.get("messages", [])):
        msg_id = msg["id"]
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        # Extract message metadata
        msg_meta = {
            "message_id": msg_id,
            "from": _extract_header(headers, "From"),
            "to": _extract_header(headers, "To"),
            "cc": _extract_header(headers, "Cc"),
            "subject": _extract_header(headers, "Subject"),
            "date": _extract_header(headers, "Date"),
            "body_text": _extract_body_text(payload),
            "body_html": _extract_body_html(payload),
            "attachments": [],
            "inline_images": [],
        }

        # Download raw RFC 2822 MIME
        try:
            raw_msg = _gmail_api_call(
                lambda mid=msg_id: gmail.users().messages().get(userId="me", id=mid, format="raw").execute()
            )
            raw_data = base64.urlsafe_b64decode(raw_msg["raw"])
            eml_name = f"message_{msg_idx}.eml"
            eml_path = os.path.join(dest_dir, eml_name)
            with open(eml_path, "wb") as f:
                f.write(raw_data)
            downloaded_files.append(eml_name)
            logger.info("Saved raw email: %s (%d bytes)", eml_name, len(raw_data))
        except Exception:
            logger.warning("Could not download raw email for message %s", msg_id)

        # Download inline images
        inline_images = _extract_inline_images(payload)
        for img in inline_images:
            try:
                att = _gmail_api_call(
                    lambda mid=msg_id, aid=img["attachmentId"]: gmail.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=mid, id=aid)
                    .execute()
                )
                data = base64.urlsafe_b64decode(att["data"])
                img_filename = os.path.basename(img["filename"])
                if not img_filename:
                    ext = img["mimeType"].split("/")[-1]
                    img_filename = f"inline_{msg_idx}_{img['cid'] or 'img'}.{ext}"
                img_path = os.path.join(dest_dir, img_filename)
                with open(img_path, "wb") as f:
                    f.write(data)
                downloaded_files.append(img_filename)
                msg_meta["inline_images"].append({"filename": img_filename, "cid": img["cid"]})
                logger.info("Downloaded inline image: %s (%d bytes)", img_filename, len(data))
            except Exception:
                logger.warning("Could not download inline image: %s", img.get("filename"))

        # Download named attachments
        attachment_parts = _extract_attachment_parts(payload)
        for part in attachment_parts:
            filename = part["filename"]
            attachment_id = part["body"]["attachmentId"]

            att = _gmail_api_call(
                lambda mid=msg_id, aid=attachment_id: gmail.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=mid, id=aid)
                .execute()
            )

            data = base64.urlsafe_b64decode(att["data"])

            filename = os.path.basename(filename)
            if not filename:
                continue

            filepath = os.path.join(dest_dir, filename)
            with open(filepath, "wb") as f:
                f.write(data)

            logger.info("Downloaded attachment: %s (%d bytes)", filename, len(data))
            msg_meta["attachments"].append(filename)

            # Handle ZIP files: extract contents alongside the zip
            if filename.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(BytesIO(data)) as zf:
                        for member in zf.namelist():
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

        thread_metadata.append(msg_meta)

    # Write thread metadata as JSON
    meta_path = os.path.join(dest_dir, "thread.json")
    with open(meta_path, "w") as f:
        json.dump({"thread_id": thread_id, "messages": thread_metadata}, f, indent=2)
    downloaded_files.append("thread.json")
    logger.info("Saved thread metadata: %d messages", len(thread_metadata))

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
