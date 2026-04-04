"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { fetchJsonl } from "@/lib/api";
import { FileLink } from "@/components/file-link";
import type { ContentBlock, JsonlEntry } from "@/lib/types";

/* ── Tool call (collapsed, click to expand) ── */
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
    const fname = filePath.split("/").pop() || filePath;
    return (
      <div className="text-xs text-gray-400 py-px">
        {block.name} <FileLink filePath={fname} sessionId={sessionId} variant="inline" files={files} onViewFile={onViewFile} />
      </div>
    );
  }

  // Skill — just the skill name
  if (skill) {
    return <div className="text-xs text-gray-400 py-px cursor-pointer hover:text-gray-600" onClick={() => setOpen(!open)}>
      /{skill}{open && <pre className="mt-1 text-[10px] text-gray-400 whitespace-pre-wrap">{JSON.stringify(inp, null, 2)}</pre>}
    </div>;
  }

  // Bash — show command summary, click to expand full
  if (cmd) {
    const firstLine = cmd.split("\n")[0].substring(0, 80);
    const isMultiline = cmd.includes("\n") || cmd.length > 80;
    return (
      <div className="py-px">
        <div className="text-xs text-gray-400 font-mono cursor-pointer hover:text-gray-600 truncate" onClick={() => setOpen(!open)}>
          $ {firstLine}{isMultiline ? "..." : ""}
        </div>
        {open && <pre className="mt-1 text-[11px] font-mono bg-gray-900 text-green-400 rounded px-2.5 py-1.5 whitespace-pre-wrap break-all">{cmd}</pre>}
      </div>
    );
  }

  // WebSearch / WebFetch — show query inline
  const query = inp.query ? String(inp.query) : "";
  const url = inp.url ? String(inp.url) : "";
  if (block.name === "WebSearch" && query) {
    return <div className="text-xs text-gray-400 py-px">WebSearch [{query}]</div>;
  }
  if (block.name === "WebFetch" && url) {
    return <div className="text-xs text-gray-400 py-px">WebFetch [{url.substring(0, 80)}]</div>;
  }

  // Other tools — name + summary
  const summary = desc || pattern || query;
  return (
    <div className="text-xs text-gray-400 py-px cursor-pointer hover:text-gray-600" onClick={() => setOpen(!open)}>
      {block.name}{summary ? ` [${summary}]` : ""}
      {open && <pre className="mt-1 text-[10px] text-gray-400 whitespace-pre-wrap">{JSON.stringify(inp, null, 2)}</pre>}
    </div>
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
            <a href={link.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:text-blue-800 underline underline-offset-2 cursor-pointer">
              {link.title}
            </a>
          </div>
        ))}
        {links.length > 5 && <div className="text-[11px] text-gray-400">+{links.length - 5} more</div>}
      </div>
    );
  } catch { return null; }
}

/* ── Tool result (open by default, click to collapse) ── */
function ToolResult({ block }: { block: ContentBlock }) {
  const [open, setOpen] = useState(true);
  const content = block.content;
  let text = "";
  let isJson = false;

  if (typeof content === "string") {
    text = content;
  } else if (Array.isArray(content)) {
    for (const c of content) {
      if (c && typeof c === "object" && "text" in c) { text = String((c as { text: string }).text); break; }
      if (c && typeof c === "object" && "type" in c) {
        const ct = (c as { type: string }).type;
        if (ct === "tool_reference") return null;
        if (ct === "image") { text = "(image)"; break; }
      }
    }
    // If no text found, show raw JSON
    if (!text && content.length > 0) {
      text = JSON.stringify(content, null, 2);
      isJson = true;
    }
  } else if (content && typeof content === "object") {
    text = JSON.stringify(content, null, 2);
    isJson = true;
  }

  if (!text) return null;

  // Check for web search results with links
  const hasLinks = text.includes("Links: [");
  const headerLine = text.split("\n")[0];

  return (
    <div className="py-px">
      <div
        className={`text-[11px] cursor-pointer hover:text-gray-700 ${open ? "" : "truncate max-h-5 overflow-hidden"}`}
        onClick={() => setOpen(!open)}
      >
        {!open && <span className="font-mono text-gray-500">{headerLine.substring(0, 100)}</span>}
      </div>
      {open && (
        <div>
          {hasLinks ? (
            <div className="text-[11px] text-gray-500">
              <span className="font-mono">{headerLine}</span>
              <WebLinks text={text} />
            </div>
          ) : (
            <pre className={`text-[11px] font-mono text-gray-500 whitespace-pre-wrap break-all max-h-48 overflow-y-auto ${isJson ? "bg-gray-50 rounded p-1.5" : ""}`}>
              {text.substring(0, 500)}{text.length > 500 ? "..." : ""}
            </pre>
          )}
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
          <img key={i} src={src} alt={`Page ${i + 1}`} className="h-32 rounded border border-gray-200 flex-shrink-0 cursor-pointer hover:border-[var(--th-accent)] hover:shadow-md transition-shadow"
            onClick={() => onViewImages?.(images, i)}
          />
        ))}
      </div>
    </div>
  );
}

/* ── AskUserQuestion ── */
function QuestionBlock({ block }: { block: ContentBlock }) {
  const questions = (block.input?.questions as Array<{ question: string; header?: string; options: Array<{ label: string; description?: string }> }>) || [];
  if (questions.length === 0) return null;
  return (
    <div className="text-xs text-gray-400 py-px">
      {questions.map((q, i) => <span key={i}>{i > 0 ? " \u00B7 " : ""}{q.header || q.question.substring(0, 50)}</span>)}
    </div>
  );
}

