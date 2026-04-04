"use client";

import React from "react";
import { CCHOST_API } from "@/lib/config";

/**
 * Renders file paths as clickable links to the cchost file API.
 * Two variants:
 *   - "badge" (default): dark chip style for use in standalone file lists
 *   - "inline": minimal underlined style for use inside chat messages
 */

// Match file paths in text — simple filenames or paths with /
const FILE_PATH_RE = /(?:^|\s|`)((?:[\w.-]+\/)*[\w.-]+\.(?:json|md|txt|pdf|xlsx|xls|csv|py|sh|yaml|yml|html|css|js|ts|png|jpg|gif|log))(?:\s|$|`|,|\.|;|\))/gi;

export function makeFileUrl(sessionId: string, filePath: string): string {
  return `${CCHOST_API}/api/sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(filePath)}`;
}

export function FileLink({
  filePath,
  sessionId,
  variant = "badge",
  files,
  onViewFile,
}: {
  filePath: string;
  sessionId: string;
  variant?: "badge" | "inline";
  files?: string[];
  onViewFile?: (path: string) => void;
}) {
  const resolved =
    files?.find((f) => f === filePath) ||
    files?.find((f) => f.endsWith(`/${filePath}`)) ||
    filePath;
  const url = makeFileUrl(sessionId, resolved);
  const isLocalFile = files?.some(
    (f) => f === resolved || f.endsWith(`/${filePath}`),
  );

  if (variant === "inline") {
    return (
      <span className="inline-flex items-center gap-0.5">
        {isLocalFile && onViewFile ? (
          <button
            onClick={() => onViewFile(resolved)}
            className="text-[var(--th-accent)] hover:text-[var(--th-accent-hover)] text-xs font-mono underline underline-offset-2 cursor-pointer"
          >
            {filePath}
          </button>
        ) : (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--th-accent)] hover:text-[var(--th-accent-hover)] text-xs font-mono underline underline-offset-2 cursor-pointer"
          >
            {filePath}
          </a>
        )}
        <a
          href={url}
          download={filePath.split("/").pop()}
          className="text-gray-300 hover:text-[var(--th-accent)] text-[10px] cursor-pointer ml-0.5"
          title="Download"
        >
          ↓
        </a>
      </span>
    );
  }

  // "badge" variant (default)
  const isPdf = filePath.endsWith(".pdf");
  const isBin = /\.(pdf|xlsx|xls|zip|png|jpg|gif)$/i.test(filePath);

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      download={isBin ? filePath.split("/").pop() : undefined}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-rose-400 hover:text-rose-300 text-xs font-mono no-underline transition-colors border border-zinc-700 hover:border-rose-500/50"
      title={`Download ${filePath}`}
    >
      <span className="opacity-60">{isPdf ? "\u{1F4C4}" : isBin ? "\u{1F4CA}" : "\u{1F4DD}"}</span>
      {filePath}
      <span className="opacity-40 text-[10px]">↓</span>
    </a>
  );
}

/**
 * Process text content and replace file paths with clickable links.
 */
export function linkifyFiles(text: string, sessionId: string): (string | React.ReactElement)[] {
  if (!sessionId) return [text];

  const parts: (string | React.ReactElement)[] = [];
  let lastIndex = 0;
  let match;

  FILE_PATH_RE.lastIndex = 0;

  while ((match = FILE_PATH_RE.exec(text)) !== null) {
    const filePath = match[1];
    const matchStart = match.index + match[0].indexOf(filePath);
    const matchEnd = matchStart + filePath.length;

    if (matchStart > lastIndex) {
      parts.push(text.slice(lastIndex, matchStart));
    }

    parts.push(
      <FileLink
        key={`${filePath}-${matchStart}`}
        filePath={filePath}
        sessionId={sessionId}
      />
    );

    lastIndex = matchEnd;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}
