# cc-web

Web interface for [Claude Code](https://claude.com/claude-code). Wraps the Claude Code CLI in persistent tmux sessions, exposes a REST API, and serves a Next.js frontend for chatting, viewing files, and managing Gmail-connected workflows.

Sessions survive server restarts. Upload files, paste images, connect Gmail, browse folders, view PDFs and emails, all from the browser.

## Architecture

```
backend/     Python (FastAPI) — session management, REST API, Gmail/Drive OAuth, EML parsing
frontend/    Next.js — chat UI, session picker, file/folder viewer, EML viewer, progress panel
```

The backend runs Claude Code in tmux sessions, reads responses from Claude's JSONL conversation log (not the TUI), and detects completion via shell hooks. Sessions are persisted to `~/.cchost/sessions.json` so they survive restarts and resume with `claude --resume`.

The frontend auto-detects the API host from the browser's URL, so it works from localhost, LAN IPs, hostnames, and Tailscale HTTPS without configuration.

## Quick start

```bash
# Backend
cd backend
pip install fastapi uvicorn libtmux pydantic google-auth google-auth-oauthlib google-api-python-client
python server.py
# API at http://localhost:8420

# Frontend
cd frontend
pnpm install
pnpm dev --port 3000
# UI at http://localhost:3000
```

### Tailscale HTTPS (optional)

For HTTPS access from any device on your tailnet:

```bash
sudo tailscale serve --bg --https=443 http://localhost:3000    # frontend
sudo tailscale serve --bg --https=8443 http://localhost:8420   # backend
# Frontend: https://yourhost.tail*.ts.net
# Backend:  https://yourhost.tail*.ts.net:8443
```

## Features

### Session persistence
Sessions survive server restarts. On startup, dormant sessions are loaded from the manifest and lazily resumed (tmux + `claude --resume`) on first access. Working directory files are preserved.

### Chat input
- Rich paste: HTML from docs/web pages auto-converts to markdown
- Image paste: screenshots from clipboard upload as file attachments
- File upload with progress indicator (Apple-style pie chart)
- Drag and drop files
- `@filename` autocomplete for referencing session files
- `/command` autocomplete for Claude Code slash commands
- All attachments shown as removable chips before sending

### File and folder viewer
- PDF rendering (pdfjs, paginated)
- Spreadsheet viewer (xlsx, multi-sheet tabs)
- EML email viewer (server-side MIME parsing, HTML/text/parts/headers tabs, inline image resolution)
- Gmail thread conversation view (thread.json)
- Folder navigation with breadcrumbs and file type icons
- ZIP contents listing
- Markdown rendering
- Image viewer with gallery
- Resizable split between file list and viewer

### Gmail integration
- OAuth connect flow (works with Tailscale HTTPS for sensitive scopes)
- Scan inbox for contractor draw requests
- Search Gmail threads
- Download full email content: thread metadata (thread.json), raw MIME (.eml), inline images, and named attachments
- Gmail attachment picker in chat input
- Create Gmail drafts and Google Docs from session files

### Chat rendering
- `@./file` references in messages render as clickable chips with file icons
- Image references show thumbnails
- Directory references show folder icon, click opens folder view
- Markdown rendering for assistant messages with file linking

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CCHOST_CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated, or `*`) |
| `CCHOST_MAX_UPLOAD_BYTES` | `104857600` (100MB) | Max file upload size |
| `CCHOST_API_URL` | `http://localhost:8420` | Backend URL (used for OAuth callbacks) |
| `CCHOST_FRONTEND_URL` | auto-detected | Frontend URL (used for OAuth redirects) |
| `NEXT_PUBLIC_CCHOST_API` | auto-detected from `window.location` | Backend API URL (frontend) |

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/sessions` | Create a session |
| `GET` | `/api/sessions` | List sessions (includes dormant) |
| `GET` | `/api/sessions/{id}` | Get session info (triggers lazy resume) |
| `DELETE` | `/api/sessions/{id}` | Destroy session |
| `POST` | `/api/sessions/{id}/runs` | Send a message (async) |
| `GET` | `/api/sessions/{id}/runs/{run_id}` | Poll run status |
| `POST` | `/api/sessions/{id}/send` | Send a message (sync) |
| `POST` | `/api/sessions/{id}/answer` | Answer a question from Claude |
| `POST` | `/api/sessions/{id}/interrupt` | Send Escape to stop Claude |
| `GET` | `/api/sessions/{id}/progress` | Get progress events |
| `GET` | `/api/sessions/{id}/files` | List files in working directory |
| `GET` | `/api/sessions/{id}/files/{path}` | Download file or list directory |
| `GET` | `/api/sessions/{id}/eml/{path}` | Parse EML file (server-side MIME) |
| `POST` | `/api/sessions/{id}/upload` | Upload files |
| `GET` | `/api/sessions/{id}/jsonl` | Raw JSONL conversation |
| `GET` | `/api/sessions/{id}/terminal` | Terminal output |
| `GET` | `/api/auth/google` | Start Gmail OAuth flow |
| `GET` | `/api/auth/google/callback` | OAuth callback |
| `GET` | `/api/auth/google/status` | Check Gmail connection |
| `POST` | `/api/gmail/scan` | Scan Gmail for draw emails |
| `POST` | `/api/gmail/search` | Search Gmail |
| `POST` | `/api/sessions/{id}/gmail/download/{tid}` | Download thread content |
| `POST` | `/api/sessions/{id}/gmail/draft` | Create Gmail draft |
| `POST` | `/api/sessions/{id}/drive/doc` | Create Google Doc |

## How it works

1. `CCHost` manages a pool of tmux sessions, each running `claude --dangerously-skip-permissions`
2. Sessions persist to `~/.cchost/sessions.json` with the Claude session ID for `--resume`
3. Messages are sent by writing to the tmux pane (short messages via send_keys, long via tmux load-buffer)
4. Responses are read from Claude's JSONL conversation log (real-time, not TUI scraping)
5. Completion is detected via shell hooks that write `Stop` events to `.cchost-events.jsonl`
6. Questions (permission prompts, multi-select) are detected from both JSONL and tmux screen state
7. On restart, dormant sessions are loaded from the manifest and lazily resumed on first API access
8. Gmail OAuth uses dynamic redirect URIs from the request Host header, with Tailscale HTTPS detection
