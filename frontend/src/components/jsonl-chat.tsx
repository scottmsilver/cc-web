"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { fetchJsonl } from "@/lib/api";
import { FileLink, makeFileUrl } from "@/components/file-link";
import { CCHOST_API, getFileName } from "@/lib/config";
import { buildTaskList, formatDurationMs } from "@/lib/transcript";
import type { ContentBlock, JsonlEntry, TranscriptTask } from "@/lib/types";

/* ── File reference chip for user messages ── */
const IMAGE_EXT = /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i;
/**
 * Find @ file references by matching against known session files.
 * Handles @./path, @path, and @filename patterns.
 * Only creates chips for paths that resolve to real session files (or their directories).
 */
export function findAtRefs(text: string, files: string[]): { start: number; end: number; path: string }[] {
  const results: { start: number; end: number; path: string }[] = [];
  // Find all @ characters that could start a file reference
  // Must be preceded by start-of-string, whitespace, or quote
  const atPositions: number[] = [];
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== "@") continue;
    if (i === 0 || /[\s"'(]/.test(text[i - 1])) {
      atPositions.push(i);
    }
  }

  for (const idx of atPositions) {
    let afterAt = text.slice(idx + 1);
    let prefixLen = 1; // just "@"

    // Strip optional "./" prefix
    if (afterAt.startsWith("./")) {
      afterAt = afterAt.slice(2);
      prefixLen = 3; // "@./"
    }

    // Try to match a known file — longest match wins
    let bestFile = "";
    let matchedTextLen = 0; // length of what was actually matched in the text
    for (const f of files) {
      if (afterAt.startsWith(f) && f.length > matchedTextLen) {
        bestFile = f;
        matchedTextLen = f.length;
      }
      // Also match by filename only (e.g. @report.pdf matching "inbox/report.pdf")
      const basename = getFileName(f);
      if (basename && afterAt.startsWith(basename) && basename.length > matchedTextLen) {
        // Only match basename if it's unambiguous (one file with that name)
        const dupes = files.filter((ff) => ff.endsWith("/" + basename) || ff === basename);
        if (dupes.length === 1) {
          bestFile = f;             // resolve to full path
          matchedTextLen = basename.length; // but only consumed basename chars from text
        }
      }
    }

    // Also check directory prefixes
    if (!bestFile) {
      const dirs = new Set<string>();
      for (const f of files) {
        const parts = f.split("/");
        for (let d = 1; d < parts.length; d++) {
          dirs.add(parts.slice(0, d).join("/") + "/");
        }
      }
      for (const dir of dirs) {
        if (afterAt.startsWith(dir) && dir.length > matchedTextLen) {
          bestFile = dir;
          matchedTextLen = dir.length;
        }
      }
    }

    if (bestFile && matchedTextLen > 0) {
      results.push({ start: idx, end: idx + prefixLen + matchedTextLen, path: bestFile });
    }
  }
  return results;
}
// Cheap check: does this text contain any @ that could be a file reference?
const AT_REF_RE = /@/;

function FileRefChip({ path, sessionId, files, onViewFile }: {
  path: string;
  sessionId: string;
  files: string[];
  onViewFile?: (path: string) => void;
}) {
  const isDir = path.endsWith("/");
  const resolved = isDir
    ? path
    : (files.find((f) => f === path) || files.find((f) => f.endsWith(`/${path}`)) || path);
  const isImage = !isDir && IMAGE_EXT.test(path);
  const isPdf = !isDir && path.endsWith(".pdf");
  const isInFiles = isDir
    ? files.some((f) => f.startsWith(path) || f.startsWith(path.replace(/\/$/, "")))
    : files.some((f) => f === resolved || f.endsWith(`/${path}`));
  const fileUrl = isDir ? "" : makeFileUrl(sessionId, resolved);

  const handleClick = () => {
    if (onViewFile) onViewFile(resolved);
    else if (!isDir) window.open(fileUrl, "_blank");
  };

  return (
    <button
      onClick={handleClick}
      className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-th-surface border border-th-border hover:border-th-accent/50 hover:bg-th-surface-hover transition-colors text-xs font-mono text-th-text group cursor-pointer align-middle mx-0.5"
      title={`${resolved}${isInFiles ? " — click to view" : ""}`}
    >
      {isImage ? (
        <img
          src={fileUrl}
          alt={path}
          className="w-6 h-6 rounded object-cover flex-shrink-0"
          onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
        />
      ) : (
        <span className="flex-shrink-0 opacity-60">
          {isDir ? "\u{1F4C1}" : isPdf ? "\u{1F4C4}" : "\u{1F4CE}"}
        </span>
      )}
      <span className="max-w-[180px] truncate group-hover:text-th-accent">{getFileName(path)}</span>
    </button>
  );
}

