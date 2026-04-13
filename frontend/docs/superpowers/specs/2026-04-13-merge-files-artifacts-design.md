# Merge Files + Artifacts Tabs

## Problem

The Files and Artifacts tabs show the same file list with different presentations. Files has a resizable sidebar with a flat list. Artifacts has a directory-grouped dropdown with action buttons. Neither is complete on its own. Users see two tabs that do the same thing.

## Solution

Merge into a single Files tab. Take the sidebar layout from Files, the directory grouping from Artifacts, add search/filter. Delete the Artifacts tab.

## Design

### Sidebar (left, resizable)

```
+---------------------------+
| [Search files...       ]  |
+---------------------------+
| inbox/19cfd886.../        |
|   Silver Remodel Feb...   |  <- active, accent border
|   REVISED Silver Re...    |
|   gmail-source.json       |
+---------------------------+
| (root)                    |
|   audit_findings.md       |
|   project-context.md      |
+---------------------------+
```

- **Search input** at top. Filters files by filename match (case-insensitive). Clears with X button. When filtering, directory headers that have no matching files are hidden.
- **Directory groups** with a muted header showing the directory path (or "(root)" for files without a directory). Groups sorted alphabetically, root group first.
- **File rows** show just the filename (last path segment). Full path in `title` attribute for hover tooltip. Clicking selects the file for viewing. Clicking the active file deselects it.
- **Active file** highlighted with `bg-th-surface-hover` and left accent border.
- Resizable via the existing drag handle (same as current Files tab, 160-500px range, default 280px).

### Viewer (right)

```
+------------------------------------------------+
| filename.pdf  [sidebar] [<] 3/10 [>] [cp] [dl] |
+------------------------------------------------+
|                                                |
|            FileViewer content                  |
|                                                |
+------------------------------------------------+
```

- **Header bar** with: truncated filename, PDF sidebar toggle + page nav (when PDF), copy content button, copy @ref button, download button.
- Uses `FileActionButtons` from the existing Artifacts code.
- **FileViewer** component below, unchanged.
- When no file is selected: centered muted text "Select a file to preview".
- Directory entries (paths ending with `/`) show the directory listing view via FileViewer's built-in directory support.

### Tab bar

- Remove the "Artifacts" tab entry from TabBar.
- Files tab keeps its file count badge: `Files (60)`.

### Chat sidebar (unchanged)

The right-panel overlay that opens when clicking file links in chat messages continues to work exactly as today. It uses ArtifactsPane as a split-pane overlay within the chat view, not as a tab. This is not affected by the merge.

## Components

### New: `FilesTab` (inline in page.tsx or extracted)

Replaces the current `activeTab === "files"` block and absorbs the Artifacts tab's features. Props:

- `activeSession: string | null`
- `files: string[]`
- `viewingFile: string | null`
- `setViewingFile: (path: string | null) => void`

Internal state:
- `searchQuery: string` for the filter input
- Resizable sidebar width (reuse existing resize pattern)

### Deleted: Artifacts tab branch

Remove `activeTab === "artifacts"` from page.tsx and the "Artifacts" entry from tab-bar.tsx.

### Kept: `artifacts-pane.tsx`

This component is still used as the right-panel file viewer overlay in chat mode (when clicking file links in messages). It stays. Only the Artifacts TAB is removed.

### Kept: `FileActionButtons`, `FileViewer`, `PdfPageNav`, `PdfSidebarToggle`

All unchanged.

## File grouping logic

Reuse the `groupByDirectory` function from artifacts-pane.tsx:

```typescript
function groupByDirectory(files: string[]): Map<string, string[]> {
  const groups = new Map<string, string[]>();
  for (const f of files) {
    const idx = f.lastIndexOf("/");
    const dir = idx >= 0 ? f.substring(0, idx) : "";
    if (!groups.has(dir)) groups.set(dir, []);
    groups.get(dir)!.push(f);
  }
  return groups;
}
```

When search is active, filter the file list before grouping. Only show groups that have matching files.

## Not in scope

- File categorization (input vs output). No API metadata exists for this.
- File upload from the Files tab (upload stays in the chat input bar).
- Drag-and-drop reordering.
- Multi-select or batch operations.
- Collapsible directory tree (flat groups are sufficient).
