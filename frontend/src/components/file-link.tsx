"use client";

import React from "react";

/**
 * Renders file paths in Claude's responses as clickable download links.
 * Detects patterns like `extracted.json`, `audit_findings.md`, `extracted_pages/Hearth_Home.pdf`
 * and makes them clickable links to the cchost file API.
 */

const CCHOST_API = "http://localhost:8420";

// File extensions we recognize
const FILE_EXTENSIONS = /\.(json|md|txt|pdf|xlsx|xls|csv|py|sh|yaml|yml|html|css|js|ts|png|jpg|gif|log)$/i;

// Match file paths in text — simple filenames or paths with /
const FILE_PATH_RE = /(?:^|\s|`)((?:[\w.-]+\/)*[\w.-]+\.(?:json|md|txt|pdf|xlsx|xls|csv|py|sh|yaml|yml|html|css|js|ts|png|jpg|gif|log))(?:\s|$|`|,|\.|;|\))/gi;

export function makeFileUrl(sessionId: string, filePath: string): string {
  return `${CCHOST_API}/api/sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(filePath)}`;
}

export function FileLink({
  filePath,
  sessionId,
}: {
  filePath: string;
  sessionId: string;
}) {
  const url = makeFileUrl(sessionId, filePath);
  const isPdf = filePath.endsWith(".pdf");
  const isBinary = /\.(pdf|xlsx|xls|png|jpg|gif)$/i.test(filePath);

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      download={isBinary ? filePath.split("/").pop() : undefined}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-rose-400 hover:text-rose-300 text-xs font-mono no-underline transition-colors border border-zinc-700 hover:border-rose-500/50"
      title={`Download ${filePath}`}
    >
      <span className="opacity-60">{isPdf ? "📄" : isBinary ? "📊" : "📝"}</span>
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

  // Reset regex
  FILE_PATH_RE.lastIndex = 0;

  while ((match = FILE_PATH_RE.exec(text)) !== null) {
    const filePath = match[1];
    const matchStart = match.index + match[0].indexOf(filePath);
    const matchEnd = matchStart + filePath.length;

    // Add text before the match
    if (matchStart > lastIndex) {
      parts.push(text.slice(lastIndex, matchStart));
    }

    // Add the file link
    parts.push(
      <FileLink
        key={`${filePath}-${matchStart}`}
        filePath={filePath}
        sessionId={sessionId}
      />
    );

    lastIndex = matchEnd;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}
