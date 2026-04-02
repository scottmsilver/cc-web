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

import hashlib
import json
import os
import threading
import time as _time
import uuid
from typing import Optional

import uvicorn
from cchost import CCHost, CCSession
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.responses import Response as HTTPResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ============================================================
# Global host instance
# ============================================================

host = CCHost(max_sessions=5)


# ============================================================
# REST API
# ============================================================

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="cchost", description="Claude Code as a hosted service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class SendResponse(BaseModel):
    text: str
    is_question: bool = False
    questions: list = []
    role: str = "assistant"


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
    return [
        SessionInfo(
            id=s.id,
            working_dir=s.working_dir,
            state="active",
            created_at=s.created_at.isoformat(),
        )
        for s in host.list()
    ]


@app.get("/api/sessions/{session_id}", response_model=SessionInfo)
def get_session(session_id: str):
    try:
        s = host.get(session_id)
        return SessionInfo(
            id=s.id,
            working_dir=s.working_dir,
            state="active",
            created_at=s.created_at.isoformat(),
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.delete("/api/sessions/{session_id}")
def destroy_session(session_id: str):
    try:
        host.destroy(session_id)
        return {"status": "destroyed"}
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")


@app.post("/api/sessions/{session_id}/send", response_model=SendResponse)
def send_message(session_id: str, req: SendRequest):
    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    r = session.send(req.message, timeout=req.timeout)
    return SendResponse(
        text=r.text,
        is_question=r.is_question,
        questions=[
            {
                "question": q["question"],
                "options": [{"label": o.label, "index": o.index} for o in q["options"]],
            }
            for q in r.questions
        ],
        role=r.role,
    )


@app.post("/api/sessions/{session_id}/answer", response_model=SendResponse)
def answer_question(session_id: str, req: AnswerRequest):
    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")

    r = session.answer(option_index=req.option_index)
    return SendResponse(
        text=r.text,
        is_question=r.is_question,
        questions=[
            {
                "question": q["question"],
                "options": [{"label": o.label, "index": o.index} for o in q["options"]],
            }
            for q in r.questions
        ],
        role=r.role,
    )


@app.get("/api/sessions/{session_id}/files")
def list_files(session_id: str):
    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"files": session.files()}


@app.get("/api/sessions/{session_id}/files/{path:path}")
def download_file(session_id: str, path: str):
    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
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
        else:
            media_type = "application/octet-stream"
        return HTTPResponse(content=data, media_type=media_type)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/sessions/{session_id}/conversation")
def get_conversation(session_id: str):
    try:
        session = host.get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"conversation": session.conversation()}


# ============================================================
# OpenAI-Compatible API (for LibreChat integration)
# ============================================================
#
# LibreChat sends standard OpenAI chat/completions requests.
# We map each conversation to a cchost session using a session
# ID derived from the conversation. The last user message gets
# sent to Claude Code.


# Session pool for OpenAI-compat conversations
_openai_sessions: dict[str, str] = {}  # conversation_hash -> cchost session_id
_openai_lock = threading.Lock()


class OAIMessage(BaseModel):
    role: str
    content: str


class OAIChatRequest(BaseModel):
    model: str = "claude-code"
    messages: list[OAIMessage]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None


def _get_or_create_oai_session(messages: list[OAIMessage]) -> tuple[CCSession, str]:
    """
    Get or create a cchost session for this conversation.

    Strategy: use a hash of the first user message as a stable conversation ID.
    If the conversation is new, create a session. If it exists, reuse it.
    """
    # Use first user message as conversation identifier
    first_user_msg = next((m.content for m in messages if m.role == "user"), "default")
    conv_hash = hashlib.md5(first_user_msg.encode()).hexdigest()[:12]

    with _openai_lock:
        if conv_hash in _openai_sessions:
            session_id = _openai_sessions[conv_hash]
            try:
                return host.get(session_id), conv_hash
            except KeyError:
                del _openai_sessions[conv_hash]

        # Create new session
        session_id = f"lc-{conv_hash}"
        workdir = os.path.expanduser(f"~/cchost-workspace/{session_id}")
        try:
            session = host.create(session_id, working_dir=workdir)
            _openai_sessions[conv_hash] = session_id
            return session, conv_hash
        except ValueError:
            # Session already exists (race condition)
            return host.get(session_id), conv_hash


@app.get("/v1/models")
def list_models():
    """OpenAI-compatible models endpoint."""
    return {
        "object": "list",
        "data": [
            {
                "id": "claude-code",
                "object": "model",
                "created": 1700000000,
                "owned_by": "cchost",
            }
        ],
    }


