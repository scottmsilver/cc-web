"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { fetchJsonl } from "@/lib/api";
import { FileLink, makeFileUrl } from "@/components/file-link";
import { CCHOST_API } from "@/lib/config";
import type { ContentBlock, JsonlEntry } from "@/lib/types";

/* ── File reference chip for user messages ── */
const IMAGE_EXT = /\.(png|jpg|jpeg|gif|webp|svg|bmp)$/i;
const AT_REF_RE = /@\.\/([^\s]+)/g;

function FileRefChip({ path, sessionId, files, onViewFile }: {
  path: string;
  sessionId: string;
  files: string[];
  onViewFile?: (path: string) => void;
}) {
  const resolved = files.find((f) => f === path) || files.find((f) => f.endsWith(`/${path}`)) || path;
  const isImage = IMAGE_EXT.test(path);
  const isPdf = path.endsWith(".pdf");
  const isInFiles = files.some((f) => f === resolved || f.endsWith(`/${path}`));
  const fileUrl = makeFileUrl(sessionId, resolved);

  const handleClick = () => {
    if (isInFiles && onViewFile) onViewFile(resolved);
    else window.open(fileUrl, "_blank");
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
          {isPdf ? "\u{1F4C4}" : "\u{1F4CE}"}
        </span>
      )}
      <span className="max-w-[180px] truncate group-hover:text-th-accent">{path.split("/").pop()}</span>
    </button>
  );
}

/** Replace @./path references in user message text with rich file chips. */
function renderUserText(text: string, sessionId: string, files: string[], onViewFile?: (path: string) => void): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  AT_REF_RE.lastIndex = 0;
  let match;
  while ((match = AT_REF_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <FileRefChip
        key={`ref-${match.index}`}
        path={match[1]}
        sessionId={sessionId}
        files={files}
        onViewFile={onViewFile}
      />
    );
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

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

  // Other tools — name + summary
  const summary = desc || pattern || query;
  return (
    <div className="text-xs text-th-text-faint py-px cursor-pointer hover:text-th-text-muted" onClick={() => setOpen(!open)}>
      {block.name}{summary ? ` [${summary}]` : ""}
      {open && <pre className="mt-1 text-[10px] text-th-text-faint whitespace-pre-wrap">{JSON.stringify(inp, null, 2)}</pre>}
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

/* ── AskUserQuestion ── */
function QuestionBlock({ block }: { block: ContentBlock }) {
  const questions = (block.input?.questions as Array<{ question: string; header?: string; options: Array<{ label: string; description?: string }> }>) || [];
  if (questions.length === 0) return null;
  return (
    <div className="text-xs text-th-text-faint py-px">
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
export function JsonlChat({ sessionId, files, onViewFile, onViewImages, pendingMessage, isWorking, refreshKey = 0 }: { sessionId: string | null; files: string[]; onViewFile?: (path: string) => void; onViewImages?: (images: string[], startIndex: number) => void; pendingMessage?: string | null; isWorking?: boolean; refreshKey?: number }) {
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

  const conv = entries.filter(e => e.type === "user" || e.type === "assistant" || e.type === "command");
  if (conv.length === 0) {
    return <div className="flex h-full items-center justify-center"><p className="text-th-text-faint">Send a message or drop a file to start</p></div>;
  }

  return (
    <div className="max-w-3xl mx-auto px-8 py-6 space-y-1">
      {conv.map((entry, i) => {
        if (entry.type === "assistant") return <AssistantEntry key={i} entry={entry} files={files} sessionId={sessionId!} onViewFile={onViewFile} />;
        if (entry.type === "user") return <UserEntry key={i} entry={entry} sessionId={sessionId!} files={files} onViewFile={onViewFile} onViewImages={onViewImages} />;
        if (entry.type === "command") return <CommandEntry key={i} entry={entry} />;
        return null;
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
