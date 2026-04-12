"use client";

import { useRef, useState, useEffect, type FormEvent, type DragEvent, type ClipboardEvent } from "react";

import { uploadFile as apiUploadFile, fetchCommands, type SlashCommand } from "@/lib/api";

/**
 * Convert pasted HTML to markdown. Handles the common cases:
 * headings, bold, italic, links, lists, tables, code blocks, images.
 */
function htmlToMarkdown(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");

  function walk(node: Node): string {
    if (node.nodeType === Node.TEXT_NODE) {
      return node.textContent?.replace(/\n/g, " ") ?? "";
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const el = node as HTMLElement;
    const tag = el.tagName.toLowerCase();
    const children = Array.from(el.childNodes).map(walk).join("");

    switch (tag) {
      case "h1": return `# ${children.trim()}\n\n`;
      case "h2": return `## ${children.trim()}\n\n`;
      case "h3": return `### ${children.trim()}\n\n`;
      case "h4": return `#### ${children.trim()}\n\n`;
      case "h5": return `##### ${children.trim()}\n\n`;
      case "h6": return `###### ${children.trim()}\n\n`;
      case "b": case "strong": return `**${children}**`;
      case "i": case "em": return `*${children}*`;
      case "code": return el.parentElement?.tagName.toLowerCase() === "pre" ? children : `\`${children}\``;
      case "pre": return `\n\`\`\`\n${children.trim()}\n\`\`\`\n\n`;
      case "a": {
        const href = el.getAttribute("href");
        return href ? `[${children}](${href})` : children;
      }
      case "img": {
        const src = el.getAttribute("src");
        const alt = el.getAttribute("alt") || "image";
        return src ? `![${alt}](${src})` : "";
      }
      case "br": return "\n";
      case "p": case "div": return `${children.trim()}\n\n`;
      case "blockquote": return children.trim().split("\n").map((l: string) => `> ${l}`).join("\n") + "\n\n";
      case "ul": case "ol": return `\n${children}\n`;
      case "li": {
        const isOrdered = el.parentElement?.tagName.toLowerCase() === "ol";
        const idx = isOrdered ? Array.from(el.parentElement!.children).indexOf(el) + 1 : 0;
        const prefix = isOrdered ? `${idx}. ` : "- ";
        return `${prefix}${children.trim()}\n`;
      }
      case "table": return `\n${children}\n`;
      case "thead": case "tbody": return children;
      case "tr": {
        const cells = Array.from(el.children).map((td) => walk(td).trim());
        const row = `| ${cells.join(" | ")} |`;
        // Add header separator after first row in thead
        if (el.parentElement?.tagName.toLowerCase() === "thead") {
          const sep = `| ${cells.map(() => "---").join(" | ")} |`;
          return `${row}\n${sep}\n`;
        }
        return `${row}\n`;
      }
      case "td": case "th": return children;
      case "hr": return "\n---\n\n";
      case "span": case "section": case "article": case "main": case "header": case "footer": case "nav":
        return children;
      default: return children;
    }
  }

  return walk(doc.body).replace(/\n{3,}/g, "\n\n").trim();
}

type UploadedFile = {
  originalName: string;
  serverName: string;
  status: "uploading" | "uploaded" | "error";
  progress: number; // 0-1
};

/** Apple-style circular progress indicator — fills clockwise as a pie slice. */
function UploadProgress({ progress }: { progress: number }) {
  const r = 7;
  const cx = 10;
  const cy = 10;
  const angle = progress * 360;
  const rad = ((angle - 90) * Math.PI) / 180;
  const x = cx + r * Math.cos(rad);
  const y = cy + r * Math.sin(rad);
  const largeArc = angle > 180 ? 1 : 0;

  return (
    <svg width="20" height="20" viewBox="0 0 20 20" className="flex-shrink-0">
      {/* Background circle */}
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="currentColor" strokeWidth="1.5" opacity="0.25" />
      {progress > 0 && progress < 1 && (
        <path
          d={`M ${cx} ${cy - r} A ${r} ${r} 0 ${largeArc} 1 ${x} ${y} L ${cx} ${cy} Z`}
          fill="currentColor"
          opacity="0.7"
        />
      )}
      {progress >= 1 && (
        <circle cx={cx} cy={cy} r={r} fill="currentColor" opacity="0.7" />
      )}
    </svg>
  );
}

export type GmailDownload = {
  threadId: string;
  threadSubject: string;
  status: "downloading" | "downloaded" | "error";
  files?: string[];
};

export function ChatInput({
  onSend,
  disabled,
  sessionId,
  onFilesUploaded,
  ensureSession,
  sessionFiles,
  isWorking,
  onInterrupt,
  externalInput,
  onInputChange,
  onGmailPickerToggle,
  showGmailPicker,
  gmailDownloads,
  onRemoveGmailDownload,
}: {
  onSend: (message: string) => void;
  disabled: boolean;
  sessionId: string | null;
  onFilesUploaded?: (files: string[]) => void;
  ensureSession?: () => Promise<string>;
  sessionFiles?: string[];
  isWorking?: boolean;
  onInterrupt?: () => void;
  externalInput?: string;
  onInputChange?: (value: string) => void;
  onGmailPickerToggle?: (show: boolean) => void;
  showGmailPicker?: boolean;
  gmailDownloads?: GmailDownload[];
  onRemoveGmailDownload?: (threadId: string) => void;
}) {
  const [localInput, setLocalInput] = useState("");
  const input = externalInput !== undefined ? externalInput : localInput;
  const setInput = (v: string) => {
    if (onInputChange) onInputChange(v);
    else setLocalInput(v);
  };
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [showAtMenu, setShowAtMenu] = useState(false);
  const [atFilter, setAtFilter] = useState("");
  const [atCursorPos, setAtCursorPos] = useState(0);
  const [showPlusMenu, setShowPlusMenu] = useState(false);
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const [slashFilter, setSlashFilter] = useState("");
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([]);
  const [slashHighlight, setSlashHighlight] = useState(0);
  const slashCommandsCacheRef = useRef<SlashCommand[] | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textInputRef = useRef<HTMLTextAreaElement>(null);

  const allUploaded = (uploadedFiles.length === 0 || uploadedFiles.every((f) => f.status === "uploaded"))
    && (!gmailDownloads || gmailDownloads.every((d) => d.status !== "downloading"));

  // Upload a single file — non-blocking, shows progress in chip
  const uploadFileImmediately = async (file: File) => {
    const name = file.name;
    setUploadedFiles((prev) => [...prev, { originalName: name, serverName: name, status: "uploading", progress: 0 }]);

    // Ensure session exists
    let sid = sessionId;
    if (!sid && ensureSession) {
      sid = await ensureSession();
    }
    if (!sid) {
      setUploadedFiles((prev) =>
        prev.map((f) => (f.originalName === name && f.status === "uploading" ? { ...f, status: "error" } : f))
      );
      return;
    }

    try {
      const uploaded = await apiUploadFile(sid, file, (fraction) => {
        setUploadedFiles((prev) =>
          prev.map((f) =>
            f.originalName === name && f.status === "uploading" ? { ...f, progress: fraction } : f
          )
        );
      });

      if (uploaded.length === 0) throw new Error("Server returned empty upload list");

      setUploadedFiles((prev) =>
        prev.map((f) =>
          f.originalName === name && f.status === "uploading"
            ? { ...f, serverName: uploaded[0], status: "uploaded", progress: 1 }
            : f
        )
      );
      onFilesUploaded?.(uploaded);
    } catch (error) {
      console.warn("File upload failed:", error);
      setUploadedFiles((prev) =>
        prev.map((f) =>
          f.originalName === name && f.status === "uploading" ? { ...f, status: "error" } : f
        )
      );
    }
  };

  const addFiles = (fileList: FileList | File[]) => {
    Array.from(fileList).forEach((f) => void uploadFileImmediately(f));
  };

  const removeFile = (originalName: string) => {
    setUploadedFiles((prev) => prev.filter((f) => f.originalName !== originalName));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (disabled || !allUploaded) return;
    if (!input.trim() && uploadedFiles.length === 0) return;

    // Build message with @file references for uploaded files
    const fileRefs = uploadedFiles
      .filter((f) => f.status === "uploaded")
      .map((f) => `@./${f.serverName}`);

    let message = input.trim();
    if (fileRefs.length > 0) {
      const atRefs = fileRefs.join(" ");
      if (message) {
        message = `${atRefs} ${message}`;
      } else {
        message = `${atRefs} What would you like me to do with these files?`;
      }
    }

    if (message) {
      onSend(message);
      setInput("");
      setUploadedFiles([]);
      setShowAtMenu(false);
      if (textInputRef.current) textInputRef.current.style.height = "auto";
    }
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length) {
      addFiles(e.dataTransfer.files);
    }
  };

  // @ and / autocomplete logic — only check when trigger chars are present
  const handleInputChange = (value: string) => {
    setInput(value);

    // Fast path: no trigger characters, dismiss any open menus
    const hasAt = value.includes("@");
    const startsWithSlash = value.startsWith("/");

    if (!hasAt && showAtMenu) setShowAtMenu(false);
    if (!startsWithSlash && showSlashMenu) setShowSlashMenu(false);
    if (!hasAt && !startsWithSlash) return;

    // @ autocomplete
    if (hasAt && sessionFiles && sessionFiles.length > 0) {
      const cursorPos = textInputRef.current?.selectionStart ?? value.length;
      const beforeCursor = value.slice(0, cursorPos);
      const atMatch = beforeCursor.match(/@([^\s]*)$/);
      if (atMatch) {
        setShowAtMenu(true);
        setAtFilter(atMatch[1].toLowerCase());
        setAtCursorPos(cursorPos);
        if (showSlashMenu) setShowSlashMenu(false);
        if (showPlusMenu) setShowPlusMenu(false);
        if (showGmailPicker) onGmailPickerToggle?.(false);
        return;
      }
      if (showAtMenu) setShowAtMenu(false);
    }

    // / slash command autocomplete (only if entire input is a slash command)
    if (startsWithSlash) {
      const slashMatch = value.match(/^\/(\S*)$/);
      if (slashMatch) {
        setSlashFilter(slashMatch[1].toLowerCase());
        setSlashHighlight(0);
        if (showPlusMenu) setShowPlusMenu(false);
        if (showGmailPicker) onGmailPickerToggle?.(false);
        if (slashCommandsCacheRef.current) {
          setSlashCommands(slashCommandsCacheRef.current);
          setShowSlashMenu(true);
        } else {
          void fetchCommands().then((cmds) => {
            slashCommandsCacheRef.current = cmds;
            setSlashCommands(cmds);
            setShowSlashMenu(true);
          });
        }
        return;
      }
      if (showSlashMenu) setShowSlashMenu(false);
    }
  };

  const filteredFiles = (sessionFiles || []).filter((f) =>
    !atFilter || f.toLowerCase().includes(atFilter)
  );

  const filteredSlashCommands = slashCommands.filter((c) =>
    !slashFilter || c.command.toLowerCase().includes(slashFilter)
  );

  const insertSlashCommand = (command: string, submit = true) => {
    setInput(command);
    setShowSlashMenu(false);
    if (submit) {
      // Submit immediately — slash commands are complete as-is
      onSend(command);
      setInput("");
    } else {
      textInputRef.current?.focus();
    }
  };

  const insertAtReference = (filename: string) => {
    const beforeCursor = input.slice(0, atCursorPos);
    const atMatch = beforeCursor.match(/@([^\s]*)$/);
    if (atMatch) {
      const start = atCursorPos - atMatch[0].length;
      const after = input.slice(atCursorPos);
      const newInput = input.slice(0, start) + `@./${filename} ` + after;
      setInput(newInput);
    }
    setShowAtMenu(false);
    textInputRef.current?.focus();
  };

  // Close @ and / menus on blur (with delay for click)
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest("[data-at-menu]") && !target.closest("[data-slash-menu]") && !target.closest("[data-plus-menu]") && !target.closest("input[type=text]")) {
        setShowAtMenu(false);
        setShowSlashMenu(false);
        setShowPlusMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div
      className={`border-t border-th-border max-h-[50vh] flex flex-col ${dragOver ? "bg-th-surface border-th-accent/50" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {/* File chips (uploads + gmail downloads) — scrollable */}
      {(uploadedFiles.length > 0 || (gmailDownloads && gmailDownloads.length > 0)) && (
        <div className="px-4 pt-3 flex flex-wrap gap-2 max-h-28 overflow-y-auto flex-shrink-0">
          {/* Gmail download chips */}
          {gmailDownloads?.map((d) => (
            <span
              key={d.threadSubject}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs border ${
                d.status === "downloading"
                  ? "bg-th-warning-bg border-th-warning-border text-th-warning-text"
                  : d.status === "error"
                    ? "bg-th-error-bg border-th-error-text/30 text-th-error-text"
                    : "bg-th-success-bg border-th-success-text/30 text-th-success-text"
              }`}
            >
              <span>
                {d.status === "downloading" ? "\u23F3" : d.status === "error" ? "\u26A0" : "\u2713"}
              </span>
              <span className="max-w-[250px] truncate">
                {d.status === "downloaded" && d.files
                  ? `${d.files.length} file${d.files.length !== 1 ? "s" : ""} from "${d.threadSubject}"`
                  : d.status === "downloading"
                    ? `Downloading "${d.threadSubject}"...`
                    : d.threadSubject}
              </span>
              {d.status !== "downloading" && (
                <button
                  onClick={() => onRemoveGmailDownload?.(d.threadId)}
                  className="hover:text-th-accent ml-0.5"
                >
                  ×
                </button>
              )}
            </span>
          ))}
          {uploadedFiles.map((f) => (
            <span
              key={f.originalName}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs border ${
                f.status === "uploading"
                  ? "bg-th-warning-bg border-th-warning-border text-th-warning-text"
                  : f.status === "error"
                    ? "bg-th-error-bg border-th-error-text/30 text-th-error-text"
                    : "bg-th-success-bg border-th-success-text/30 text-th-success-text"
              }`}
            >
              {f.status === "uploading" ? (
                <UploadProgress progress={f.progress} />
              ) : f.status === "error" ? (
                <span>{"\u26A0"}</span>
              ) : (
                <span>{"\u2713"}</span>
              )}
              <span className="max-w-[200px] truncate">
                {f.originalName}
              </span>
              <button
                onClick={() => removeFile(f.originalName)}
                className="hover:text-th-accent ml-0.5"
              >
                {"\u00D7"}
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input row — pinned at bottom */}
      <form onSubmit={handleSubmit} className="p-3 pb-4 flex items-end gap-2 flex-shrink-0">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />

        <div className="relative flex flex-1 items-end bg-th-bg border border-th-border rounded-xl px-2 focus-within:border-th-accent/50 focus-within:ring-1 focus-within:ring-th-accent/20 transition-all">
          <div className="relative mb-1" data-plus-menu>
            <button
              type="button"
              onClick={() => {
                setShowPlusMenu((prev) => !prev);
                setShowAtMenu(false);
                setShowSlashMenu(false);
                if (showGmailPicker) onGmailPickerToggle?.(false);
              }}
              className="flex items-center justify-center w-8 h-8 rounded-full text-th-text hover:text-th-accent hover:bg-th-surface-hover transition-colors flex-shrink-0 text-lg font-light"
              title="Attach files"
            >
              +
            </button>
            {showPlusMenu && (
              <div className="absolute bottom-full left-0 mb-1 w-44 rounded-lg border border-th-border bg-th-bg shadow-lg z-50">
                <button
                  type="button"
                  onClick={() => { setShowPlusMenu(false); fileInputRef.current?.click(); }}
                  className="w-full px-3 py-2 text-left text-sm text-th-text hover:bg-th-surface hover:text-th-accent"
                >
                  From computer
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setShowPlusMenu(false);
                    setShowAtMenu(false);
                    setShowSlashMenu(false);
                    onGmailPickerToggle?.(!showGmailPicker);
                  }}
                  className="w-full px-3 py-2 text-left text-sm text-th-text hover:bg-th-surface hover:text-th-accent"
                >
                  From Gmail
                </button>
              </div>
            )}
          </div>
          <textarea
            ref={textInputRef}
            value={input}
            onChange={(e) => {
              handleInputChange(e.target.value);
              // Auto-resize
              e.target.style.height = "auto";
              e.target.style.height = Math.min(e.target.scrollHeight, 200) + "px";
            }}
            onPaste={(e: ClipboardEvent<HTMLTextAreaElement>) => {
              const clipboardData = e.clipboardData;

              // Handle pasted images (screenshots, copied images)
              const imageFiles: File[] = [];
              for (const item of Array.from(clipboardData.items)) {
                if (item.type.startsWith("image/")) {
                  const file = item.getAsFile();
                  if (file) {
                    // Name it with timestamp to avoid collisions
                    const ext = item.type.split("/")[1] || "png";
                    const named = new File([file], `pasted_${Date.now()}.${ext}`, { type: item.type });
                    imageFiles.push(named);
                  }
                }
              }
              if (imageFiles.length > 0) {
                e.preventDefault();
                imageFiles.forEach((f) => void uploadFileImmediately(f));
                return;
              }

              // Handle rich text paste (HTML from docs, web pages, etc.)
              const html = clipboardData.getData("text/html");
              const plainText = clipboardData.getData("text/plain");
              if (html && plainText) {
                // Only convert if the HTML has actual formatting (not just wrapped plain text)
                const hasFormatting = /<(h[1-6]|strong|b|em|i|table|ul|ol|pre|blockquote|a\s)/i.test(html);
                if (hasFormatting) {
                  e.preventDefault();
                  const markdown = htmlToMarkdown(html);
                  // Insert at cursor position
                  const textarea = textInputRef.current;
                  if (textarea) {
                    const start = textarea.selectionStart;
                    const end = textarea.selectionEnd;
                    const newValue = input.slice(0, start) + markdown + input.slice(end);
                    handleInputChange(newValue);
                    // Set cursor after pasted content
                    requestAnimationFrame(() => {
                      textarea.selectionStart = textarea.selectionEnd = start + markdown.length;
                      textarea.style.height = "auto";
                      textarea.style.height = Math.min(textarea.scrollHeight, 200) + "px";
                    });
                  }
                  return;
                }
              }
              // Default: let browser handle plain text paste
            }}
            onKeyDown={(e) => {
              if (showAtMenu && filteredFiles.length > 0) {
                if (e.key === "Tab" || e.key === "Enter") {
                  e.preventDefault();
                  insertAtReference(filteredFiles[0]);
                  return;
                }
                if (e.key === "Escape") {
                  setShowAtMenu(false);
                  return;
                }
              }
              if (showSlashMenu && filteredSlashCommands.length > 0) {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setSlashHighlight((prev) => Math.min(prev + 1, filteredSlashCommands.length - 1));
                  return;
                }
                if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setSlashHighlight((prev) => Math.max(prev - 1, 0));
                  return;
                }
                if (e.key === "Tab" || e.key === "Enter") {
                  e.preventDefault();
                  insertSlashCommand(filteredSlashCommands[slashHighlight].command);
                  return;
                }
                if (e.key === "Escape") {
                  setShowSlashMenu(false);
                  return;
                }
              }
              if (e.key === "Escape" && isWorking && onInterrupt) {
                e.preventDefault();
                onInterrupt();
                return;
              }
              if (e.key === "Enter" && !e.shiftKey && !showAtMenu && !showSlashMenu) {
                e.preventDefault();
                const form = e.currentTarget.closest("form");
                form?.requestSubmit();
              }
            }}
            rows={1}
            placeholder={
              !allUploaded
                ? "Uploading files..."
                : isWorking
                  ? "Type to interrupt, or press Escape to stop..."
                : uploadedFiles.length > 0
                  ? `Message about ${uploadedFiles.length} file(s)... (type @ to reference files)`
                  : "Message Claude Code... (type @ to reference files)"
            }
            disabled={disabled}
            className="flex-1 bg-transparent px-2 py-2.5 text-sm text-th-text focus:outline-none placeholder-th-text-muted disabled:opacity-50 resize-none overflow-hidden"
          />
          {isWorking && !input.trim() ? (
            <button
              type="button"
              onClick={onInterrupt}
              className="flex items-center justify-center w-8 h-8 rounded-lg bg-red-600 hover:bg-red-500 text-white transition-colors flex-shrink-0 mr-1"
              title="Stop (Escape)"
            >
              ■
            </button>
          ) : (
            <button
              type="submit"
              disabled={disabled || !allUploaded || (!input.trim() && uploadedFiles.length === 0)}
              className="flex items-center justify-center w-8 h-8 rounded-lg bg-th-accent hover:bg-th-accent-hover disabled:bg-th-surface-hover disabled:text-th-text-muted text-white transition-colors disabled:cursor-not-allowed flex-shrink-0 mr-1"
            >
              ↑
            </button>
          )}


          {/* @ autocomplete dropdown */}
          {showAtMenu && filteredFiles.length > 0 && (
            <div
              data-at-menu
              className="absolute bottom-full left-0 mb-1 w-80 max-h-48 overflow-y-auto rounded-lg border border-th-border bg-th-bg shadow-lg z-50"
            >
              <div className="px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-th-text-muted border-b border-th-border">
                Files in session
              </div>
              {filteredFiles.map((file) => (
                <button
                  key={file}
                  type="button"
                  onClick={() => insertAtReference(file)}
                  className="w-full px-3 py-2 text-left text-sm text-th-text hover:bg-th-surface hover:text-th-accent font-mono truncate"
                >
                  {file}
                </button>
              ))}
            </div>
          )}

          {/* / slash command autocomplete dropdown */}
          {showSlashMenu && filteredSlashCommands.length > 0 && (
            <div
              data-slash-menu
              className="absolute bottom-full left-0 mb-1 w-80 max-h-48 overflow-y-auto rounded-lg border border-th-border bg-th-bg shadow-lg z-50"
            >
              <div className="px-3 py-1.5 text-[11px] font-medium uppercase tracking-wider text-th-text-muted border-b border-th-border">
                Commands
              </div>
              {filteredSlashCommands.map((cmd, idx) => (
                <button
                  key={cmd.command}
                  type="button"
                  onClick={() => insertSlashCommand(cmd.command)}
                  className={`w-full px-3 py-2 text-left text-sm flex items-baseline gap-2 ${
                    idx === slashHighlight
                      ? "bg-th-surface text-th-accent"
                      : "text-th-text hover:bg-th-surface hover:text-th-accent"
                  }`}
                >
                  <span className="font-mono font-medium">{cmd.command}</span>
                  <span className="text-th-text-muted text-xs truncate">{cmd.description}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </form>

      {dragOver && (
        <div className="absolute inset-0 bg-th-surface border-2 border-dashed border-th-accent/50 rounded-xl flex items-center justify-center pointer-events-none z-10">
          <p className="text-th-accent font-medium">Drop files here</p>
        </div>
      )}
    </div>
  );
}
