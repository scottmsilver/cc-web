"use client";

import { useRef, useState, type FormEvent, type DragEvent } from "react";

const CCHOST_API = "http://localhost:8420";

type PendingFile = {
  name: string;
  file: File;
  uploaded: boolean;
};

export function ChatInput({
  onSend,
  disabled,
  sessionId,
  onFilesUploaded,
}: {
  onSend: (message: string) => void;
  disabled: boolean;
  sessionId: string | null;
  onFilesUploaded?: (files: string[]) => void;
}) {
  const [input, setInput] = useState("");
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textInputRef = useRef<HTMLInputElement>(null);

  const uploadFiles = async (files: File[]): Promise<string[]> => {
    if (!sessionId) return [];
    setUploading(true);
    const formData = new FormData();
    files.forEach((f) => formData.append(f.name, f));
    try {
      const res = await fetch(
        `${CCHOST_API}/api/sessions/${sessionId}/upload`,
        { method: "POST", body: formData }
      );
      const data = await res.json();
      const uploaded = data.uploaded || [];
      onFilesUploaded?.(uploaded);
      return uploaded;
    } catch {
      return [];
    } finally {
      setUploading(false);
    }
  };

  const addFiles = (fileList: FileList | File[]) => {
    const newFiles = Array.from(fileList).map((f) => ({
      name: f.name,
      file: f,
      uploaded: false,
    }));
    setPendingFiles((prev) => [...prev, ...newFiles]);
  };

  const removeFile = (name: string) => {
    setPendingFiles((prev) => prev.filter((f) => f.name !== name));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (disabled || (!input.trim() && pendingFiles.length === 0)) return;

    // Upload pending files first
    let fileNames: string[] = [];
    if (pendingFiles.length > 0) {
      fileNames = await uploadFiles(pendingFiles.map((f) => f.file));
    }

    // Build message with file references
    let message = input.trim();
    if (fileNames.length > 0) {
      const fileList = fileNames.join(", ");
      if (message) {
        message = `[Attached: ${fileList}]\n\n${message}`;
      } else {
        message = `I've attached these files to the working directory: ${fileList}. What would you like me to do with them?`;
      }
    }

    if (message) {
      onSend(message);
      setInput("");
      setPendingFiles([]);
    }
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length) {
      addFiles(e.dataTransfer.files);
    }
  };

  return (
    <div
      className={`border-t border-[#3a3a3a] ${dragOver ? "bg-[#2a2520] border-[#d77757]/50" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
    >
      {/* Pending files */}
      {pendingFiles.length > 0 && (
        <div className="px-4 pt-3 flex flex-wrap gap-2">
          {pendingFiles.map((f) => (
            <span
              key={f.name}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-[#232323] border border-[#3a3a3a] text-xs"
            >
              <span className="opacity-60">
                {f.name.endsWith(".pdf") ? "📄" : f.name.endsWith(".xlsx") ? "📊" : "📎"}
              </span>
              <span className="text-[#e8e4df] max-w-[200px] truncate">{f.name}</span>
              <button
                onClick={() => removeFile(f.name)}
                className="text-[#8a8580] hover:text-[#d77757] ml-0.5"
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}

      {/* Input row */}
      <form onSubmit={handleSubmit} className="p-3 flex items-center gap-2">
        {/* Attach button — always clickable */}
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="flex items-center justify-center w-10 h-10 rounded-xl hover:bg-[#2d2d2d] text-[#8a8580] hover:text-[#d77757] transition-colors flex-shrink-0"
          title="Attach files"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
          </svg>
        </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => e.target.files && addFiles(e.target.files)}
        />

        {/* Text input */}
        <input
          ref={textInputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            uploading
              ? "Uploading files..."
              : pendingFiles.length > 0
                ? `Message about ${pendingFiles.length} file(s)...`
                : "Message Claude Code..."
          }
          disabled={disabled || uploading}
          className="flex-1 bg-[#232323] border border-[#3a3a3a] rounded-xl px-4 py-2.5 text-sm text-[#e8e4df] focus:outline-none focus:border-[#d77757]/50 focus:ring-1 focus:ring-[#d77757]/20 placeholder-[#6a6560] disabled:opacity-50 transition-all"
        />

        {/* Send button */}
        <button
          type="submit"
          disabled={disabled || uploading || (!input.trim() && pendingFiles.length === 0)}
          className="flex items-center justify-center w-10 h-10 rounded-xl bg-[#d77757] hover:bg-[#c46847] disabled:bg-[#333333] disabled:text-[#6a6560] text-white transition-colors disabled:cursor-not-allowed flex-shrink-0"
        >
          ↑
        </button>
      </form>

      {dragOver && (
        <div className="absolute inset-0 bg-[#d77757]/10 border-2 border-dashed border-[#d77757]/50 rounded-xl flex items-center justify-center pointer-events-none z-10">
          <p className="text-[#d77757] font-medium">Drop files here</p>
        </div>
      )}
    </div>
  );
}