@app.post("/v1/chat/completions")
def chat_completions(req: OAIChatRequest):
    """
    OpenAI-compatible chat completions endpoint.
    Supports both streaming (SSE) and non-streaming responses.
    """
    # Get the last user message
    last_user_msg = None
    for msg in reversed(req.messages):
        if msg.role == "user":
            last_user_msg = msg.content
            break

    if not last_user_msg:
        raise HTTPException(status_code=400, detail="No user message found")

    # Get or create session
    session, conv_hash = _get_or_create_oai_session(req.messages)

    response_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"

    if req.stream:
        # SSE streaming — send keepalive chunks while Claude works,
        # then send the actual response when done.
        def generate_sse():
            import time as t

            # Send message to Claude (non-blocking — we start it in a thread)
            result_holder = {"response": None, "done": False}

            def run_send():
                result_holder["response"] = session.send(last_user_msg, timeout=900)
                result_holder["done"] = True

            send_thread = threading.Thread(target=run_send, daemon=True)
            send_thread.start()

            # Stream keepalive / progress while waiting
            # Send an initial "thinking" indicator
            thinking_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(_time.time()),
                "model": "claude-code",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(thinking_chunk)}\n\n"

            # Poll until done, sending SSE comments as keepalive every 10s
            while not result_holder["done"]:
                t.sleep(2)
                # SSE comment keeps connection alive (not a data event)
                yield ": keepalive\n\n"

            # Send the actual response
            r = result_holder["response"]
            if r and r.text:
                content_chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": int(_time.time()),
                    "model": "claude-code",
                    "choices": [{"index": 0, "delta": {"content": r.text}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(content_chunk)}\n\n"

            # Finish
            finish_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": int(_time.time()),
                "model": "claude-code",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(finish_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            generate_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    else:
        # Non-streaming — blocks until done
        r = session.send(last_user_msg, timeout=900)
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": int(_time.time()),
            "model": "claude-code",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": r.text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }


# ============================================================
# Chat UI — self-contained HTML
# ============================================================

CHAT_HTML = """<!DOCTYPE html>
<html>
<head>
<title>cchost — Claude Code Chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; height: 100vh; display: flex; flex-direction: column; }
header { padding: 12px 20px; background: #16213e; border-bottom: 1px solid #0f3460; display: flex; align-items: center; gap: 12px; }
header h1 { font-size: 16px; font-weight: 600; color: #e94560; }
header .session-info { font-size: 12px; color: #888; margin-left: auto; }
.tabs { display: flex; gap: 0; background: #16213e; border-bottom: 1px solid #0f3460; }
.tab { padding: 8px 20px; cursor: pointer; color: #888; font-size: 13px; border-bottom: 2px solid transparent; }
.tab.active { color: #e94560; border-bottom-color: #e94560; }
.panel { flex: 1; display: none; flex-direction: column; overflow: hidden; }
.panel.active { display: flex; }
#chat-panel { flex: 1; display: flex; flex-direction: column; }
#messages { flex: 1; overflow-y: auto; padding: 16px; }
.msg { margin-bottom: 12px; max-width: 85%; }
.msg.user { margin-left: auto; }
.msg .bubble { padding: 10px 14px; border-radius: 12px; font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word; }
.msg.user .bubble { background: #0f3460; color: #fff; border-bottom-right-radius: 4px; }
.msg.assistant .bubble { background: #222; color: #e0e0e0; border-bottom-left-radius: 4px; }
.msg.question .bubble { background: #2a1a3e; border: 1px solid #e94560; }
.msg .options { margin-top: 8px; }
.msg .opt-btn { display: block; margin: 4px 0; padding: 6px 12px; background: #16213e; border: 1px solid #0f3460; color: #e0e0e0; border-radius: 6px; cursor: pointer; font-size: 13px; text-align: left; }
.msg .opt-btn:hover { background: #0f3460; border-color: #e94560; }
.msg .label { font-size: 11px; color: #666; margin-bottom: 2px; }
.msg.user .label { text-align: right; }
#input-area { padding: 12px 16px; background: #16213e; border-top: 1px solid #0f3460; display: flex; gap: 8px; }
#msg-input { flex: 1; padding: 10px 14px; background: #222; border: 1px solid #333; border-radius: 8px; color: #fff; font-size: 14px; font-family: inherit; resize: none; }
#msg-input:focus { outline: none; border-color: #e94560; }
#send-btn { padding: 10px 20px; background: #e94560; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; }
#send-btn:hover { background: #c73e54; }
#send-btn:disabled { background: #555; cursor: wait; }
.spinner { display: none; padding: 8px 16px; color: #888; font-size: 13px; }
.spinner.active { display: block; }
/* Files panel */
#files-panel { padding: 16px; overflow-y: auto; }
#files-panel .file-list { font-family: monospace; font-size: 13px; }
#files-panel .file-item { padding: 4px 8px; cursor: pointer; border-radius: 4px; }
#files-panel .file-item:hover { background: #222; color: #e94560; }
#file-content { margin-top: 12px; background: #111; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 12px; white-space: pre-wrap; overflow-y: auto; max-height: 60vh; }
</style>
</head>
<body>
<header>
  <h1>cchost</h1>
  <span style="color:#666">Claude Code Chat</span>
  <span class="session-info" id="session-info">No session</span>
</header>
<div class="tabs">
  <div class="tab active" onclick="switchTab('chat')">Chat</div>
  <div class="tab" onclick="switchTab('files')">Files</div>
</div>
<div class="panel active" id="chat-panel">
  <div id="messages"></div>
  <div class="spinner" id="spinner">Claude is thinking...</div>
  <div id="input-area">
    <textarea id="msg-input" rows="1" placeholder="Type a message..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMsg()}"></textarea>
    <button id="send-btn" onclick="sendMsg()">Send</button>
  </div>
</div>
<div class="panel" id="files-panel">
  <button onclick="loadFiles()" style="padding:8px 16px;background:#0f3460;color:#fff;border:none;border-radius:6px;cursor:pointer;margin-bottom:12px;">Refresh Files</button>
  <div class="file-list" id="file-list"></div>
  <div id="file-content"></div>
</div>

<script>
let sessionId = null;
let isWaitingForAnswer = false;

async function ensureSession() {
  if (sessionId) return;
  const res = await fetch('/api/sessions', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({session_id: 'ui-' + Date.now(), working_dir: '/home/HOMEDIR/cchost-workspace'})});
  const data = await res.json();
  sessionId = data.id;
  document.getElementById('session-info').textContent = sessionId + ' — ' + data.working_dir;
}

function addMsg(role, text, questions) {
  const div = document.createElement('div');
  div.className = 'msg ' + role + (questions ? ' question' : '');
  let html = '<div class="label">' + role + '</div><div class="bubble">' + escapeHtml(text) + '</div>';
  if (questions && questions.length) {
    html += '<div class="options">';
    questions.forEach(q => {
      q.options.forEach(opt => {
        html += '<button class="opt-btn" onclick="answerQ(' + opt.index + ')">' + opt.index + '. ' + escapeHtml(opt.label) + '</button>';
      });
    });
    html += '</div>';
  }
  div.innerHTML = html;
  document.getElementById('messages').appendChild(div);
  div.scrollIntoView({behavior:'smooth'});
}

function escapeHtml(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\\n/g,'<br>');
}

async function sendMsg() {
  const input = document.getElementById('msg-input');
  const msg = input.value.trim();
  if (!msg) return;
  input.value = '';

  await ensureSession();
  addMsg('user', msg);

  const btn = document.getElementById('send-btn');
  const spinner = document.getElementById('spinner');
  btn.disabled = true;
  spinner.className = 'spinner active';

  try {
    const res = await fetch('/api/sessions/' + sessionId + '/send', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({message: msg, timeout: 900})
    });
    const data = await res.json();

    if (data.is_question) {
      isWaitingForAnswer = true;
      addMsg('assistant', data.text, data.questions);
    } else {
      addMsg('assistant', data.text);
    }
  } catch(e) {
    addMsg('assistant', 'Error: ' + e.message);
  }
  btn.disabled = false;
  spinner.className = 'spinner';
}

async function answerQ(idx) {
  const spinner = document.getElementById('spinner');
  spinner.className = 'spinner active';
  document.getElementById('send-btn').disabled = true;

  try {
    const res = await fetch('/api/sessions/' + sessionId + '/answer', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({option_index: idx})
    });
    const data = await res.json();
    addMsg('user', 'Selected option ' + idx);

    if (data.is_question) {
      addMsg('assistant', data.text, data.questions);
    } else {
      isWaitingForAnswer = false;
      addMsg('assistant', data.text);
    }
  } catch(e) {
    addMsg('assistant', 'Error: ' + e.message);
  }
  document.getElementById('send-btn').disabled = false;
  spinner.className = 'spinner';
}

async function loadFiles() {
  if (!sessionId) return;
  const res = await fetch('/api/sessions/' + sessionId + '/files');
  const data = await res.json();
  const list = document.getElementById('file-list');
  list.innerHTML = data.files.map(f =>
    '<div class="file-item" onclick="loadFile(\\'' + f.replace(/'/g,"\\\\'") + '\\')">' + f + '</div>'
  ).join('');
}

async function loadFile(path) {
  const res = await fetch('/api/sessions/' + sessionId + '/files/' + encodeURIComponent(path));
  const text = await res.text();
  document.getElementById('file-content').textContent = text.substring(0, 20000);
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  event.target.classList.add('active');
  document.getElementById(name + '-panel').classList.add('active');
  if (name === 'files') loadFiles();
}
</script>
</body>
</html>""".replace(
    "HOMEDIR", os.environ.get("USER", "user")
)


@app.get("/ui", response_class=HTMLResponse)
def chat_ui():
    return CHAT_HTML


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("Starting cchost server...")
    print("  REST API:  http://localhost:8420/docs")
    print("  Chat UI:   http://localhost:8420/ui")
    print("  LibreChat: http://localhost:3080 (run: cd librechat && docker compose up -d)")
    print("  OpenAI:    http://localhost:8420/v1/chat/completions")
    uvicorn.run(app, host="0.0.0.0", port=8420)
