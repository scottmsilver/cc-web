# cc-web frontend

Next.js chat UI for cc-web. Connects to the backend API for session management, conversation rendering, file browsing, and progress tracking.

## Setup

```bash
pnpm install
pnpm dev --port 3001
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NEXT_PUBLIC_CCHOST_API` | `http://localhost:8420` | Backend API URL |

## Structure

```
src/
  app/          Page layout and root component
  components/   UI components (chat, files, progress, terminal)
  lib/
    api.ts      Centralized API client (all backend fetch calls)
    config.ts   Shared config (API URL, binary file detection)
    types.ts    Shared TypeScript types
    progress.ts Progress event parsing and normalization
    themes.ts   Color theme definitions
```
