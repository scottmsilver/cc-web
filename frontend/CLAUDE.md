@AGENTS.md

## Project

This is the frontend for cc-web, a web interface for Claude Code.
The backend API runs on a configurable URL (default `http://localhost:8420`).

## Key patterns

- All API calls go through `src/lib/api.ts` — never use raw `fetch()` in components
- Shared types live in `src/lib/types.ts` — never define `ContentBlock`, `JsonlEntry`, etc. locally
- The API base URL comes from `src/lib/config.ts` — never hardcode `localhost:8420`
- CSS colors use theme variables (`var(--th-accent)`, etc.) — never hardcode hex values
- Error handling: always `console.warn` in catch blocks, never empty catch
