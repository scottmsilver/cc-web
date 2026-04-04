"use client";

import { useEffect, useRef, useState } from "react";

import { fetchJsonl } from "@/lib/api";
import type { ContentBlock, JsonlEntry } from "@/lib/types";

function ContentBlockView({ block }: { block: ContentBlock }) {
  if (block.type === "text" && block.text) {
    return (
      <div className="text-th-text whitespace-pre-wrap">{block.text}</div>
    );
  }
  if (block.type === "thinking") {
    return (
      <div className="text-th-text-faint italic">
        thinking ({(block.thinking || "").length} chars)
      </div>
    );
  }
  if (block.type === "tool_use") {
    const inp = block.input || {};
    const cmd = inp.command ? String(inp.command) : "";
    const filePath = inp.file_path ? String(inp.file_path) : "";
    const pattern = inp.pattern ? String(inp.pattern) : "";
    const skill = inp.skill ? String(inp.skill) : "";
    const hasQuestions = Boolean(inp.questions);
    const hasDetail = cmd || filePath || pattern || skill || hasQuestions;
    return (
      <div>
        <span className="text-purple-700 font-medium">{block.name}</span>
        {cmd && (
          <pre className="mt-0.5 text-th-text bg-th-surface rounded px-2 py-1 whitespace-pre-wrap">{cmd}</pre>
        )}
        {filePath && <span className="text-th-text-muted ml-1">{filePath}</span>}
        {pattern && <span className="text-th-text-muted ml-1">{pattern}</span>}
        {skill && <span className="text-th-text-muted ml-1">{skill}</span>}
        {hasQuestions && <span className="text-amber-700 ml-1">AskUserQuestion</span>}
        {!hasDetail && (
          <pre className="mt-0.5 text-th-text-muted whitespace-pre-wrap">{JSON.stringify(inp, null, 2)}</pre>
        )}
      </div>
    );
  }
  if (block.type === "tool_result") {
    const content = block.content;
    let preview = "";
    if (typeof content === "string") {
      preview = content.substring(0, 200);
    } else if (Array.isArray(content)) {
      for (const c of content) {
        if (c && typeof c === "object" && "text" in c) {
          preview = String((c as { text: string }).text).substring(0, 200);
          break;
        }
        if (c && typeof c === "object" && "tool_name" in c) {
          preview = `(tool_reference: ${(c as { tool_name: string }).tool_name})`;
          break;
        }
      }
    }
    return (
      <div className="text-th-text-muted">
        result{preview ? `: ${preview}` : ""}
        {preview.length >= 200 ? "..." : ""}
      </div>
    );
  }
  return <div className="text-th-text-faint">{block.type || "unknown"}</div>;
}

function EntryView({ entry, index }: { entry: JsonlEntry; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const t = entry.type || "";
  const content = entry.message?.content;

  // Skip non-conversation entries in compact view
  if (!["user", "assistant"].includes(t)) {
    return (
      <div className="flex gap-2 px-3 py-1 text-th-text-faint hover:bg-th-surface cursor-pointer" onClick={() => setExpanded(!expanded)}>
        <span className="w-5 text-right flex-shrink-0">{index}</span>
        <span>{t}</span>
        {expanded && (
          <pre className="text-[10px] text-th-text-muted whitespace-pre-wrap break-all">{JSON.stringify(entry, null, 2)}</pre>
        )}
      </div>
    );
  }

  const typeColor = t === "assistant" ? "text-green-700" : "text-blue-700";
  const blocks = Array.isArray(content) ? content : [];

  return (
    <div className="px-3 py-1.5 hover:bg-th-surface">
      <div className="flex gap-2 items-baseline">
        <span className="w-5 text-right text-th-text-faint flex-shrink-0">{index}</span>
        <span className={`w-12 flex-shrink-0 font-medium ${typeColor}`}>{t}</span>
        <div className="flex-1 min-w-0 space-y-1">
          {blocks.map((block, j) => {
            if (typeof block === "string") {
              return <div key={j} className="text-th-text">{block.substring(0, 200)}</div>;
            }
            return <ContentBlockView key={j} block={block as ContentBlock} />;
          })}
          {blocks.length === 0 && <span className="text-th-text-faint">(empty)</span>}
        </div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-th-text-faint hover:text-th-text flex-shrink-0"
        >
          {expanded ? "\u25BC" : "\u25B6"}
        </button>
      </div>
      {expanded && (
        <pre className="mt-1 ml-20 text-[10px] text-th-text-muted bg-th-surface rounded p-2 whitespace-pre-wrap break-all border border-th-border">
          {JSON.stringify(entry, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function JsonlViewer({ sessionId }: { sessionId: string | null }) {
  const [entries, setEntries] = useState<JsonlEntry[]>([]);
  const [path, setPath] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const data = await fetchJsonl(sessionId);
        if (!cancelled) {
          setEntries(data.entries || []);
          setPath(data.path || "");
        }
      } catch (error) {
        console.warn("Failed to fetch JSONL for viewer:", error);
      }
    };
    void load();
    const id = setInterval(() => void load(), 5000);
    return () => { cancelled = true; clearInterval(id); };
  }, [sessionId]);

  useEffect(() => {
    if (autoScroll) bottomRef.current?.scrollIntoView();
  }, [entries, autoScroll]);

  return (
    <div className="rounded-lg border border-th-border bg-th-bg">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-th-border">
        <span className="text-xs font-medium text-th-text">JSONL Transcript</span>
        <span className="text-[11px] text-th-text-muted truncate flex-1">{path}</span>
        <label className="flex items-center gap-1 text-[11px] text-th-text-muted">
          <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} className="accent-th-accent" />
          Follow
        </label>
        <span className="text-[11px] text-th-text-muted">{entries.length}</span>
      </div>
      <div className="max-h-[600px] overflow-y-auto font-mono text-[11px] leading-relaxed divide-y divide-th-border">
        {entries.map((entry, i) => (
          <EntryView key={i} entry={entry} index={i} />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
