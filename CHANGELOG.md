# Changelog

All notable changes to cc-web are documented here.

## [0.2.0.0] - 2026-04-13

### Added
- Message queuing: type while Claude is working, messages queue and process in order
- Sub-agent visibility: see spawned agents in the progress panel with live status
- File action buttons: copy content as image/text, copy @ref to clipboard, download
- File staleness detection: toast notification when a viewed file changes on disk
- URL routing: session, tab, and file state in query params for shareable links
- `@file` references matched against real session files (not regex), handles `@path` and `@./path`
- Server-side EML parsing with proper charset/encoding, inline image resolution
- Gmail thread conversation view for thread.json files
- Folder navigation with breadcrumbs and file type icons
- Rich paste: HTML from docs/web converts to markdown on paste
- Image paste: screenshots from clipboard upload as file chips
- Upload progress: Apple-style pie chart indicator, non-blocking input
- Session persistence: sessions survive server restarts via manifest + `claude --resume`
- Gmail integration: OAuth, inbox scan, thread download (full MIME + attachments), draft creation
- Tailscale HTTPS support with dynamic host detection

### Changed
- CORS defaults to `*` for multi-host dev access
- OAuth redirect URIs auto-detect from request Host header
- EML viewer uses server-side parsing (no more 50MB client-side loads)
- Artifacts pane header uses compact SVG icon buttons

### Fixed
- Path traversal boundary check uses `os.sep` (prevents sibling-directory access)
- Unicode arrows render correctly in JSX (wrapped in string expressions)
- EML charset decoding handles UTF-8, ISO-8859-1, quoted-printable correctly
- Typing performance: autocomplete only triggers when `@` or `/` present

## [0.1.0.0] - 2026-04-07

### Added
- Initial release: Claude Code wrapped in tmux with REST API + Next.js frontend
- Session management (create, list, destroy, send messages, answer questions)
- Progress tracking via JSONL conversation log parsing
- File upload/download with working directory isolation
- PDF viewer (pdfjs), spreadsheet viewer (xlsx), ZIP contents listing
- Slash command autocomplete
- Session selector dropdown
- Terminal view, JSONL viewer, progress timeline
