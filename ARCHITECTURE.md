# Architecture

## Overview

cc-web wraps the Claude Code CLI in persistent tmux sessions, reads responses from JSONL conversation logs, and serves a Next.js frontend for browser-based interaction.

```
┌─────────────────────────────────────────────────────────────┐
│                      Browser (Next.js)                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐  │
│  │ Chat UI  │  │ File     │  │ Progress │  │ Session    │  │
│  │          │  │ Viewer   │  │ Panel    │  │ Selector   │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬──────┘  │
│       └──────────────┴──────────────┴──────────────┘         │
│                          REST API calls                      │
└─────────────────────────────┬───────────────────────────────┘
                              │
┌─────────────────────────────┴───────────────────────────────┐
│                  FastAPI Server (:8420)                       │
│  ┌───────────┐  ┌───────────┐  ┌───────────┐               │
│  │ CCHost    │  │ RunManager│  │ Google    │               │
│  │ (sessions)│  │ (async)   │  │ Routes    │               │
│  └─────┬─────┘  └───────────┘  └───────────┘               │
│        │                                                     │
│  ┌─────┴─────────────────────────────────────────────┐      │
│  │              CCSession (per session)                │      │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │      │
│  │  │ tmux     │  │ JSONL    │  │ Progress     │    │      │
│  │  │ pane     │  │ reader   │  │ snapshot     │    │      │
│  │  └──────────┘  └──────────┘  └──────────────┘    │      │
│  └────────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────┴───────────────────────────────┐
│                    Claude Code CLI                            │
│  Running in tmux with --dangerously-skip-permissions         │
│  JSONL logs at ~/.claude/projects/{slug}/{uuid}.jsonl        │
│  Sub-agents at .../subagents/agent-{id}.jsonl                │
└─────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

**JSONL over TUI scraping.** Claude Code writes structured conversation logs. We read those instead of parsing the terminal UI. This gives us reliable message boundaries, tool use details, and sub-agent tracking without fragile screen parsing.

**tmux for persistence.** Each session is a tmux session. Claude Code stays alive between messages (sub-agents, bash processes, tools all persist). On server restart, sessions are lazily resumed from a manifest file.

**Fire-and-forget queuing.** The `/queue` endpoint sends text to tmux without waiting. Claude Code's own readline handles the queuing. The JSONL transcript is the source of truth for what got processed.

**File-list-based @ matching.** Chat message `@file` references are resolved against the session's actual file list, not regex. This prevents false positives (emails, paths outside the session) and handles trailing punctuation naturally.

**Server-side EML parsing.** Email files are parsed by Python's `email` stdlib on the server. The frontend receives structured JSON with pre-resolved `cid:` inline images. No multi-MB text loads in the browser.

## File Structure

```
backend/
  cchost.py          CCHost + CCSession classes, tmux management, manifest persistence
  server.py          FastAPI endpoints, RunManager, progress polling
  progress.py        JSONL normalization and progress snapshot derivation
  google_routes.py   Gmail/Drive OAuth, thread download, draft creation
  google_service.py  Google API token management

frontend/src/
  app/page.tsx             Main page, session state, polling loops
  components/
    chat-input.tsx         Textarea with rich paste, image paste, upload progress, @/slash autocomplete
    jsonl-chat.tsx         Chat message rendering, @ref chips, queued message badges
    file-viewer.tsx        PDF/spreadsheet/zip/markdown/image/directory viewer, staleness detection
    eml-viewer.tsx         Server-fetched EML viewer (HTML/text/parts/headers tabs)
    file-actions.tsx       Shared copy/download/ref buttons
    artifacts-pane.tsx     Right panel file browser with action buttons
    progress-panel.tsx     Progress events, milestones, sub-agent cards
    session-selector.tsx   Session dropdown with dormant/active states
  lib/
    api.ts               REST API client functions
    config.ts            Auto-detected API URL from window.location
    progress.ts          Progress response normalization
    types.ts             Shared TypeScript types
```

## Data Flow

1. User types message in chat input
2. If Claude is idle: `POST /api/sessions/{id}/runs` creates a run, sends via tmux
3. If Claude is busy: `POST /api/sessions/{id}/queue` sends to tmux without waiting
4. Frontend polls `GET /api/sessions/{id}/progress` every 1.2s
5. Backend reads JSONL + hook events, derives progress snapshot
6. `GET /api/sessions/{id}/subagents` returns spawned agent status (parallel fetch)
7. When Claude finishes, run status becomes "completed" with response text
8. Chat re-renders from JSONL transcript (source of truth)

## Session Lifecycle

```
Create → tmux session starts → Claude boots → _wait_for_ready → idle (❯)
  ↓
Send message → tmux pane → Claude processes → JSONL written → poll detects completion
  ↓
Server restart → manifest loaded → session is "dormant" (no tmux)
  ↓
First API access → _resume_session → tmux + claude --resume → idle
  ↓
Destroy → tmux killed → removed from manifest
```
