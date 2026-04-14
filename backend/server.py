"""
cchost server — REST API + Gradio chat UI.

REST API endpoints:
  POST   /api/sessions                    Create a session
  GET    /api/sessions                    List sessions
  GET    /api/sessions/{id}               Get session info
  DELETE /api/sessions/{id}               Destroy session
  POST   /api/sessions/{id}/send          Send a message
  POST   /api/sessions/{id}/answer        Answer a question
  GET    /api/sessions/{id}/files         List files
  GET    /api/sessions/{id}/files/{path}  Download file
  GET    /api/sessions/{id}/conversation  Get conversation history

Gradio UI at /ui — conversational chat interface with file browser.
"""

import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("cchost")


import uvicorn
from cchost import CCHost, CCSession, TopicManager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.responses import Response as HTTPResponse
from pydantic import BaseModel, Field

# ============================================================
# Global host instance
# ============================================================


host = CCHost(max_sessions=20)
topic_manager = TopicManager(host)


# ============================================================
# REST API
# ============================================================

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="cchost", description="Claude Code as a hosted service")

_cors_origins = os.environ.get(
    "CCHOST_CORS_ORIGINS",
    "*",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from google_routes import router as google_router

app.include_router(google_router)


class CreateSessionRequest(BaseModel):
    session_id: str
    working_dir: str = "/tmp"


class SendRequest(BaseModel):
    message: str
    timeout: int = 600


class AnswerRequest(BaseModel):
    option_index: int = 1


class SessionInfo(BaseModel):
    id: str
    working_dir: str
    state: str
    created_at: str
    title: str = ""
    status: str = ""


class QuestionOptionResponse(BaseModel):
    label: str
    index: int


class QuestionResponse(BaseModel):
    question: str
    options: list[QuestionOptionResponse] = Field(default_factory=list)


class SendResponse(BaseModel):
    text: str
    is_question: bool = False
    questions: list[QuestionResponse] = Field(default_factory=list)
    role: str = "assistant"


class RunResponse(BaseModel):
    run_id: str
    session_id: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    result: Optional[SendResponse] = None
    error: Optional[str] = None
    waiting_for_input: bool = False
    current_question: Optional[QuestionResponse] = None


@dataclass
class RunState:
    run_id: str
    session_id: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    waiting_for_input: bool = False
    current_question: Optional[dict] = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialize_question(question: dict) -> dict:
    return {
        "question": question.get("question", ""),
        "options": [
            {"label": option.label, "description": option.description, "index": option.index}
            for option in question.get("options", [])
        ],
    }


def _serialize_send_response(response) -> dict:
    return {
        "text": response.text,
        "is_question": response.is_question,
        "questions": [_serialize_question(question) for question in response.questions],
        "role": response.role,
    }


def _serialize_run(run: RunState) -> dict:
    return {
        "run_id": run.run_id,
        "session_id": run.session_id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "result": run.result,
        "error": run.error,
        "waiting_for_input": run.waiting_for_input,
        "current_question": run.current_question,
    }


def _is_active_run(run: Optional[RunState]) -> bool:
    return run is not None and run.status in {"pending", "running", "waiting_for_input"}


def _option_indexes_from_question(question: dict) -> list[int]:
    options = question.get("options", []) if isinstance(question, dict) else []
    indexes: list[int] = []
    for option in options:
        if isinstance(option, dict):
            idx = option.get("index")
        else:
            idx = getattr(option, "index", None)
        if isinstance(idx, int):
            indexes.append(idx)
    return sorted(set(indexes))


def _validate_option_index(question: dict, option_index: int) -> None:
    valid_indexes = _option_indexes_from_question(question)
    if not valid_indexes:
        raise HTTPException(status_code=409, detail="Session is not waiting for an answer")

    contiguous = valid_indexes == list(range(1, len(valid_indexes) + 1))
    if option_index not in valid_indexes:
        if contiguous:
            detail = f"option_index must be between 1 and {valid_indexes[-1]}"
        else:
            detail = f"option_index must be one of {valid_indexes}"
        raise HTTPException(status_code=400, detail=detail)


class RunManager:
    """Encapsulates all run state: runs, session-run mapping, progress history, and session slots."""

    def __init__(self):
        self._runs: dict[str, RunState] = {}
        self._session_runs: dict[str, str] = {}
        self._progress_history: dict[str, dict] = {}
        self._session_slots: set[str] = set()
        self._lock = threading.Lock()

    def get_run_for_session(self, session_id: str) -> Optional[RunState]:
        with self._lock:
            run_id = self._session_runs.get(session_id)
            if not run_id:
                return None
            return self._runs.get(run_id)

    def release_session_slot(self, session_id: str) -> None:
        with self._lock:
            self._session_slots.discard(session_id)

    def interrupt_session(self, session_id: str) -> None:
        """Mark any active run for this session as interrupted and release the slot."""
        with self._lock:
            run_id = self._session_runs.get(session_id)
            if run_id:
                run = self._runs.get(run_id)
                if run is not None and _is_active_run(run):
                    run.status = "error"
                    run.error = "Interrupted by user"
                    run.finished_at = _utcnow_iso()
            self._session_slots.discard(session_id)

    def clear_progress_history(self, session_id: str) -> None:
        with self._lock:
            self._progress_history.pop(session_id, None)

    def get_run_payload_for_session(self, session_id: str) -> Optional[dict]:
        with self._lock:
            run_id = self._session_runs.get(session_id)
            if not run_id:
                return None
            run = self._runs.get(run_id)
            if run is None:
                return None
            return _serialize_run(run)

    def require_no_active_run(self, session_id: str) -> None:
        with self._lock:
            run_id = self._session_runs.get(session_id)
            run = self._runs.get(run_id) if run_id else None
            if _is_active_run(run) or session_id in self._session_slots:
                raise HTTPException(status_code=409, detail="A run is already active for this session")
            self._session_slots.add(session_id)
            self._progress_history.pop(session_id, None)
            if run_id and run is not None and not _is_active_run(run):
                self._session_runs.pop(session_id, None)

    def claim_waiting_run_for_answer(self, session_id: str, option_index: int) -> RunState:
        with self._lock:
            run_id = self._session_runs.get(session_id)
            run = self._runs.get(run_id) if run_id else None
            if run is None or not _is_active_run(run):
                raise HTTPException(status_code=409, detail="Session is not waiting for an answer")
            if run.status != "waiting_for_input":
                raise HTTPException(status_code=409, detail="Run is active and not waiting for input")
            if run.current_question is None:
                raise HTTPException(status_code=409, detail="Session is not waiting for an answer")
            _validate_option_index(run.current_question, option_index)
            self._mark_run_running(run)
            run.result = None
            run.current_question = None
            return run

    def create_run(self, session_id: str) -> RunState:
        with self._lock:
            active_run_id = self._session_runs.get(session_id)
            active_run = self._runs.get(active_run_id) if active_run_id else None
            if _is_active_run(active_run) or session_id in self._session_slots:
                raise HTTPException(status_code=409, detail="A run is already active for this session")

            run_id = uuid.uuid4().hex
            run = RunState(
                run_id=run_id,
                session_id=session_id,
                status="pending",
                started_at=_utcnow_iso(),
            )
            self._runs[run_id] = run
            self._session_runs[session_id] = run_id
            self._progress_history.pop(session_id, None)
            self._session_slots.add(session_id)
            return run

    def get_run(self, run_id: str) -> Optional[RunState]:
        with self._lock:
            return self._runs.get(run_id)

    def destroy_session(self, session_id: str) -> None:
        with self._lock:
            run_id = self._session_runs.pop(session_id, None)
            if run_id:
                self._runs.pop(run_id, None)
            self._progress_history.pop(session_id, None)
            self._session_slots.discard(session_id)

    def merge_progress_snapshot(self, session_id: str, run_id: Optional[str], snapshot_data: dict) -> dict:
        with self._lock:
            history = self._progress_history.get(session_id)
            if history is None or history.get("run_id") != run_id:
                merged_events = list(snapshot_data.get("events", []))
                merged_milestones = list(snapshot_data.get("milestones", []))
            else:
                merged_events = list(history.get("events", []))
                for event in snapshot_data.get("events", []):
                    if event not in merged_events:
                        merged_events.append(event)

                merged_milestones = list(history.get("milestones", []))
                for milestone in snapshot_data.get("milestones", []):
                    if milestone not in merged_milestones:
                        merged_milestones.append(milestone)

            merged_snapshot = dict(snapshot_data)
            merged_snapshot["events"] = merged_events
            merged_snapshot["milestones"] = merged_milestones
            self._progress_history[session_id] = {
                "run_id": run_id,
                "events": merged_events,
                "milestones": merged_milestones,
            }
            return merged_snapshot

    @staticmethod
    def _mark_run_running(run: RunState) -> None:
        run.status = "running"
        run.finished_at = None
        run.error = None
        run.waiting_for_input = False
        run.current_question = None

    def execute_send_run(self, run_id: str, session, message: str, timeout: int) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            self._mark_run_running(run)

        try:
            response = session.send(message, timeout=timeout)
        except Exception as exc:
            with self._lock:
                run = self._runs.get(run_id)
                if run is None:
                    return
                run.status = "error"
                run.error = str(exc)
                run.finished_at = _utcnow_iso()
                self._session_slots.discard(run.session_id)
            return

        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            self._apply_response_to_run(run, response)
            if run.status in {"completed", "error"}:
                self._session_slots.discard(run.session_id)

    def continue_run_with_answer(self, run: RunState, session, option_index: int) -> Optional:
        try:
            response = session.answer(option_index=option_index)
        except Exception as exc:
            with self._lock:
                current = self._runs.get(run.run_id)
                if current is not None:
                    current.status = "error"
                    current.error = str(exc)
                    current.finished_at = _utcnow_iso()
                    self._session_slots.discard(current.session_id)
            return None

        with self._lock:
            current = self._runs.get(run.run_id)
            if current is not None:
                self._apply_response_to_run(current, response)
                if current.status in {"completed", "error"}:
                    self._session_slots.discard(current.session_id)

        return response

    @staticmethod
    def _apply_response_to_run(run: RunState, response) -> None:
        run.result = _serialize_send_response(response)
        if response.is_question and response.questions:
            run.status = "waiting_for_input"
            run.waiting_for_input = True
            run.current_question = _serialize_question(response.questions[0])
            run.finished_at = None
            return

        run.status = "completed"
        run.waiting_for_input = False
        run.current_question = None
        run.finished_at = _utcnow_iso()


run_manager = RunManager()


def _get_session_or_404(session_id: str):
    try:
        return host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


def _get_current_question(session: CCSession) -> Optional[dict]:
    question = session.current_question()
    if isinstance(question, dict):
        return question
    return None


@app.post("/api/sessions", response_model=SessionInfo)
def create_session(req: CreateSessionRequest):
    try:
        session = host.create(req.session_id, working_dir=req.working_dir)
        return SessionInfo(
            id=session.id,
            working_dir=session.working_dir,
            state="idle",
            created_at=session.created_at.isoformat(),
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/sessions", response_model=list[SessionInfo])
def list_sessions():
    results = []
    for s in host.list():
        summary = s.summary()
        results.append(
            SessionInfo(
                id=s.id,
                working_dir=s.working_dir,
                state="dormant" if s.is_dormant else "active",
                created_at=s.created_at.isoformat(),
                title=summary.get("title", ""),
                status=summary.get("status", ""),
            )
        )
    return results


@app.get("/api/sessions/{session_id}", response_model=SessionInfo)
def get_session(session_id: str):
    try:
        # Note: get() triggers lazy resume for dormant sessions
        s = host.get(session_id)
        summary = s.summary()
        return SessionInfo(
            id=s.id,
            working_dir=s.working_dir,
            state="dormant" if s.is_dormant else "active",
            created_at=s.created_at.isoformat(),
            title=summary.get("title", ""),
            status=summary.get("status", ""),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.delete("/api/sessions/{session_id}")
def destroy_session(session_id: str):
    try:
        host.destroy(session_id)
        run_manager.destroy_session(session_id)
        return {"status": "destroyed"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/api/sessions/{session_id}/interrupt")
def interrupt_session(session_id: str):
    session = _get_session_or_404(session_id)
    try:
        session.send_keys("Escape")
        run_manager.interrupt_session(session_id)
        return {"status": "interrupted"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/sessions/{session_id}/runs", response_model=RunResponse)
def create_run(session_id: str, req: SendRequest):
    session = _get_session_or_404(session_id)
    _record_session_activity(session_id)
    run = run_manager.create_run(session_id)

    worker = threading.Thread(
        target=run_manager.execute_send_run,
        args=(run.run_id, session, req.message, req.timeout),
        daemon=True,
    )
    worker.start()

    return RunResponse(**_serialize_run(run))


@app.post("/api/sessions/{session_id}/queue")
def queue_message(session_id: str, req: SendRequest):
    """Queue a message to Claude while a run is already active.

    Sends text directly to the tmux pane without acquiring the lock or
    creating a new run.  Claude Code CLI natively accepts typed input
    while working and processes it once the current turn finishes.
    The existing progress polling will pick up the response.
    """
    _record_session_activity(session_id)
    session = _get_session_or_404(session_id)
    try:
        result = session.queue_message(req.message)
        return result
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.get("/api/sessions/{session_id}/runs/{run_id}", response_model=RunResponse)
def get_run(session_id: str, run_id: str):
    _get_session_or_404(session_id)
    run = run_manager.get_run(run_id)
    if run is None or run.session_id != session_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunResponse(**_serialize_run(run))


@app.get("/api/sessions/{session_id}/progress")
def get_progress(session_id: str):
    session = _get_session_or_404(session_id)
    run_payload = run_manager.get_run_payload_for_session(session_id)
    snapshot = session.progress_snapshot()
    raw_snapshot = asdict(snapshot)

    if run_payload is not None and run_payload["status"] == "pending":
        snapshot_data = raw_snapshot
    else:
        snapshot_data = run_manager.merge_progress_snapshot(
            session_id,
            run_payload["run_id"] if run_payload is not None else None,
            raw_snapshot,
        )
    # If there's a question on screen but no active run, include it
    # so the UI can show interactive answer buttons
    pending_question = None
    if raw_snapshot.get("is_question") and run_payload is None:
        q = session.current_question()
        if q:
            pending_question = _serialize_question(q)

    return {
        "snapshot": snapshot_data,
        "run": run_payload,
        "pending_question": pending_question,
    }


@app.get("/api/sessions/{session_id}/subagents")
def get_subagents(session_id: str):
    session = _get_session_or_404(session_id)
    return {"subagents": session.subagents()}


@app.post("/api/sessions/{session_id}/send", response_model=SendResponse)
def send_message(session_id: str, req: SendRequest):
    _record_session_activity(session_id)
    session = _get_session_or_404(session_id)
    run_manager.require_no_active_run(session_id)
    try:
        response = session.send(req.message, timeout=req.timeout)
    finally:
        run_manager.release_session_slot(session_id)
    return SendResponse(**_serialize_send_response(response))


@app.post("/api/sessions/{session_id}/answer", response_model=SendResponse)
def answer_question(session_id: str, req: AnswerRequest):
    session = _get_session_or_404(session_id)
    run = run_manager.get_run_for_session(session_id)

    if _is_active_run(run):
        claimed_run = run_manager.claim_waiting_run_for_answer(session_id, req.option_index)
        response = run_manager.continue_run_with_answer(claimed_run, session, req.option_index)
        if response is None:
            raise HTTPException(status_code=500, detail="Failed to process answer")
    else:
        run_manager.require_no_active_run(session_id)
        try:
            question = _get_current_question(session)
            if question is None:
                raise HTTPException(status_code=409, detail="Session is not waiting for an answer")
            _validate_option_index(question, req.option_index)
            response = session.answer(option_index=req.option_index)
        finally:
            run_manager.release_session_slot(session_id)

    return SendResponse(**_serialize_send_response(response))


class BtwRequest(BaseModel):
    question: str


@app.post("/api/sessions/{session_id}/btw")
def btw(session_id: str, req: BtwRequest):
    """Ask a /btw side question — ephemeral, doesn't enter conversation history."""
    session = _get_session_or_404(session_id)
    try:
        answer = session.btw(req.question)
        return {"answer": answer, "timed_out": not bool(answer)}
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/sessions/{session_id}/gmail/suggestions")
def gmail_suggestions(session_id: str):
    """Return Gmail search suggestions. Returns cache, kicks off background generation if stale."""
    session = _get_session_or_404(session_id)
    import json as _json

    cache_path = os.path.join(session.working_dir, "suggested-searches.json")
    # Return cache if available
    try:
        with open(cache_path) as f:
            data = _json.load(f)
        if data.get("suggestions"):
            return {"suggestions": data["suggestions"]}
    except (FileNotFoundError, _json.JSONDecodeError):
        pass

    # No cache yet — the background summary cycle will generate it within ~60s
    return {"suggestions": [], "generating": True}


@app.get("/api/sessions/{session_id}/gmail/suggestions/debug")
def gmail_suggestions_debug(session_id: str):
    """Debug: synchronously generate suggestions and return diagnostics."""
    session = _get_session_or_404(session_id)
    diag = {"idle": False, "btw_response": None, "error": None, "suggestions": []}
    try:
        diag["idle"] = session._is_tmux_idle()
        # Don't gate on idle — btw() handles its own lock
        response = session.btw(
            "Based on our conversation, suggest 3 Gmail searches. "
            'Respond ONLY with JSON: {"suggestions": [{"label": "Topic Name", "query": "subject:topic"}]}'
        )
        diag["btw_response"] = response[:500] if response else None
        if response:
            import json as _json

            flat = " ".join(response.split())
            candidates = []
            depth = 0
            obj_start = -1
            for i, ch in enumerate(flat):
                if ch == "{":
                    if depth == 0:
                        obj_start = i
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0 and obj_start >= 0:
                        candidates.append(flat[obj_start : i + 1])
                        obj_start = -1
            diag["json_candidates"] = len(candidates)
            for candidate in reversed(candidates):
                try:
                    parsed = _json.loads(candidate)
                    if parsed.get("suggestions"):
                        diag["suggestions"] = parsed["suggestions"]
                        break
                except _json.JSONDecodeError:
                    pass
    except Exception as e:
        diag["error"] = str(e)
    return diag


@app.post("/api/sessions/{session_id}/refresh-summary")
def refresh_summary(session_id: str):
    """Trigger a single session's summary refresh (calls /btw in background)."""
    session = _get_session_or_404(session_id)
    try:
        result = session.generate_summary()
        return {"title": result.get("title", ""), "status": result.get("status", "")}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sessions/{session_id}/toggle")
def toggle_option(session_id: str, req: AnswerRequest):
    """Toggle a checkbox in a multi-select question."""
    session = _get_session_or_404(session_id)
    if not session.question_status():
        raise HTTPException(status_code=409, detail="No question is currently displayed")
    session.toggle_option(req.option_index)
    return {"ok": True}


@app.post("/api/sessions/{session_id}/submit-multiselect", response_model=SendResponse)
def submit_multiselect(session_id: str):
    """Submit a multi-select question after toggling options."""
    session = _get_session_or_404(session_id)
    if not session.question_status():
        raise HTTPException(status_code=409, detail="No question is currently displayed")
    response = session.submit_multiselect()
    return SendResponse(**_serialize_send_response(response))


@app.get("/api/sessions/{session_id}/jsonl")
def get_jsonl(session_id: str):
    """Return the raw JSONL transcript entries."""
    session = _get_session_or_404(session_id)
    return session.raw_transcript()


@app.get("/api/sessions/{session_id}/files")
def list_files(session_id: str):
    session = _get_session_or_404(session_id)
    return {"files": session.files()}


@app.get("/api/sessions/{session_id}/eml-html/{path:path}")
def eml_html(session_id: str, path: str):
    """Serve the rendered HTML body of an EML file with inline images resolved."""
    import email
    import email.policy

    session = _get_session_or_404(session_id)
    try:
        data = session.read_file(path)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))

    msg = email.message_from_bytes(data, policy=email.policy.default)

    # Find HTML body and inline images
    html_body = None
    images: dict[str, str] = {}  # cid -> data URL

    def _walk(part):
        nonlocal html_body
        ct = part.get_content_type()
        if part.is_multipart():
            for child in part.iter_parts():
                _walk(child)
            return
        payload = part.get_payload(decode=True)
        if payload is None:
            return
        if ct == "text/html" and html_body is None:
            charset = part.get_content_charset() or "utf-8"
            try:
                html_body = payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                html_body = payload.decode("utf-8", errors="replace")
        elif ct.startswith("image/"):
            import base64 as b64mod

            cid = (part.get("Content-ID") or "").strip("<>")
            if cid:
                data_url = f"data:{ct};base64,{b64mod.b64encode(payload).decode('ascii')}"
                images[cid] = data_url

    _walk(msg)

    if not html_body:
        return HTTPResponse(content="<p>No HTML body</p>", media_type="text/html")

    # Replace cid: references
    for cid, data_url in images.items():
        html_body = html_body.replace(f"cid:{cid}", data_url)

    # Fetch external images and inline them (uses stored OAuth if available)
    import re as _re

    import requests as _req

    external_urls = set(_re.findall(r'src="(https?://[^"]+)"', html_body))
    if external_urls:
        # Try to use Google OAuth token for googleusercontent.com URLs
        headers = {}
        try:
            tm = google_service.TokenManager()
            creds = tm.load()
            if creds:
                creds = tm.refresh_if_needed(creds)
                if creds and hasattr(creds, "token"):
                    headers["Authorization"] = f"Bearer {creds.token}"
        except Exception:
            pass

        for ext_url in external_urls:
            try:
                use_auth = "google" in ext_url and headers
                r = _req.get(ext_url, headers=headers if use_auth else {}, timeout=5)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                    import base64 as _b64

                    ct = r.headers["content-type"].split(";")[0]
                    data_url = f"data:{ct};base64,{_b64.b64encode(r.content).decode('ascii')}"
                    html_body = html_body.replace(ext_url, data_url)
            except Exception:
                pass  # Leave original URL if fetch fails

    # Wrap with Gmail-like font
    full_html = f"""<!DOCTYPE html>
<html><head><style>
body {{ font-family: Roboto, 'Google Sans', Arial, sans-serif; font-size: 14px; line-height: 1.5; color: #202124; margin: 8px; }}
img {{ max-width: 100%; }}
</style></head>
<body>{html_body}</body></html>"""

    return HTTPResponse(content=full_html, media_type="text/html")


@app.get("/api/sessions/{session_id}/eml/{path:path}")
def parse_eml(session_id: str, path: str):
    """Parse an EML file server-side and return structured parts as JSON."""
    import email
    import email.policy

    session = _get_session_or_404(session_id)
    try:
        data = session.read_file(path)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))

    msg = email.message_from_bytes(data, policy=email.policy.default)

    def _extract_parts(part, index=0):
        """Recursively extract MIME parts."""
        ct = part.get_content_type()
        result = {
            "index": index,
            "content_type": ct,
            "filename": part.get_filename(),
            "charset": part.get_content_charset(),
            "is_multipart": part.is_multipart(),
            "children": [],
        }
        if part.is_multipart():
            for i, child in enumerate(part.iter_parts()):
                result["children"].append(_extract_parts(child, i))
        else:
            payload = part.get_payload(decode=True)
            if payload is None:
                result["size"] = 0
            else:
                result["size"] = len(payload)
                if ct.startswith("text/"):
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        result["body"] = payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        result["body"] = payload.decode("utf-8", errors="replace")
                elif ct.startswith("image/"):
                    import base64 as b64mod

                    result["data_url"] = f"data:{ct};base64,{b64mod.b64encode(payload).decode('ascii')}"
                    # Also check Content-ID for cid: references
                    cid = part.get("Content-ID", "")
                    if cid:
                        result["cid"] = cid.strip("<>")
        return result

    # Extract headers
    headers = {}
    for key in ("From", "To", "Cc", "Subject", "Date", "Message-ID"):
        val = msg.get(key)
        if val:
            headers[key.lower()] = str(val)

    parts = _extract_parts(msg)

    # Collect flattened leaf parts for convenience
    leaves = []

    def _flatten(p):
        if p["children"]:
            for c in p["children"]:
                _flatten(c)
        else:
            leaves.append(p)

    _flatten(parts)

    # Find text and HTML bodies
    text_body = None
    html_body = None
    for leaf in leaves:
        if leaf["content_type"] == "text/plain" and not leaf.get("filename") and text_body is None:
            text_body = leaf.get("body", "")
        if leaf["content_type"] == "text/html" and not leaf.get("filename") and html_body is None:
            html_body = leaf.get("body", "")

    # Resolve cid: references in HTML
    if html_body:
        for leaf in leaves:
            if leaf.get("cid") and leaf.get("data_url"):
                html_body = html_body.replace(f"cid:{leaf['cid']}", leaf["data_url"])

    return {
        "headers": headers,
        "text_body": text_body,
        "html_body": html_body,
        "parts": parts,
        "leaves": leaves,
    }


@app.get("/api/sessions/{session_id}/file-mtime/{path:path}")
def file_mtime(session_id: str, path: str):
    """Return the modification time of a file (lightweight staleness check)."""
    session = _get_session_or_404(session_id)
    clean_path = path.strip("/")
    target = os.path.join(session.working_dir, clean_path) if clean_path else session.working_dir
    resolved = os.path.realpath(target)
    workdir_real = os.path.realpath(session.working_dir)
    if resolved != workdir_real and not resolved.startswith(workdir_real + os.sep):
        raise HTTPException(status_code=400, detail="Path traversal blocked")
    if not os.path.exists(resolved):
        raise HTTPException(status_code=404, detail="File not found")
    return {"mtime": os.path.getmtime(resolved), "size": os.path.getsize(resolved)}


@app.get("/api/sessions/{session_id}/files/{path:path}")
def download_file(session_id: str, path: str):
    session = _get_session_or_404(session_id)

    # Normalize: treat "/" or empty as root of working dir
    clean_path = path.strip("/")

    # If path is a directory, list its contents
    target = os.path.join(session.working_dir, clean_path) if clean_path else session.working_dir
    resolved = os.path.realpath(target)
    workdir_real = os.path.realpath(session.working_dir)
    if resolved != workdir_real and not resolved.startswith(workdir_real + os.sep):
        raise HTTPException(status_code=400, detail="Path traversal blocked")
    if os.path.isdir(resolved):
        entries = []
        for name in sorted(os.listdir(resolved)):
            if name.startswith("."):
                continue  # hide dotfiles
            full = os.path.join(resolved, name)
            rel = os.path.join(clean_path, name) if clean_path else name
            entries.append({"name": name, "path": rel, "is_dir": os.path.isdir(full)})
        return {"directory": path, "entries": entries}

    try:
        data = session.read_file(path)
        # Guess content type
        if path.endswith(".pdf"):
            media_type = "application/pdf"
        elif path.endswith(".xlsx"):
            media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif path.endswith(".json"):
            media_type = "application/json"
        elif path.endswith(".md") or path.endswith(".txt"):
            media_type = "text/plain"
        elif path.endswith(".eml"):
            media_type = "message/rfc822"
        else:
            media_type = "application/octet-stream"
        return HTTPResponse(content=data, media_type=media_type)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.delete("/api/sessions/{session_id}/gmail/download/{thread_id}")
def remove_downloaded_thread(session_id: str, thread_id: str):
    """Remove downloaded Gmail thread attachments and clean up gmail-source.json."""
    session = _get_session_or_404(session_id)
    import json
    import shutil

    # Remove the inbox/{thread_id}/ directory (path traversal protection)
    inbox_dir = os.path.join(session.working_dir, "inbox", thread_id)
    resolved = os.path.realpath(inbox_dir)
    if not resolved.startswith(os.path.realpath(session.working_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid thread_id")
    if os.path.isdir(resolved):
        shutil.rmtree(resolved)

    # Remove this thread from gmail-source.json
    source_path = os.path.join(session.working_dir, "gmail-source.json")
    try:
        with open(source_path) as f:
            source = json.load(f)
        source["thread_ids"] = [t for t in source.get("thread_ids", []) if t != thread_id]
        if source["thread_ids"]:
            with open(source_path, "w") as f:
                json.dump(source, f, indent=2)
        else:
            os.remove(source_path)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    return {"status": "removed"}


_MAX_UPLOAD_BYTES = int(os.environ.get("CCHOST_MAX_UPLOAD_BYTES", 100 * 1024 * 1024))  # 100 MB


@app.post("/api/sessions/{session_id}/upload")
async def upload_file(session_id: str, request: Request):
    """Upload a file to the session's working directory."""

    session = _get_session_or_404(session_id)
    os.makedirs(session.working_dir, exist_ok=True)

    # Parse multipart form data
    form = await request.form()
    uploaded = []
    for key in form:
        file = form[key]
        if hasattr(file, "filename") and file.filename:
            safe_name = os.path.basename(file.filename)
            if not safe_name or ".." in safe_name:
                raise HTTPException(status_code=400, detail=f"Invalid filename: {file.filename}")
            dest = os.path.join(session.working_dir, safe_name)
            content = await file.read()
            size = len(content)
            if size > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File {safe_name} exceeds max upload size ({_MAX_UPLOAD_BYTES} bytes)",
                )
            with open(dest, "wb") as f:
                f.write(content)
            uploaded.append(safe_name)
            logger.info("Uploaded %s (%d bytes) to %s", safe_name, size, dest)

    if not uploaded:
        logger.warning("Upload request for session %s had no files", session_id)

    return {"uploaded": uploaded, "working_dir": session.working_dir}


@app.get("/api/sessions/{session_id}/terminal")
def get_terminal(session_id: str, lines: int = 0):
    """Capture tmux pane output. lines=0 (default) returns full scrollback history."""
    session = _get_session_or_404(session_id)
    return {"terminal": session.terminal_capture(lines)}


SLASH_COMMANDS = [
    {"command": "/add-dir", "description": "Add a new working directory"},
    {"command": "/clear", "description": "Clear conversation history"},
    {"command": "/compact", "description": "Compact conversation to reduce context"},
    {"command": "/cost", "description": "Show token usage and costs"},
    {"command": "/fast", "description": "Toggle fast mode"},
    {"command": "/help", "description": "Show help and available commands"},
    {"command": "/hooks", "description": "View hook configurations"},
    {"command": "/init", "description": "Initialize a CLAUDE.md file"},
    {"command": "/keybindings", "description": "Open keybindings configuration"},
    {"command": "/mcp", "description": "Manage MCP servers"},
    {"command": "/memory", "description": "Edit Claude memory files"},
    {"command": "/model", "description": "Set the AI model"},
    {"command": "/permissions", "description": "Manage tool permission rules"},
    {"command": "/plan", "description": "Enable plan mode or view plan"},
    {"command": "/rename", "description": "Rename the conversation"},
    {"command": "/resume", "description": "Resume a previous conversation"},
    {"command": "/review", "description": "Pre-landing PR review"},
    {"command": "/rewind", "description": "Restore to a previous point"},
    {"command": "/skills", "description": "List available skills"},
    {"command": "/stats", "description": "Show usage statistics"},
    {"command": "/status", "description": "Show status info"},
    {"command": "/tasks", "description": "List background tasks"},
    {"command": "/theme", "description": "Change the theme"},
    {"command": "/vim", "description": "Toggle Vim editing mode"},
    {"command": "/voice", "description": "Toggle voice mode"},
]


@app.get("/api/commands")
def list_commands():
    """Return the list of known slash commands for autocomplete."""
    return {"commands": SLASH_COMMANDS}


class SlashCommandRequest(BaseModel):
    command: str


@app.post("/api/sessions/{session_id}/command")
def run_command(session_id: str, req: SlashCommandRequest):
    """Execute a slash command and capture the result."""
    session = _get_session_or_404(session_id)
    try:
        result = session.slash_command(req.command)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/api/sessions/{session_id}/conversation")
def get_conversation(session_id: str):
    session = _get_session_or_404(session_id)
    return {"conversation": session.conversation()}


_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/ui")
def chat_ui():
    return FileResponse(os.path.join(_STATIC_DIR, "chat.html"), media_type="text/html")


# ============================================================
# Topics API
# ============================================================


class CreateTopicRequest(BaseModel):
    name: str


class TopicConversationInfo(BaseModel):
    id: str
    session_id: str
    started_at: str
    title: str = ""
    status: str = ""


class TopicInfo(BaseModel):
    name: str
    slug: str
    created_at: str
    conversations: list[TopicConversationInfo] = []


@app.get("/api/topics", response_model=list[TopicInfo])
def list_topics():
    return topic_manager.list_topics()


@app.post("/api/topics", response_model=TopicInfo)
def create_topic(req: CreateTopicRequest):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Topic name cannot be empty")
    return topic_manager.create_topic(req.name.strip())


@app.get("/api/topics/{slug}", response_model=TopicInfo)
def get_topic(slug: str):
    try:
        return topic_manager.get_topic(slug)
    except KeyError:
        raise HTTPException(status_code=404, detail="Topic not found")


@app.delete("/api/topics/{slug}")
def delete_topic(slug: str):
    try:
        topic_manager.delete_topic(slug)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="Topic not found")
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/api/topics/{slug}/conversations")
def start_topic_conversation(slug: str):
    try:
        session = topic_manager.start_conversation(slug)
        topic = topic_manager.get_topic(slug)
        conv = topic["conversations"][-1]
        return {"session_id": session.id, "conversation_id": conv["id"]}
    except KeyError:
        raise HTTPException(status_code=404, detail="Topic not found")


@app.post("/api/topics/{slug}/conversations/{conv_id}/resume")
def resume_topic_conversation(slug: str, conv_id: str):
    try:
        session = topic_manager.resume_conversation(slug, conv_id)
        return {"session_id": session.id}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/topics/{slug}/generate-context")
def generate_topic_context(slug: str):
    try:
        content = topic_manager.generate_context(slug)
        return {"content": content, "generated": bool(content)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Topic not found")


# ============================================================
# Background summary refresh
# ============================================================


_last_session_activity: dict[str, float] = {}


def _record_session_activity(session_id: str) -> None:
    """Record that a session was recently active (called from run endpoints)."""
    _last_session_activity[session_id] = time.time()


def _background_summary_refresh():
    """Periodically refresh session summaries via /btw in the background."""
    while True:
        time.sleep(60)
        try:
            for session in host.list():
                # Skip sessions that aren't idle
                if not session._is_tmux_idle():
                    continue
                # Skip sessions that were active recently (< 30s ago)
                # to avoid /btw racing with user messages
                last_active = _last_session_activity.get(session.id, 0)
                if time.time() - last_active < 30:
                    continue
                try:
                    session.generate_summary()
                except Exception:
                    pass
                try:
                    session.generate_gmail_suggestions()
                except Exception:
                    pass
        except Exception:
            pass


_summary_thread = threading.Thread(target=_background_summary_refresh, daemon=True)
_summary_thread.start()


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("Starting cchost server...")
    print("  REST API:  http://localhost:8420/docs")
    print("  Chat UI:   http://localhost:8420/ui")
    uvicorn.run(app, host="0.0.0.0", port=8420)
