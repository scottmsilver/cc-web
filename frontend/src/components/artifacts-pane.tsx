"use client";

import { FileViewer } from "@/components/file-viewer";
import { getFileUrl } from "@/lib/api";

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

  return (
    <div className="flex flex-col h-full border-l border-gray-200 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-200 bg-gray-50 flex-shrink-0">
        <select
          value={selectedFile}
          onChange={(e) => onSelectFile(e.target.value)}
          className="flex-1 text-xs font-mono bg-white border border-gray-300 rounded px-2 py-1 text-gray-800 cursor-pointer"
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
                  <option key={f} value={f}>{f.split("/").pop()}</option>
                ))}
              </optgroup>
            );
          })}
        </select>
        <a
          href={getFileUrl(sessionId, selectedFile)}
          download={selectedFile.split("/").pop()}
          className="text-xs text-gray-400 hover:text-[var(--th-accent)] cursor-pointer"
          title="Download"
        >↓</a>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-sm cursor-pointer" title="Close">✕</button>
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        <FileViewer sessionId={sessionId} filePath={selectedFile} onClose={onClose} hideHeader />
      </div>
    </div>
  );
}