/* ── Assistant entry ── */
function AssistantEntry({ entry, files, sessionId, onViewFile }: { entry: JsonlEntry; files: string[]; sessionId: string; onViewFile?: (path: string) => void }) {
  const rawContent = entry.message?.content;
  if (typeof rawContent === "string" && rawContent.trim()) {
    const text = rawContent as string;
    return (
      <div className="py-2 text-[15px] prose-chat leading-relaxed" style={{ color: "var(--th-text)" }}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
    );
  }
  const blocks = Array.isArray(rawContent) ? rawContent : [];
  return (
    <div>
      {(blocks as ContentBlock[]).map((block, j) => {
        if (block.type === "text" && block.text?.trim()) {
          return (
            <div key={j} className="py-2 text-[15px] prose-chat leading-relaxed" style={{ color: "var(--th-text)" }}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
                code: (props) => {
                  const t = String(props.children).trim();
                  // File reference — all local files open in artifacts
                  if (/^[\w./-]+\.\w{2,4}$/.test(t) && !props.className)
                    return <FileLink filePath={t} sessionId={sessionId} variant="inline" files={files} onViewFile={onViewFile} />;
                  // Directory reference (ends with /)
                  if (/^[\w./-]+\/$/.test(t) && !props.className && onViewFile) {
                    const dirFiles = files.filter(f => f.startsWith(t) || f.includes(`/${t}`));
                    if (dirFiles.length > 0) {
                      return <button onClick={() => onViewFile(dirFiles[0])} className="text-[var(--th-accent)] hover:text-[var(--th-accent-hover)] text-xs font-mono underline underline-offset-2 cursor-pointer">{t}</button>;
                    }
                  }
                  return <code className={props.className}>{props.children}</code>;
                },
              }}>{block.text}</ReactMarkdown>
            </div>
          );
        }
        if (block.type === "tool_use") {
          if (block.name === "AskUserQuestion") return <QuestionBlock key={j} block={block} />;
          return <ToolCall key={j} block={block} files={files} sessionId={sessionId} onViewFile={onViewFile} />;
        }
        return null;
      })}
    </div>
  );
}

/* ── User entry ── */
function UserEntry({ entry, onViewImages }: { entry: JsonlEntry; onViewImages?: (images: string[], startIndex: number) => void }) {
  const rawContent = entry.message?.content;
  // Handle content as plain string (Claude Code sometimes writes it this way)
  if (typeof rawContent === "string" && rawContent.trim() && rawContent.length <= 2000) {
    return (
      <div className="flex justify-end py-1">
        <div className="max-w-[75%] rounded-2xl px-4 py-2.5 text-sm" style={{ background: "var(--th-user-bubble)", border: "1px solid var(--th-user-bubble-border)" }}>
          {rawContent}
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
      else if (b.type === "image" || b.type === "document") imageBlocks.push(b);
    }
  }

  const text = textParts.join("\n").trim();
  if (!text && resultBlocks.length === 0 && imageBlocks.length === 0) return null;
  if (text.length > 2000 && resultBlocks.length === 0 && imageBlocks.length === 0) return null;

  return (
    <div>
      {text && text.length <= 2000 && (
        <div className="flex justify-end py-1">
          <div className="max-w-[75%] rounded-2xl bg-blue-50 border border-blue-100 px-4 py-2.5 text-sm text-gray-900">{text}</div>
        </div>
      )}
      {resultBlocks.map((b, j) => <ToolResult key={`r-${j}`} block={b} />)}
      {imageBlocks.length > 0 && <ImageStrip blocks={imageBlocks} onViewImages={onViewImages} />}
    </div>
  );
}

/* ── Main ── */
export function JsonlChat({ sessionId, files, onViewFile, onViewImages, pendingMessage, isWorking }: { sessionId: string | null; files: string[]; onViewFile?: (path: string) => void; onViewImages?: (images: string[], startIndex: number) => void; pendingMessage?: string | null; isWorking?: boolean }) {
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
  }, [sessionId]);

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

  const conv = entries.filter(e => e.type === "user" || e.type === "assistant");
  if (conv.length === 0) {
    return <div className="flex h-full items-center justify-center"><p className="text-gray-400">Send a message or drop a file to start</p></div>;
  }

  return (
    <div className="max-w-3xl mx-auto px-8 py-6 space-y-1">
      {conv.map((entry, i) => {
        if (entry.type === "assistant") return <AssistantEntry key={i} entry={entry} files={files} sessionId={sessionId!} onViewFile={onViewFile} />;
        if (entry.type === "user") return <UserEntry key={i} entry={entry} onViewImages={onViewImages} />;
        return null;
      })}

      {/* Optimistic: show user message before JSONL has it */}
      {pendingMessage && (
        <div className="flex justify-end py-1">
          <div className="max-w-[75%] rounded-2xl px-4 py-2.5 text-sm" style={{ background: "var(--th-user-bubble)", borderColor: "var(--th-user-bubble-border)", borderWidth: 1 }}>
            {pendingMessage}
          </div>
        </div>
      )}

      {/* Working indicator */}
      {isWorking && (
        <div className="flex items-center gap-2 py-2 text-sm" style={{ color: "var(--th-text-muted)" }}>
          <span className="inline-block w-2 h-2 rounded-full animate-pulse" style={{ background: "var(--th-accent)" }} />
          Claude is working...
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
