# cc-web

Web interface for [Claude Code](https://claude.com/claude-code). Wraps the Claude Code CLI in persistent tmux sessions, exposes a REST API, and serves a Next.js frontend for chatting, viewing files, and tracking progress.

## Architecture

```
backend/     Python — session management (tmux), REST API (FastAPI), progress parsing
frontend/    Next.js — chat UI, file viewer, progress panel, terminal view
```

The backend runs Claude Code in tmux sessions, reads responses from Claude's JSONL conversation log (not the TUI), and detects completion via shell hooks. The frontend polls the API for conversation updates, progress events, and file changes.

## Quick start

### Backend

```bash
cd backend
pip install fastapi uvicorn libtmux pydantic
python server.py
# API at http://localhost:8420
# Built-in chat UI at http://localhost:8420/ui
```

### Frontend

```bash
cd frontend
pnpm install
pnpm dev --port 3001
# UI at http://localhost:3001
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CCHOST_CORS_ORIGINS` | `http://localhost:3000,http://localhost:3001` | Allowed CORS origins (comma-separated) |
| `CCHOST_MAX_UPLOAD_BYTES` | `104857600` (100MB) | Max file upload size |
| `NEXT_PUBLIC_CCHOST_API` | `http://localhost:8420` | Backend API URL (frontend) |

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/sessions` | Create a session |
| `GET` | `/api/sessions` | List sessions |
| `GET` | `/api/sessions/{id}` | Get session info |
| `DELETE` | `/api/sessions/{id}` | Destroy session |
| `POST` | `/api/sessions/{id}/runs` | Send a message (async) |
| `GET` | `/api/sessions/{id}/runs/{run_id}` | Poll run status |
| `POST` | `/api/sessions/{id}/answer` | Answer a question from Claude |
| `GET` | `/api/sessions/{id}/progress` | Get progress events |
| `GET` | `/api/sessions/{id}/files` | List files in working directory |
| `GET` | `/api/sessions/{id}/files/{path}` | Download a file |
| `POST` | `/api/sessions/{id}/upload` | Upload files |
| `GET` | `/api/sessions/{id}/jsonl` | Raw JSONL conversation |
| `GET` | `/api/sessions/{id}/conversation` | Parsed conversation history |
| `GET` | `/api/sessions/{id}/terminal` | Terminal output |

## How it works

1. `CCHost` manages a pool of tmux sessions, each running `claude` CLI
2. Messages are sent by writing to the tmux pane
3. Responses are read from Claude's JSONL conversation log (real-time, not TUI scraping)
4. Completion is detected via shell hooks that write `Stop` events to `.cchost-events.jsonl`
5. Questions (permission prompts, multi-select) are detected from both JSONL and tmux screen state
6. The frontend polls `/runs/{id}` for status and `/jsonl` for conversation rendering
