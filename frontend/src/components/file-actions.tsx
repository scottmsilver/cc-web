"use client";

import { useState } from "react";
import { getFileUrl } from "@/lib/api";

const CHECK_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

const COPY_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect x="9" y="9" width="13" height="13" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);

const DOWNLOAD_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </svg>
);

const ICON_BTN =
  "w-7 h-7 flex items-center justify-center rounded text-th-text-faint hover:text-th-accent hover:bg-th-surface-hover transition-colors cursor-pointer";

/** Copy an image URL to clipboard as PNG. */
export async function copyImageToClipboard(url: string): Promise<void> {
  const r = await fetch(url);
  const blob = await r.blob();
  if (blob.type === "image/png") {
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
  } else {
    const img = new Image();
    img.crossOrigin = "anonymous";
    await new Promise<void>((res, rej) => {
      img.onload = () => res();
      img.onerror = rej;
      img.src = URL.createObjectURL(blob);
    });
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    canvas.getContext("2d")!.drawImage(img, 0, 0);
    const pngBlob = await new Promise<Blob>((res) =>
      canvas.toBlob((b) => res(b!), "image/png"),
    );
    await navigator.clipboard.write([new ClipboardItem({ "image/png": pngBlob })]);
    URL.revokeObjectURL(img.src);
  }
}

/** Copy file content to clipboard. Images as PNG, text as text. */
export async function copyFileContent(fileUrl: string, ext: string): Promise<void> {
  const isImage = /^(png|jpg|jpeg|gif|webp|svg)$/i.test(ext);
  if (isImage) {
    await copyImageToClipboard(fileUrl);
  } else {
    const r = await fetch(fileUrl);
    const text = await r.text();
    await navigator.clipboard.writeText(text);
  }
}

/** Copy an @ref string to clipboard. */
export function copyAtRef(filePath: string): Promise<void> {
  return navigator.clipboard.writeText(`@./${filePath}`);
}

/**
 * Compact icon buttons for file actions: copy content, copy @ref, download.
 * Designed for toolbar/header use — icon-only with hover titles.
 */
export function FileActionButtons({
  sessionId,
  filePath,
}: {
  sessionId: string;
  filePath: string;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  const [refState, setRefState] = useState<"idle" | "copied">("idle");
  const fileUrl = getFileUrl(sessionId, filePath);
  const ext = filePath.split(".").pop()?.toLowerCase() || "";

  const handleCopy = async () => {
    try {
      await copyFileContent(fileUrl, ext);
    } catch {
      // Fallback to text
      const r = await fetch(fileUrl);
      await navigator.clipboard.writeText(await r.text());
    }
    setCopyState("copied");
    setTimeout(() => setCopyState("idle"), 1500);
  };

  const handleRef = () => {
    void copyAtRef(filePath).then(() => {
      setRefState("copied");
      setTimeout(() => setRefState("idle"), 1500);
    });
  };

  return (
    <>
      <button
        onClick={() => void handleCopy()}
        className={ICON_BTN}
        title={copyState === "copied" ? "Copied!" : "Copy content"}
      >
        {copyState === "copied" ? CHECK_ICON : COPY_ICON}
      </button>
      <button
        onClick={handleRef}
        className={ICON_BTN}
        title={refState === "copied" ? "Copied @ref!" : `Copy @./${filePath}`}
      >
        {refState === "copied" ? CHECK_ICON : <span className="text-xs font-bold leading-none">@</span>}
      </button>
      <a
        href={fileUrl}
        download={filePath.split("/").pop()}
        className={ICON_BTN}
        title="Download"
      >
        {DOWNLOAD_ICON}
      </a>
    </>
  );
}