/** Replace @./path references in user message text with rich file chips. */
function renderUserText(text: string, sessionId: string, files: string[], onViewFile?: (path: string) => void): React.ReactNode[] {
  const refs = findAtRefs(text, files);
  if (refs.length === 0) return [text];
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  for (const ref of refs) {
    if (ref.start > lastIndex) {
      parts.push(text.slice(lastIndex, ref.start));
    }
    parts.push(
      <FileRefChip
        key={`ref-${ref.start}`}
        path={ref.path}
        sessionId={sessionId}
        files={files}
        onViewFile={onViewFile}
      />
    );
    lastIndex = ref.end;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

/* ── Thinking ── */
function ThinkingBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const trimmed = text.trim();
  if (!trimmed) return null;
  const firstLine = trimmed.split("\n").find((l) => l.trim()) || "";
  return (
    <div className="py-1">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-left w-full text-xs text-th-text-faint italic hover:text-th-text-muted"
      >
        <span className="mr-1 not-italic font-mono text-[10px] uppercase tracking-wider">thinking</span>
        {open ? "" : firstLine.substring(0, 120) + (firstLine.length > 120 ? "…" : "")}
      </button>
      {open && (
        <pre className="mt-1 text-[11px] text-th-text-muted italic whitespace-pre-wrap border-l-2 border-th-border pl-3 ml-2 font-sans">
          {trimmed}
        </pre>
      )}
    </div>
  );
}

/* ── Plan card (ExitPlanMode) ── */
function PlanCard({ block }: { block: ContentBlock }) {
  const inp = block.input || {};
  const plan = typeof inp.plan === "string" ? inp.plan : "";
  const allowed = Array.isArray(inp.allowedPrompts)
    ? (inp.allowedPrompts as Array<{ tool?: string; prompt?: string }>)
    : [];
  return (
    <div className="my-2 rounded-lg border border-th-accent/40 bg-th-accent/5 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-th-accent/30 bg-th-accent/10">
        <span className="text-xs font-semibold text-th-accent uppercase tracking-wide">Plan</span>
      </div>
      <div className="px-3 py-2 prose-chat text-[13px] text-th-text">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{plan}</ReactMarkdown>
      </div>
      {allowed.length > 0 && (
        <div className="border-t border-th-accent/30 bg-th-accent/5 px-3 py-1.5">
          <div className="text-[10px] uppercase tracking-wide text-th-text-muted mb-1">Pre-approved</div>
          <ul className="text-[11px] text-th-text-muted space-y-0.5">
            {allowed.map((a, i) => (
              <li key={i}>
                <span className="font-mono text-th-accent">{a.tool}</span> — {a.prompt}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/* ── Agent dispatch ── */
function AgentDispatchCard({ block }: { block: ContentBlock }) {
  const [open, setOpen] = useState(false);
  const inp = block.input || {};
  const desc = String(inp.description || "");
  const subtype = String(inp.subagent_type || "general-purpose");
  const prompt = String(inp.prompt || "");
  return (
    <div className="my-1 rounded-md border border-th-border bg-th-surface/50 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 w-full px-2.5 py-1.5 text-left hover:bg-th-surface"
      >
        <span className="text-[10px] uppercase tracking-wider text-th-text-muted font-mono">agent</span>
        <span className="text-xs font-medium text-th-text truncate">{desc || "dispatched subagent"}</span>
        <span className="ml-auto text-[10px] text-th-text-faint font-mono">{subtype}</span>
      </button>
      {open && (
        <pre className="px-3 py-2 text-[11px] text-th-text-muted whitespace-pre-wrap border-t border-th-border bg-th-bg max-h-64 overflow-y-auto">
          {prompt}
        </pre>
      )}
    </div>
  );
}

/* ── AskUserQuestion (historical, non-interactive) ── */
function AskUserHistoricalCard({ block }: { block: ContentBlock }) {
  const qs = (block.input?.questions as Array<{
    question: string;
    header?: string;
    options: Array<{ label: string; description?: string }>;
  }>) || [];
  if (qs.length === 0) return null;
  return (
    <div className="my-2 rounded-lg border border-th-warning-border/50 bg-th-warning-bg/30 p-3">
      {qs.map((q, i) => (
        <div key={i} className={i > 0 ? "mt-3 pt-3 border-t border-th-border/50" : ""}>
          {q.header && (
            <div className="text-[10px] uppercase tracking-wide text-th-warning-text mb-1">{q.header}</div>
          )}
          <p className="text-sm text-th-text mb-2">{q.question}</p>
          <ul className="space-y-1">
            {q.options.map((opt, j) => (
              <li key={j} className="rounded border border-th-border bg-th-bg/60 px-2.5 py-1.5 text-xs">
                <div className="text-th-text">{opt.label}</div>
                {opt.description && (
                  <div className="text-[11px] text-th-text-muted mt-0.5">{opt.description}</div>
                )}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/* ── Shared detail-on-click row ──
 * Shows a compact one-line summary. Hover shows a tooltip with the raw JSON,
 * click/tap reveals the full JSON inline.
 */
function DetailLine({
  children,
  detail,
  className = "text-[11px] text-th-text-faint py-px",
}: {
  children: React.ReactNode;
  detail: unknown;
  className?: string;
}) {
  const raw = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
  const tooltip = raw.length > 400 ? raw.substring(0, 400) + "…" : raw;
  return (
    <details className={className}>
      <summary
        className="cursor-pointer hover:text-th-text-muted list-none marker:hidden"
        title={tooltip}
      >
        {children}
      </summary>
      <pre className="mt-1 text-[10px] text-th-text-muted whitespace-pre-wrap bg-th-surface/60 rounded px-2 py-1 max-h-64 overflow-auto">
        {raw}
      </pre>
    </details>
  );
}

/* ── Task event ── */
function TaskEventLine({ block }: { block: ContentBlock }) {
  const inp = block.input || {};
  const name = block.name || "";
  if (name === "TaskCreate") {
    return (
      <DetailLine detail={inp}>
        + <span className="text-th-text-muted">{String(inp.subject || inp.title || "task")}</span>
      </DetailLine>
    );
  }
  if (name === "TaskUpdate") {
    const status = String(inp.status || "");
    const arrow = status === "completed" ? "✓" : status === "in_progress" ? "→" : "·";
    return (
      <DetailLine detail={inp}>
        {arrow} task {status ? <span className="font-mono">{status}</span> : ""}
        {inp.taskId ? ` #${String(inp.taskId)}` : ""}
      </DetailLine>
    );
  }
  return (
    <DetailLine detail={inp}>
      {name}
    </DetailLine>
  );
}

/* ── MCP tool ── */
function McpToolLine({ block }: { block: ContentBlock }) {
  const [open, setOpen] = useState(false);
  const parts = (block.name || "").split("__"); // mcp__server__tool
  const server = parts[1] || "mcp";
  const tool = parts[2] || (block.name || "");
  const inp = block.input || {};
  return (
    <div className="py-px">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="text-xs text-th-text-faint hover:text-th-text-muted"
      >
        <span className="font-mono text-th-accent/70">mcp</span>
        <span className="mx-1 text-th-text-faint">·</span>
        <span className="font-mono">{server}</span>
        <span className="mx-1 text-th-text-faint">·</span>
        <span>{tool}</span>
      </button>
      {open && (
        <pre className="mt-1 text-[10px] text-th-text-faint whitespace-pre-wrap bg-th-surface/60 rounded px-2 py-1">
          {JSON.stringify(inp, null, 2)}
        </pre>
      )}
    </div>
  );
}

/* ── Generic tool (known tools fall through here) ── */
function ToolCall({ block, files, sessionId, onViewFile }: { block: ContentBlock; files: string[]; sessionId: string; onViewFile?: (path: string) => void }) {
  const [open, setOpen] = useState(false);
  const inp = block.input || {};
  const cmd = inp.command ? String(inp.command) : "";
  const filePath = inp.file_path ? String(inp.file_path) : "";
  const skill = inp.skill ? String(inp.skill) : "";
  const pattern = inp.pattern ? String(inp.pattern) : "";
  const desc = inp.description ? String(inp.description) : "";

  // Read/Write with file — just show as file link
  if (filePath && !cmd) {
    const fname = getFileName(filePath);
    return (
      <div className="text-xs text-th-text-faint py-px">
        {block.name} <FileLink filePath={fname} sessionId={sessionId} variant="inline" files={files} onViewFile={onViewFile} />
      </div>
    );
  }

  // Skill — just the skill name
  if (skill) {
    return <div className="text-xs text-th-text-faint py-px cursor-pointer hover:text-th-text-muted" onClick={() => setOpen(!open)}>
      /{skill}{open && <pre className="mt-1 text-[10px] text-th-text-faint whitespace-pre-wrap">{JSON.stringify(inp, null, 2)}</pre>}
    </div>;
  }

  // Bash — show command summary, click to expand full
  if (cmd) {
    const firstLine = cmd.split("\n")[0].substring(0, 80);
    const isMultiline = cmd.includes("\n") || cmd.length > 80;
    return (
      <div className="py-px">
        <div className="text-xs text-th-text-faint font-mono cursor-pointer hover:text-th-text-muted truncate" onClick={() => setOpen(!open)}>
          $ {firstLine}{isMultiline ? "..." : ""}
        </div>
        {open && <pre className="mt-1 text-[11px] font-mono bg-th-term-bg text-th-term-text rounded px-2.5 py-1.5 whitespace-pre-wrap break-all">{cmd}</pre>}
      </div>
    );
  }

  // WebSearch / WebFetch — show query inline
  const query = inp.query ? String(inp.query) : "";
  const url = inp.url ? String(inp.url) : "";
  if (block.name === "WebSearch" && query) {
    return <div className="text-xs text-th-text-faint py-px">WebSearch [{query}]</div>;
  }
  if (block.name === "WebFetch" && url) {
    return <div className="text-xs text-th-text-faint py-px">WebFetch [{url.substring(0, 80)}]</div>;
  }

  // Known short summaries
  const summary = desc || pattern || query;
  if (summary) {
    return (
      <div className="text-xs text-th-text-faint py-px cursor-pointer hover:text-th-text-muted" onClick={() => setOpen(!open)}>
        {block.name} [{summary}]
        {open && <pre className="mt-1 text-[10px] text-th-text-faint whitespace-pre-wrap">{JSON.stringify(inp, null, 2)}</pre>}
      </div>
    );
  }

  // Fallback: honest "gross but acceptable" raw JSON
  return (
    <details className="py-px text-xs">
      <summary className="cursor-pointer text-th-text-faint hover:text-th-text-muted">
        {block.name || "(unnamed tool)"} <span className="text-[10px] text-th-text-faint opacity-70">(raw)</span>
      </summary>
      <pre className="mt-1 text-[10px] text-th-text-muted whitespace-pre-wrap bg-th-surface/60 rounded px-2 py-1 max-h-48 overflow-auto">
        {JSON.stringify(inp, null, 2)}
      </pre>
    </details>
  );
}

/* ── Parse web search links from result text ── */
function WebLinks({ text }: { text: string }) {
  const match = text.match(/Links:\s*(\[[\s\S]*?\])/);
  if (!match) return null;
  try {
    const links = JSON.parse(match[1]) as { title: string; url: string }[];
    return (
      <div className="mt-0.5 space-y-0.5">
        {links.slice(0, 5).map((link, i) => (
          <div key={i} className="text-[11px]">
            <a href={link.url} target="_blank" rel="noopener noreferrer" className="text-th-accent hover:text-th-accent-hover underline underline-offset-2 cursor-pointer inline-flex items-center gap-1 no-external-icon">
              {link.title}
              <svg className="inline-block w-3 h-3 flex-shrink-0 opacity-50" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M3.5 3h5.5v5.5M8.5 3.5L3 9" /></svg>
            </a>
          </div>
        ))}
        {links.length > 5 && <div className="text-[11px] text-th-text-faint">+{links.length - 5} more</div>}
      </div>
    );
  } catch { return null; }
}

/* ── Tool result (open by default, click to collapse) ── */
function ToolResult({ block, onViewImages }: { block: ContentBlock; onViewImages?: (images: string[], startIndex: number) => void }) {
  const [open, setOpen] = useState(true);
  const content = block.content;
  let text = "";
  let isJson = false;
  const inlineImages: string[] = [];

  if (typeof content === "string") {
    text = content;
  } else if (Array.isArray(content)) {
    for (const c of content) {
      if (c && typeof c === "object" && "text" in c) { text = String((c as { text: string }).text); break; }
      if (c && typeof c === "object" && "type" in c) {
        const ct = (c as { type: string }).type;
        if (ct === "tool_reference") return null;
        if (ct === "image") {
          const src = (c as { source?: { media_type?: string; data?: string } }).source;
          if (src?.data && src?.media_type) {
            inlineImages.push(`data:${src.media_type};base64,${src.data}`);
          }
        }
      }
    }
    // If we found images but no text, show images only
    if (!text && inlineImages.length > 0) {
      text = "";
    }
    // If no text and no images, show raw JSON
    if (!text && inlineImages.length === 0 && content.length > 0) {
      text = JSON.stringify(content, null, 2);
      isJson = true;
    }
  } else if (content && typeof content === "object") {
    text = JSON.stringify(content, null, 2);
    isJson = true;
  }

  if (!text && inlineImages.length === 0) return null;

  // Check for web search results with links
  const hasLinks = text.includes("Links: [");
  const headerLine = text.split("\n")[0];

  return (
    <div className="py-px">
      <div
        className={`text-[11px] cursor-pointer hover:text-th-text ${open ? "" : "truncate max-h-5 overflow-hidden"}`}
        onClick={() => setOpen(!open)}
      >
        {!open && <span className="font-mono text-th-text-muted">{inlineImages.length > 0 ? `(${inlineImages.length} page${inlineImages.length !== 1 ? "s" : ""})` : headerLine.substring(0, 100)}</span>}
      </div>
      {open && (
        <div>
          {inlineImages.length > 0 && (
            <div className="py-1 overflow-x-auto">
              <div className="flex gap-2">
                {inlineImages.map((src, i) => (
                  <img key={i} src={src} alt={`Page ${i + 1}`} className="h-32 rounded border border-th-border flex-shrink-0 cursor-pointer hover:border-th-accent hover:shadow-md transition-shadow"
                    onClick={() => onViewImages?.(inlineImages, i)}
                  />
                ))}
              </div>
            </div>
          )}
          {text && (hasLinks ? (
            <div className="text-[11px] text-th-text-muted">
              <span className="font-mono">{headerLine}</span>
              <WebLinks text={text} />
            </div>
          ) : (
            <pre className={`text-[11px] font-mono text-th-text-muted whitespace-pre-wrap break-all max-h-48 overflow-y-auto ${isJson ? "bg-th-surface rounded p-1.5" : ""}`}>
              {text.substring(0, 500)}{text.length > 500 ? "..." : ""}
            </pre>
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Image strip (horizontal scroll) ── */
function ImageStrip({ blocks, onViewImages }: { blocks: ContentBlock[]; onViewImages?: (images: string[], startIndex: number) => void }) {
  const images = blocks.map((b) => {
    const src = (b as unknown as { source?: { media_type?: string; data?: string } }).source;
    return src?.data && src?.media_type ? `data:${src.media_type};base64,${src.data}` : null;
  }).filter(Boolean) as string[];
  if (images.length === 0) return null;

  return (
    <div className="py-1 overflow-x-auto">
      <div className="flex gap-2">
        {images.map((src, i) => (
          <img key={i} src={src} alt={`Page ${i + 1}`} className="h-32 rounded border border-th-border flex-shrink-0 cursor-pointer hover:border-th-accent hover:shadow-md transition-shadow"
            onClick={() => onViewImages?.(images, i)}
          />
        ))}
      </div>
    </div>
  );
}

/* ── System event rows ── */
function SystemEventRow({ entry }: { entry: JsonlEntry }) {
  const sub = entry.subtype;
  const r = entry as Record<string, unknown>;

  if (sub === "turn_duration") {
    const ms = Number(r.durationMs || 0);
    const count = Number(r.messageCount || 0);
    return (
      <DetailLine detail={r} className="text-[11px] text-th-text-faint py-1">
        {formatDurationMs(ms)}
        {count > 0 ? ` · ${count} step${count === 1 ? "" : "s"}` : ""}
      </DetailLine>
    );
  }
  if (sub === "compact_boundary") {
    const meta = r.compactMetadata as { trigger?: string; preTokens?: number } | undefined;
    return (
      <div className="my-3 flex items-center gap-3 text-[11px] text-th-text-muted">
        <div className="flex-1 h-px bg-th-border" />
        <span className="uppercase tracking-wider">
          conversation compacted
          {meta?.preTokens ? ` · ${Math.round(meta.preTokens / 1000)}K tokens` : ""}
          {meta?.trigger ? ` · ${meta.trigger}` : ""}
        </span>
        <div className="flex-1 h-px bg-th-border" />
      </div>
    );
  }
  if (sub === "api_error") {
    const content = String(r.content || r.message || "API error");
    return (
      <div className="my-1 rounded-md border border-th-error-text/40 bg-th-error-bg px-3 py-2 text-xs text-th-error-text">
        <span className="uppercase tracking-wider mr-2 font-semibold">api error</span>
        {content}
      </div>
    );
  }
  if (sub === "away_summary" || sub === "informational") {
    const content = String(r.content || "");
    if (!content) return null;
    return (
      <div className="my-1 rounded border border-th-border bg-th-surface/60 px-3 py-2 text-xs text-th-text-muted">
        <span className="text-[10px] uppercase tracking-wide text-th-text-faint mr-2">{sub === "away_summary" ? "Away" : "Info"}</span>
        {content}
      </div>
    );
  }
  if (sub === "stop_hook_summary") {
    const hooks = (r.hookInfos as Array<{ command?: string; durationMs?: number }>) || [];
    const errors = (r.hookErrors as unknown[]) || [];
    if (hooks.length === 0 && errors.length === 0) return null;
    return (
      <DetailLine detail={r}>
        <span className="uppercase tracking-wider mr-1">hooks</span>
        {hooks.length}
        {hooks.length > 0 && ` · ${hooks.map((h) => `${(h.command || "").split("/").pop()} ${h.durationMs}ms`).join(", ")}`}
        {errors.length > 0 && <span className="text-th-error-text"> · {errors.length} error{errors.length === 1 ? "" : "s"}</span>}
      </DetailLine>
    );
  }
  if (sub === "local_command") {
    return null; // rendered via "command" type elsewhere if present
  }
  if (sub === "scheduled_task_fire") {
    return (
      <DetailLine detail={r} className="text-[11px] text-th-text-faint py-px uppercase tracking-wider">
        scheduled task fired
      </DetailLine>
    );
  }
  // Gross but acceptable fallback for unknown system subtypes
  return (
    <details className="text-[11px] text-th-text-faint py-px">
      <summary className="cursor-pointer hover:text-th-text-muted">system · {sub || "unknown"}</summary>
      <pre className="mt-1 text-[10px] text-th-text-muted whitespace-pre-wrap bg-th-surface/60 rounded px-2 py-1 max-h-48 overflow-auto">
        {JSON.stringify(r, null, 2)}
      </pre>
    </details>
  );
}

/* ── Top-level event row (non-message entries) ── */
function EventRow({ entry }: { entry: JsonlEntry }) {
  const t = entry.type;
  const r = entry as Record<string, unknown>;

  if (t === "system") return <SystemEventRow entry={entry} />;

  if (t === "pr-link") {
    const url = String(r.prUrl || "");
    const num = r.prNumber ? `#${r.prNumber}` : "PR";
    const repo = r.prRepository ? ` (${r.prRepository})` : "";
    return (
      <div className="my-1 rounded-md border border-th-accent/40 bg-th-accent/5 px-3 py-2 text-xs">
        <span className="uppercase tracking-wider text-th-text-muted mr-2">pr</span>
        <a href={url} target="_blank" rel="noopener noreferrer" className="text-th-accent hover:underline">{num}{repo}</a>
      </div>
    );
  }

  if (t === "custom-title" || t === "agent-name") {
    const title = String(r.customTitle || r.agentName || "");
    if (!title) return null;
    return (
      <DetailLine detail={r}>
        <span className="uppercase tracking-wider mr-1">{t === "custom-title" ? "title" : "agent"}</span>
        <span className="text-th-text-muted">{title}</span>
      </DetailLine>
    );
  }

  if (t === "worktree-state") {
    const w = r.worktreeSession as { worktreeName?: string; worktreeBranch?: string } | undefined;
    if (!w) return null;
    return (
      <DetailLine detail={r}>
        <span className="uppercase tracking-wider mr-1">worktree</span>
        <span className="font-mono text-th-text-muted">{w.worktreeName}</span>
        {w.worktreeBranch && <span className="text-th-text-faint"> · {w.worktreeBranch}</span>}
      </DetailLine>
    );
  }

  if (t === "permission-mode") {
    const mode = String(r.permissionMode || "");
    return (
      <DetailLine detail={r}>
        <span className="uppercase tracking-wider mr-1">permission</span>
        <span className="font-mono text-th-text-muted">{mode}</span>
      </DetailLine>
    );
  }

  if (t === "queue-operation") {
    const op = String(r.operation || "");
    const content = String(r.content || "");
    return (
      <DetailLine detail={r}>
        <span className="uppercase tracking-wider mr-1">queue {op}</span>
        <span className="text-th-text-muted">{content.substring(0, 100)}</span>
      </DetailLine>
    );
  }

  if (t === "file-history-snapshot") {
    const snap = r.snapshot as { trackedFileBackups?: unknown[] } | undefined;
    const n = Array.isArray(snap?.trackedFileBackups) ? snap.trackedFileBackups.length : 0;
    if (n === 0) return null;
    return (
      <DetailLine detail={r}>
        <span className="uppercase tracking-wider mr-1">snapshot</span>
        {n} file{n === 1 ? "" : "s"}
      </DetailLine>
    );
  }

  if (t === "progress") {
    const d = r.data as { hookEvent?: string; hookName?: string; command?: string; type?: string } | undefined;
    if (!d) return null;
    const label = d.hookName || d.hookEvent || d.type || "progress";
    return (
      <DetailLine detail={r} className="text-[10px] text-th-text-faint py-px opacity-70">
        <span className="uppercase tracking-wider mr-1">hook</span>
        {label}{d.command ? ` · ${d.command}` : ""}
      </DetailLine>
    );
  }

  if (t === "last-prompt" || t === "attachment") {
    return null; // intentionally skipped (redundant with message content)
  }

  // Gross-but-acceptable fallback
  return (
    <details className="py-px text-[11px] text-th-text-faint">
      <summary className="cursor-pointer hover:text-th-text-muted">event · {t || "unknown"}</summary>
      <pre className="mt-1 text-[10px] text-th-text-muted whitespace-pre-wrap bg-th-surface/60 rounded px-2 py-1 max-h-48 overflow-auto">
        {JSON.stringify(r, null, 2)}
      </pre>
    </details>
  );
}

/* ── Assistant entry ── */
function AssistantEntry({ entry, files, sessionId, onViewFile }: { entry: JsonlEntry; files: string[]; sessionId: string; onViewFile?: (path: string) => void }) {
  const rawContent = entry.message?.content;
  if (typeof rawContent === "string" && rawContent.trim()) {
    const text = rawContent as string;
    return (
      <div className="py-2 text-[15px] prose-chat leading-relaxed text-th-text">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
          a: (props) => {
            const href = props.href || "";
            const isExternal = href.startsWith("http");
            return <a href={href} {...(isExternal ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{props.children}</a>;
          },
        }}>{text}</ReactMarkdown>
      </div>
    );
  }
  const blocks = Array.isArray(rawContent) ? rawContent : [];
  return (
    <div>
      {(blocks as ContentBlock[]).map((block, j) => {
        if (block.type === "text" && block.text?.trim()) {
          return (
            <div key={j} className="py-2 text-[15px] prose-chat leading-relaxed text-th-text">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                a: (props) => {
                  const href = props.href || "";
                  const isExternal = href.startsWith("http");
                  return <a href={href} {...(isExternal ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{props.children}</a>;
                },
                code: (props) => {
                  const t = String(props.children).trim();
                  // File reference — all local files open in artifacts
                  if (/^[\w./-]+\.\w{2,4}$/.test(t) && !props.className)
                    return <FileLink filePath={t} sessionId={sessionId} variant="inline" files={files} onViewFile={onViewFile} />;
                  // Directory reference (ends with /)
                  if (/^[\w./-]+\/$/.test(t) && !props.className && onViewFile) {
                    const dirFiles = files.filter(f => f.startsWith(t) || f.includes(`/${t}`));
                    if (dirFiles.length > 0) {
                      return <button onClick={() => onViewFile(dirFiles[0])} className="text-th-accent hover:text-th-accent-hover text-xs font-mono underline underline-offset-2 cursor-pointer">{t}</button>;
                    }
                  }
                  return <code className={props.className}>{props.children}</code>;
                },
              }}>{block.text}</ReactMarkdown>
            </div>
          );
        }
        if (block.type === "thinking") {
          return <ThinkingBlock key={j} text={block.thinking || ""} />;
        }
        if (block.type === "tool_use") {
          const name = block.name || "";
          if (name === "AskUserQuestion") return <AskUserHistoricalCard key={j} block={block} />;
          if (name === "ExitPlanMode") return <PlanCard key={j} block={block} />;
          if (name === "EnterPlanMode") {
            return (
              <div key={j} className="my-2 flex items-center gap-2 text-[11px] text-th-accent uppercase tracking-wider">
                <div className="flex-1 h-px bg-th-accent/30" />
                <span>entering plan mode</span>
                <div className="flex-1 h-px bg-th-accent/30" />
              </div>
            );
          }
          if (name === "Agent") return <AgentDispatchCard key={j} block={block} />;
          if (name === "TaskCreate" || name === "TaskUpdate" || name === "TaskList" || name === "TaskOutput") {
            return <TaskEventLine key={j} block={block} />;
          }
          if (name.startsWith("mcp__")) return <McpToolLine key={j} block={block} />;
          return <ToolCall key={j} block={block} files={files} sessionId={sessionId} onViewFile={onViewFile} />;
        }
        // Unknown block type — gross but acceptable
        return (
          <details key={j} className="py-px text-[11px] text-th-text-faint">
            <summary className="cursor-pointer hover:text-th-text-muted">block · {block.type || "unknown"}</summary>
            <pre className="mt-1 text-[10px] text-th-text-muted whitespace-pre-wrap bg-th-surface/60 rounded px-2 py-1 max-h-48 overflow-auto">
              {JSON.stringify(block, null, 2)}
            </pre>
          </details>
        );
      })}
    </div>
  );
}

/* ── User entry ── */
/* ── Command result (from slash commands) ── */
function CommandEntry({ entry }: { entry: JsonlEntry }) {
  const command = (entry as Record<string, unknown>).command as string || "";
  const content = (entry as Record<string, unknown>).content as string || "";
  if (!content) return null;
  return (
    <div className="py-2">
      <div className="rounded-lg border border-th-border bg-th-surface overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-th-border bg-th-surface-hover">
          <span className="text-xs font-mono font-semibold text-th-accent">{command}</span>
          <span className="text-xs text-th-text-faint">command output</span>
        </div>
        <pre className="px-3 py-2 text-xs font-mono text-th-text whitespace-pre-wrap overflow-x-auto leading-relaxed">{content}</pre>
      </div>
    </div>
  );
}

function UserEntry({ entry, sessionId, files, onViewFile, onViewImages }: { entry: JsonlEntry; sessionId: string; files: string[]; onViewFile?: (path: string) => void; onViewImages?: (images: string[], startIndex: number) => void }) {
  const rawContent = entry.message?.content;
  // Handle content as plain string (Claude Code sometimes writes it this way)
  if (typeof rawContent === "string" && rawContent.trim() && rawContent.length <= 2000) {
    const hasRefs = AT_REF_RE.test(rawContent);
    return (
      <div className="flex justify-end py-1">
        <div className="max-w-[75%] rounded-2xl px-4 py-2.5 text-sm bg-th-user-bubble border border-th-user-bubble-border">
          {hasRefs && sessionId
            ? renderUserText(rawContent, sessionId, files, onViewFile)
            : rawContent}
        </div>
      </div>
    );
  }
  const blocks = Array.isArray(rawContent) ? rawContent : [];
  const textParts: string[] = [];
  const resultBlocks: ContentBlock[] = [];
  const imageBlocks: ContentBlock[] = [];

  for (const block of blocks) {
    if (typeof block === "string" && block.trim()) textParts.push(block);
    else if (typeof block === "object" && block !== null) {
      const b = block as ContentBlock;
      if (b.type === "text" && b.text?.trim()) textParts.push(b.text);
      else if (b.type === "tool_result") resultBlocks.push(b);
      else if (b.type === "image") imageBlocks.push(b);
      else if (b.type === "document") {
        const src = (b as unknown as { source?: { media_type?: string } }).source;
        if (src?.media_type && !src.media_type.startsWith("image/")) {
          // Non-image document (PDF, etc.) — skip, already visible via tool_result
        } else {
          imageBlocks.push(b);
        }
      }
    }
  }

  const text = textParts.join("\n").trim();
  if (!text && resultBlocks.length === 0 && imageBlocks.length === 0) return null;
  if (text.length > 2000 && resultBlocks.length === 0 && imageBlocks.length === 0) return null;

  const hasRefs = AT_REF_RE.test(text);

  return (
    <div>
      {text && text.length <= 2000 && (
        <div className="flex justify-end py-1">
          <div className="max-w-[75%] rounded-2xl bg-th-user-bubble border border-th-user-bubble-border px-4 py-2.5 text-sm text-th-text">
            {hasRefs && sessionId
              ? renderUserText(text, sessionId, files, onViewFile)
              : text}
          </div>
        </div>
      )}
      {resultBlocks.map((b, j) => <ToolResult key={`r-${j}`} block={b} onViewImages={onViewImages} />)}
      {imageBlocks.length > 0 && <ImageStrip blocks={imageBlocks} onViewImages={onViewImages} />}
    </div>
  );
}

/* ── Main ── */
export function JsonlChat({ sessionId, files, onViewFile, onViewImages, pendingMessage, queuedMessages, isWorking, refreshKey = 0, onTasksChange }: { sessionId: string | null; files: string[]; onViewFile?: (path: string) => void; onViewImages?: (images: string[], startIndex: number) => void; pendingMessage?: string | null; queuedMessages?: string[]; isWorking?: boolean; refreshKey?: number; onTasksChange?: (tasks: TranscriptTask[]) => void }) {
  const [entries, setEntries] = useState<JsonlEntry[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLElement | null>(null);
  const prevCountRef = useRef(0);
  const userScrolledRef = useRef(false);
  const hasEntries = entries.length > 0;

  useEffect(() => {
    if (!sessionId) { setEntries([]); return; }
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchJsonl(sessionId);
        if (!cancelled) setEntries(data.entries || []);
      } catch (error) {
        console.warn("Failed to fetch JSONL:", error);
      }
    };
    void load();
    const id = setInterval(() => void load(), 3000);
    return () => { cancelled = true; clearInterval(id); };
  }, [sessionId, refreshKey]);

  useEffect(() => {
    if (onTasksChange) onTasksChange(buildTaskList(entries));
  }, [entries, onTasksChange]);

  // Find the scroll container (closest ancestor with overflow scroll/auto)
  useEffect(() => {
    let el = bottomRef.current?.parentElement;
    while (el) {
      const style = getComputedStyle(el);
      if (style.overflowY === "auto" || style.overflowY === "scroll") {
        scrollContainerRef.current = el;
        break;
      }
      el = el.parentElement;
    }
    if (!scrollContainerRef.current) return;

    const container = scrollContainerRef.current;
    const onScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      const atBottom = scrollHeight - scrollTop - clientHeight < 80;
      userScrolledRef.current = !atBottom;
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => container.removeEventListener("scroll", onScroll);
  }, [hasEntries]);

  // Auto-scroll only if user is at the bottom
  useEffect(() => {
    if (entries.length > prevCountRef.current && !userScrolledRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
    prevCountRef.current = entries.length;
  }, [entries]);

  // Keep everything except purely redundant types; render unknowns honestly below.
  const conv = entries.filter((e) => e.type !== "last-prompt" && e.type !== "attachment");
  if (conv.filter((e) => e.type === "user" || e.type === "assistant").length === 0) {
    return <div className="flex h-full items-center justify-center"><p className="text-th-text-faint">Send a message or drop a file to start</p></div>;
  }

  return (
    <div className="max-w-3xl mx-auto px-8 py-6 space-y-1">
      {conv.map((entry, i) => {
        if (entry.type === "assistant") return <AssistantEntry key={i} entry={entry} files={files} sessionId={sessionId!} onViewFile={onViewFile} />;
        if (entry.type === "user") return <UserEntry key={i} entry={entry} sessionId={sessionId!} files={files} onViewFile={onViewFile} onViewImages={onViewImages} />;
        if (entry.type === "command") return <CommandEntry key={i} entry={entry} />;
        return <EventRow key={i} entry={entry} />;
      })}

      {/* Optimistic: show user message before JSONL has it */}
      {pendingMessage && (
        <div className="flex justify-end py-1">
          <div className="max-w-[75%] rounded-2xl px-4 py-2.5 text-sm bg-th-user-bubble border border-th-user-bubble-border">
            {AT_REF_RE.test(pendingMessage) && sessionId
              ? renderUserText(pendingMessage, sessionId, files, onViewFile)
              : pendingMessage}
          </div>
        </div>
      )}

      {/* Queued messages sent while Claude was busy */}
      {queuedMessages && queuedMessages.length > 0 && queuedMessages.map((msg, i) => (
        <div key={`queued-${i}`} className="flex justify-end py-1">
          <div className="max-w-[75%] rounded-2xl px-4 py-2.5 text-sm bg-th-user-bubble border border-th-user-bubble-border relative">
            <span className="absolute -top-2 -right-1 text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-th-accent text-white leading-none">queued</span>
            {AT_REF_RE.test(msg) && sessionId
              ? renderUserText(msg, sessionId, files, onViewFile)
              : msg}
          </div>
        </div>
      ))}

      {/* Working indicator */}
      {isWorking && (
        <div className="flex items-center gap-2 py-2 text-sm text-th-text-muted">
          <span className="inline-block w-2 h-2 rounded-full animate-pulse bg-th-accent" />
          Claude is working...
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
