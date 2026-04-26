"""
google_routes — FastAPI router for Gmail/Drive endpoints.

OAuth tokens are managed by the silver-oauth broker (see broker_client.py).
Each request that touches Gmail/Drive must include an `X-Silver-OAuth-Email`
header (or `?silver_oauth_email=...` query param) identifying which connected
Google account to use.

Endpoints:
  GET    /api/auth/silver-oauth/start-url                  URL the browser should redirect to for OAuth
  GET    /api/auth/silver-oauth/status                     List connected Google accounts
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
import re
import threading
import uuid
import zipfile
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path
from typing import Optional

import broker_client
import google_service
from fastapi import APIRouter, HTTPException, Request
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from pydantic import BaseModel, Field

logger = logging.getLogger("cchost")

router = APIRouter()

# All scopes cchost needs. Used when generating the OAuth start URL.
_CCHOST_SCOPES = "gmail.readonly,gmail.compose,drive.file"


def _email_from_request(request: Request) -> str:
    """
    Pick which Google account to use for this request, in order of preference:
      1. X-Silver-OAuth-Email header (frontend explicitly chose one)
      2. silver_oauth_email query param (legacy / link-style)
      3. The signed-in user's own email (auto-wire — assumes their cchost
         identity matches the Google account they want data from)
    """
    email = request.headers.get("x-silver-oauth-email", "") or request.query_params.get("silver_oauth_email", "")
    email = email.strip()
    if not email:
        # Fall back to the signed-in user's email.
        import auth as auth_module

        email = auth_module.current_user(request) or ""
    if not email:
        raise HTTPException(
            status_code=401,
            detail="no Google account selected (no X-Silver-OAuth-Email header and no signed-in user)",
        )
    return email


def _creds_for(request: Request, scope: str) -> Credentials:
    """Fetch fresh broker-issued credentials for the email named by the request."""
    email = _email_from_request(request)
    try:
        return broker_client.credentials_for(email, scope)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"broker not configured: {exc}")


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
    score: float | None = None
    snippet: str = ""


class SemanticSearchRequest(BaseModel):
    query: str
    k: int = 20


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
    draft_url: str = ""


class DraftFromFileRequest(BaseModel):
    session_id: str
    path: str  # session-relative path to a .email.md file


class ParsedEmailMd(BaseModel):
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str = ""
    reply_to: str = ""  # gmail threadId
    in_reply_to: str = ""  # RFC Message-ID
    from_: str = Field(default="", alias="from")
    attachments: list[str] = Field(default_factory=list)
    body_md: str = ""

    model_config = {"populate_by_name": True}


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


@router.get("/api/auth/silver-oauth/start-url")
def silver_oauth_start_url(return_url: str):
    """
    Build the URL the browser should redirect to in order to begin OAuth via
    the silver-oauth broker. The frontend stays agnostic of the broker URL.
    """
    if not return_url:
        raise HTTPException(status_code=400, detail="return_url required")
    try:
        return {"url": broker_client.start_url(return_url, _CCHOST_SCOPES)}
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"broker not configured: {exc}")


@router.get("/api/auth/silver-oauth/status")
def silver_oauth_status():
    """List Google accounts the broker has tokens for."""
    if not broker_client.is_configured():
        return {"configured": False, "accounts": []}
    try:
        accounts = broker_client.list_accounts()
        return {"configured": True, "accounts": accounts}
    except Exception as exc:
        logger.warning("broker /accounts call failed: %s", exc)
        return {"configured": True, "accounts": [], "error": str(exc)}


@router.post("/api/gmail/scan", response_model=list[ThreadSummary])
def gmail_scan(request: Request, req: GmailScanRequest):
    """Search Gmail for draw-related emails and return thread summaries."""
    creds = _creds_for(request, "gmail.readonly")
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
            snippet=first_msg.get("snippet", ""),
        )

    return list(seen_threads.values())


GMAIL_SEARCH_URL = os.environ.get("GMAIL_SEARCH_URL", "http://localhost:8081")


def _strip_gmail_operators(query: str) -> str:
    """Convert Gmail query syntax to natural language for semantic search.

    Keeps values from subject: and from: (useful context).
    Drops filter-only operators (has:, newer_than:, older_than:, in:, is:, label:).
    """
    parts: list[str] = []
    work = query
    # Extract and remove quoted operator values first (subject:"Silver Remodel")
    for match in re.finditer(r'\b(subject|from|to):"([^"]*)"', work):
        parts.append(match.group(2))
    work = re.sub(r'\b(subject|from|to):"[^"]*"', "", work)
    # Extract and remove unquoted operator values (subject:draw, from:scott)
    for match in re.finditer(r"\b(subject|from|to):(\S+)", work):
        parts.append(match.group(2))
    work = re.sub(r"\b(subject|from|to):\S+", "", work)
    # Remove remaining filter-only operators (has:, newer_than:, etc.)
    remainder = re.sub(r'\b\w+:"[^"]*"', "", work)
    remainder = re.sub(r"\b\w+:\S+", "", remainder)
    # Remove boolean operators
    remainder = re.sub(r"\b(AND|OR|NOT)\b", "", remainder, flags=re.IGNORECASE)
    remainder = re.sub(r"\s+", " ", remainder).strip()
    if remainder:
        parts.append(remainder)
    return " ".join(parts)


@router.post("/api/gmail/semantic-search", response_model=list[ThreadSummary])
def gmail_semantic_search(request: Request, req: SemanticSearchRequest):
    """Semantic search via gmail-search engine. Falls back to Gmail API scan on failure."""
    import requests as _requests

    # Strip Gmail query operators for semantic search
    clean_query = _strip_gmail_operators(req.query)
    if not clean_query:
        # Query was entirely operators (e.g., "has:attachment newer_than:90d")
        # Fall back to Gmail API which understands these
        return gmail_scan(request, GmailScanRequest(query=req.query))

    downloaded = _load_downloaded_threads()
    try:
        resp = _requests.get(
            f"{GMAIL_SEARCH_URL}/api/search",
            params={"q": clean_query, "k": req.k},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("gmail-search unavailable (%s), falling back to scan", e)
        # Fall back to Gmail API scan
        return gmail_scan(request, GmailScanRequest(query=req.query))

    results = []
    for r in data.get("results", []):
        # Extract first sender from participants
        participants = r.get("participants", [])
        sender = participants[0] if participants else ""
        # Get snippet from first match
        matches = r.get("matches", [])
        snippet = ""
        if matches:
            snippet = matches[0].get("snippet", "")

        results.append(
            ThreadSummary(
                id=r["thread_id"],
                subject=r.get("subject", ""),
                sender=sender,
                date=r.get("date_last", r.get("date_first", "")),
                message_count=r.get("message_count", 1),
                attachment_count=0,  # gmail-search doesn't return this directly
                downloaded=r["thread_id"] in downloaded,
                score=r.get("score"),
                snippet=snippet,
            )
        )
    return results


@router.post("/api/gmail/search", response_model=list[SearchThreadSummary])
def gmail_search(request: Request, req: GmailSearchRequest):
    """Simple Gmail search for mid-run skill queries."""
    creds = _creds_for(request, "gmail.readonly")
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


class ThreadPreviewMessage(BaseModel):
    from_: str = Field(default="", alias="from")
    to: str = ""
    date: str = ""
    body_text: str = ""

    model_config = {"populate_by_name": True}


class ThreadPreview(BaseModel):
    id: str
    subject: str = ""
    messages: list[ThreadPreviewMessage] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)


@router.get("/api/gmail/thread/{thread_id}/preview", response_model=ThreadPreview)
def gmail_thread_preview(request: Request, thread_id: str):
    """Fetch a compact preview of one Gmail thread (body text + headers)."""
    creds = _creds_for(request, "gmail.readonly")
    gmail = google_service.build_gmail_service(creds)

    thread = _gmail_api_call(lambda: gmail.users().threads().get(userId="me", id=thread_id, format="full").execute())

    tmsgs = thread.get("messages", [])
    subject = ""
    messages: list[ThreadPreviewMessage] = []
    attachments: list[str] = []
    for msg in tmsgs:
        payload = msg.get("payload", {})
        headers = payload.get("headers", [])
        if not subject:
            subject = _extract_header(headers, "Subject")
        messages.append(
            ThreadPreviewMessage(
                **{"from": _extract_header(headers, "From")},
                to=_extract_header(headers, "To"),
                date=_extract_header(headers, "Date"),
                body_text=_extract_body_text(payload),
            )
        )
        for part in _extract_attachment_parts(payload):
            fn = os.path.basename(part.get("filename") or "")
            if fn:
                attachments.append(fn)

    return ThreadPreview(id=thread_id, subject=subject, messages=messages, attachments=attachments)


# ===========================================================================
# COMPOSITE ENDPOINT
# ===========================================================================


@router.post("/api/inbox/analyze/{thread_id}", response_model=AnalyzeResponse)
def analyze_thread(request: Request, thread_id: str):
    """Atomic create-session + download attachments + start analyzer run."""
    # Import host and run_manager from server (avoids circular at module level)
    from server import host, run_manager

    creds = _creds_for(request, "gmail.readonly")

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
def download_thread_attachments(request: Request, session_id: str, thread_id: str):
    """Download all attachments from a Gmail thread into the session working dir."""
    from server import host

    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    creds = _creds_for(request, "gmail.readonly")

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
def create_gmail_draft(request: Request, session_id: str, req: GmailDraftRequest):
    """Create a Gmail draft reply from the session's generated email file."""
    from server import host

    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    creds = _creds_for(request, "gmail.compose")

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


