"use client";

import { useRef, useState, useEffect, type FormEvent, type DragEvent } from "react";

import { uploadFile as apiUploadFile, fetchCommands, type SlashCommand } from "@/lib/api";

type UploadedFile = {
  originalName: string;
  serverName: string;
  status: "uploading" | "uploaded" | "error";
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
  const [showSlashMenu, setShowSlashMenu] = useState(false);
  const [slashFilter, setSlashFilter] = useState("");
  const [slashCommands, setSlashCommands] = useState<SlashCommand[]>([]);
  const [slashHighlight, setSlashHighlight] = useState(0);
  const slashCommandsCacheRef = useRef<SlashCommand[] | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textInputRef = useRef<HTMLInputElement>(null);

  const allUploaded = uploadedFiles.length === 0 || uploadedFiles.every((f) => f.status === "uploaded");

  // Upload a single file immediately
  const uploadFileImmediately = async (file: File) => {
    // Add to list as "uploading"
    const entry: UploadedFile = { originalName: file.name, serverName: file.name, status: "uploading" };
    setUploadedFiles((prev) => [...prev, entry]);

    // Ensure session exists
    let sid = sessionId;
    if (!sid && ensureSession) {
      sid = await ensureSession();
    }
    if (!sid) {
      setUploadedFiles((prev) =>
        prev.map((f) => (f === entry ? { ...f, status: "error" } : f))
      );
      return;
    }

    try {
      const uploaded = await apiUploadFile(sid, file);

      if (uploaded.length === 0) {
        throw new Error("Server returned empty upload list");
      }

      const serverName = uploaded[0];

      setUploadedFiles((prev) =>
        prev.map((f) =>
          f.originalName === file.name && f.status === "uploading"
            ? { ...f, serverName, status: "uploaded" }
            : f
        )
      );

      onFilesUploaded?.(uploaded);
    } catch (error) {
      console.warn("File upload failed:", error);
      setUploadedFiles((prev) =>
        prev.map((f) =>
          f.originalName === file.name && f.status === "uploading"
            ? { ...f, status: "error" }
            : f
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
    }
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length) {
      addFiles(e.dataTransfer.files);
    }
  };

  // @ and / autocomplete logic
  const handleInputChange = (value: string) => {
    setInput(value);
    const cursorPos = textInputRef.current?.selectionStart ?? value.length;
    // Find the @ token at cursor
    const beforeCursor = value.slice(0, cursorPos);
    const atMatch = beforeCursor.match(/@([^\s]*)$/);
    if (atMatch && sessionFiles && sessionFiles.length > 0) {
      setShowAtMenu(true);
      setAtFilter(atMatch[1].toLowerCase());
      setAtCursorPos(cursorPos);
      setShowSlashMenu(false);
    } else {
      setShowAtMenu(false);
    }

    // Check for / at start of input
    const slashMatch = value.match(/^\/(\S*)$/);
    if (slashMatch) {
      const filter = slashMatch[1].toLowerCase();
      setSlashFilter(filter);
      setSlashHighlight(0);
      // Lazy-load commands
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
    } else {
      setShowSlashMenu(false);
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
      if (!target.closest("[data-at-menu]") && !target.closest("[data-slash-menu]") && !target.closest("input[type=text]")) {
        setShowAtMenu(false);
        setShowSlashMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  return (
    <div
      className={`border-t border-th-border ${dragOver ? "bg-th-surface border-th-accent/50" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {/* Uploaded file chips */}
      {uploadedFiles.length > 0 && (
        <div className="px-4 pt-3 flex flex-wrap gap-2">
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
              <span>
                {f.status === "uploading" ? "\u23F3" : f.status === "error" ? "\u26A0" : "\u2713"}
              </span>
              <span className="max-w-[200px] truncate">
                {f.serverName !== f.originalName && f.status === "uploaded"
                  ? `${f.serverName} (was ${f.originalName})`
                  : f.originalName}
              </span>
              {f.status !== "uploading" && (
                <button
                  onClick={() => removeFile(f.originalName)}
                  className="hover:text-th-accent ml-0.5"
                >
                  \u00D7
                </button>
              )}
            </span>
          ))}
        </div>
      )}

      {/* Input row */}
      <form onSubmit={handleSubmit} className="p-3 flex items-center gap-2">
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

        <div className="relative flex flex-1 items-center bg-th-bg border border-th-border rounded-xl px-2 focus-within:border-th-accent/50 focus-within:ring-1 focus-within:ring-th-accent/20 transition-all">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center justify-center w-8 h-8 rounded-full text-th-text hover:text-th-accent hover:bg-th-surface-hover transition-colors flex-shrink-0 text-lg font-light"
            title="Attach files"
          >
            +
          </button>
          <input
            ref={textInputRef}
            type="text"
            value={input}
            onChange={(e) => handleInputChange(e.target.value)}
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
            placeholder={
              !allUploaded
                ? "Uploading files..."
                : isWorking
                  ? "Type to interrupt, or press Escape to stop..."
                : uploadedFiles.length > 0
                  ? `Message about ${uploadedFiles.length} file(s)... (type @ to reference files)`
                  : "Message Claude Code... (type @ to reference files)"
            }
            disabled={disabled || !allUploaded}
            className="flex-1 bg-transparent px-2 py-2.5 text-sm text-th-text focus:outline-none placeholder-th-text-muted disabled:opacity-50"
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
