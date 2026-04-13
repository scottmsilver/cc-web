"use client";

import { useState } from "react";
import { FileViewer, PdfPageNav, PdfSidebarToggle } from "@/components/file-viewer";
import { FileActionButtons } from "@/components/file-actions";
import { getFileUrl } from "@/lib/api";
import { getFileName, groupByDirectory } from "@/lib/config";

export function ArtifactsPane({
  sessionId,
  files,
  selectedFile,
  onSelectFile,
  onClose,
}: {
  sessionId: string;
  files: string[];
  selectedFile: string;
  onSelectFile: (path: string) => void;
  onClose: () => void;
}) {
  const groups = groupByDirectory(files);
  const dirs = [...groups.keys()].sort();
  const isDir = selectedFile.endsWith("/");
  const isPdf = selectedFile.endsWith(".pdf");
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfPageCount, setPdfPageCount] = useState(0);
  const [pdfSidebar, setPdfSidebar] = useState(false);

  return (
    <div className="flex flex-col h-full border-l border-th-border overflow-hidden">
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-th-border bg-th-surface flex-shrink-0">
        <select
          value={selectedFile}
          onChange={(e) => onSelectFile(e.target.value)}
          className="min-w-0 flex-1 text-xs font-mono bg-th-bg border border-th-border rounded px-1.5 py-1 text-th-text cursor-pointer truncate"
        >
          {dirs.map((dir) => {
            const dirFiles = groups.get(dir)!;
            if (dir === "") {
              return dirFiles.map((f) => (
                <option key={f} value={f}>{f}</option>
              ));
            }
            return (
              <optgroup key={dir} label={dir + "/"}>
                {dirFiles.map((f) => (
                  <option key={f} value={f}>{getFileName(f)}</option>
                ))}
              </optgroup>
            );
          })}
        </select>
        {isPdf && pdfPageCount > 1 && <PdfSidebarToggle open={pdfSidebar} onToggle={() => setPdfSidebar(v => !v)} />}
        {isPdf && <PdfPageNav page={pdfPage} pageCount={pdfPageCount} onPageChange={setPdfPage} />}
        {!isDir && <FileActionButtons sessionId={sessionId} filePath={selectedFile} pdfPage={pdfPage} />}
        <button
          onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded text-th-text-faint hover:text-th-text hover:bg-th-surface-hover transition-colors cursor-pointer"
          title="Close"
        >
          {"\u2715"}
        </button>
      </div>
      <div className="flex flex-col flex-1 min-h-0">
        <FileViewer sessionId={sessionId} filePath={selectedFile} onClose={onClose} hideHeader onNavigate={onSelectFile} pdfPage={pdfPage} onPdfPageChange={setPdfPage} onPdfPageCountChange={setPdfPageCount} pdfSidebarOpen={pdfSidebar} />
      </div>
    </div>
  );
}