# ---------------------------------------------------------------------------
# .email.md parsing + draft-from-file
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _to_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def _parse_inline_list(v: str) -> list[str]:
    """Parse `[a, b, c]` → ['a','b','c']. Returns [] if not a bracketed list."""
    v = v.strip()
    if not (v.startswith("[") and v.endswith("]")):
        return []
    inner = v[1:-1].strip()
    if not inner:
        return []
    return [part.strip().strip('"').strip("'") for part in inner.split(",") if part.strip()]


def _parse_frontmatter(text: str) -> dict:
    """Lightweight frontmatter parser.
    Supports:
      key: value                     (string — everything after the first colon)
      key: [a, b, c]                 (inline list)
      key:                           (followed by block list items)
        - item
    Multi-colon values like `subject: Re: foo` are preserved as strings.
    Outer single/double quotes are stripped.
    """
    out: dict = {}
    current_list_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.lstrip()
        if current_list_key and stripped.startswith("- "):
            item = stripped[2:].strip().strip('"').strip("'")
            out.setdefault(current_list_key, []).append(item)
            continue
        if ":" not in line:
            raise ValueError(f"Unrecognized frontmatter line: {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"Empty key in line: {line!r}")
        if not value:
            current_list_key = key
            out[key] = []
            continue
        current_list_key = None
        if value.startswith("["):
            out[key] = _parse_inline_list(value)
        else:
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            out[key] = value
    return out


def parse_email_md(text: str) -> ParsedEmailMd:
    """Parse a .email.md file: frontmatter + markdown body."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("Missing frontmatter block (expected leading ---...---)")
    try:
        meta = _parse_frontmatter(m.group(1))
    except ValueError as e:
        raise ValueError(f"Invalid frontmatter: {e}")
    body = text[m.end() :]
    return ParsedEmailMd(
        to=_to_list(meta.get("to")),
        cc=_to_list(meta.get("cc")),
        bcc=_to_list(meta.get("bcc")),
        subject=str(meta.get("subject") or ""),
        reply_to=str(meta.get("reply_to") or ""),
        in_reply_to=str(meta.get("in_reply_to") or ""),
        **{"from": str(meta.get("from") or "")},
        attachments=_to_list(meta.get("attachments")),
        body_md=body.lstrip("\n"),
    )


def _md_to_html(md_text: str) -> str:
    import markdown as _md

    return _md.markdown(md_text, extensions=["extra", "sane_lists", "nl2br"])


def _safe_session_path(session_working_dir: str, rel: str) -> str:
    """Resolve rel inside session working dir, reject traversal."""
    base = os.path.realpath(session_working_dir)
    rel = rel.lstrip("/")
    candidate = os.path.realpath(os.path.join(base, rel))
    if not (candidate == base or candidate.startswith(base + os.sep)):
        raise HTTPException(status_code=400, detail="Path escapes session")
    return candidate


def _build_draft_mime(parsed: ParsedEmailMd, session_dir: str, file_dir: str, thread_headers: dict | None):
    """Build a MIMEMultipart draft. thread_headers carries In-Reply-To/References/subject/to if replying."""
    import mimetypes as _mimetypes
    from email import encoders as _encoders
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText as _MIMEText

    subject = parsed.subject or (thread_headers or {}).get("subject", "")
    to_list = parsed.to or ([thread_headers["to"]] if thread_headers and thread_headers.get("to") else [])
    if not to_list:
        raise HTTPException(status_code=400, detail="No recipient — set `to:` in frontmatter")
    if not subject:
        raise HTTPException(status_code=400, detail="No subject — set `subject:` in frontmatter")

    body_md = parsed.body_md
    body_html = _md_to_html(body_md)

    alt = MIMEMultipart("alternative")
    alt.attach(_MIMEText(body_md, "plain", "utf-8"))
    alt.attach(_MIMEText(body_html, "html", "utf-8"))

    # Resolve attachments relative to the .email.md file
    attach_paths: list[str] = []
    for a in parsed.attachments:
        resolved = _safe_session_path(session_dir, os.path.join(os.path.relpath(file_dir, session_dir), a))
        if not os.path.isfile(resolved):
            raise HTTPException(status_code=400, detail=f"Attachment not found: {a}")
        attach_paths.append(resolved)

    if attach_paths:
        outer = MIMEMultipart("mixed")
        outer.attach(alt)
        for path in attach_paths:
            ctype, _ = _mimetypes.guess_type(path)
            maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
            with open(path, "rb") as f:
                data = f.read()
            part = MIMEBase(maintype, subtype)
            part.set_payload(data)
            _encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=os.path.basename(path))
            outer.attach(part)
        msg = outer
    else:
        msg = alt

    msg["To"] = ", ".join(to_list)
    if parsed.cc:
        msg["Cc"] = ", ".join(parsed.cc)
    if parsed.bcc:
        msg["Bcc"] = ", ".join(parsed.bcc)
    msg["Subject"] = subject
    if parsed.from_:
        msg["From"] = parsed.from_
    if thread_headers and thread_headers.get("message_id"):
        mid = thread_headers["message_id"]
        msg["In-Reply-To"] = mid
        msg["References"] = mid
    elif parsed.in_reply_to:
        msg["In-Reply-To"] = parsed.in_reply_to
        msg["References"] = parsed.in_reply_to
    return msg


@router.post("/api/gmail/draft-from-file", response_model=DraftResponse)
def create_draft_from_file(request: Request, req: DraftFromFileRequest):
    """Create a Gmail draft from a .email.md file in a session."""
    from server import host

    try:
        session = host.get(req.session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    if not req.path.endswith(".email.md"):
        raise HTTPException(status_code=400, detail="Path must end in .email.md")

    abs_path = _safe_session_path(session.working_dir, req.path)
    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail=f"File not found: {req.path}")

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Could not read file: {e}")

    try:
        parsed = parse_email_md(text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    creds = _creds_for(request, "gmail.compose")
    gmail = google_service.build_gmail_service(creds)

    thread_headers: dict | None = None
    thread_id: str | None = None

    if parsed.reply_to:
        thread_id = parsed.reply_to
        thread = _gmail_api_call(
            lambda: gmail.users()
            .threads()
            .get(userId="me", id=thread_id, format="metadata", metadataHeaders=["Subject", "From", "To", "Message-ID"])
            .execute()
        )
        msgs = thread.get("messages", [])
        if msgs:
            headers = msgs[-1].get("payload", {}).get("headers", [])
            orig_subject = _get_header(headers, "Subject")
            if orig_subject and not orig_subject.lower().startswith("re:"):
                orig_subject = f"Re: {orig_subject}"
            thread_headers = {
                "subject": orig_subject,
                "to": _get_header(headers, "From"),
                "message_id": _get_header(headers, "Message-ID"),
            }

    mime_msg = _build_draft_mime(parsed, session.working_dir, os.path.dirname(abs_path), thread_headers)
    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")

    body = {"message": {"raw": raw}}
    if thread_id:
        body["message"]["threadId"] = thread_id

    draft = _gmail_api_call(lambda: gmail.users().drafts().create(userId="me", body=body).execute())

    draft_url = (
        f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"
        if thread_id
        else "https://mail.google.com/mail/u/0/#drafts"
    )
    return DraftResponse(
        draft_id=draft["id"],
        message="Draft created",
        draft_url=draft_url,
    )


@router.post("/api/sessions/{session_id}/drive/doc", response_model=DocResponse)
def create_google_doc(request: Request, session_id: str):
    """Create a Google Doc from the session's audit files."""
    from server import host

    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    creds = _creds_for(request, "drive.file")

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
    creds: Credentials,
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
