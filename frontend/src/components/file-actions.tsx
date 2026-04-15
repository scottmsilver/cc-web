"use client";

import { useState } from "react";
import { getFileUrl } from "@/lib/api";
import { getFileName } from "@/lib/config";

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

const RENDERED_COPY_ICON = (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="16" y1="13" x2="8" y2="13" />
    <line x1="16" y1="17" x2="8" y2="17" />
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

/** Render a PDF page to a PNG blob. */
export async function renderPdfPageToBlob(
  fileUrl: string,
  pageNum: number,
  scale = 2,
): Promise<Blob> {
  const pdfjsLib = await import("pdfjs-dist");
  pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
  const pdf = await pdfjsLib.getDocument(fileUrl).promise;
  const page = await pdf.getPage(Math.min(pageNum, pdf.numPages));
  const viewport = page.getViewport({ scale });
  const canvas = document.createElement("canvas");
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
  return new Promise<Blob>((res) => canvas.toBlob((b) => res(b!), "image/png"));
}

/** Copy file content to clipboard. Images as PNG, PDFs as page image, text as text. */
export async function copyFileContent(
  fileUrl: string,
  ext: string,
  pdfPage?: number,
  mode: "source" | "rendered" = "source",
): Promise<void> {
  const isImage = /^(png|jpg|jpeg|gif|webp|svg)$/i.test(ext);
  const isPdf = ext === "pdf";
  if (isImage) {
    await copyImageToClipboard(fileUrl);
  } else if (isPdf) {
    const blob = await renderPdfPageToBlob(fileUrl, pdfPage || 1);
    await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
  } else if (ext === "md" && mode === "rendered") {
    // Render markdown into an off-screen element with clean default styling,
    // then copy via Selection API. This avoids picking up theme colors.
    const r = await fetch(fileUrl);
    const md = await r.text();
    const { default: ReactDOMClient } = await import("react-dom/client");
    const { default: ReactMarkdown } = await import("react-markdown");
    const { default: remarkGfm } = await import("remark-gfm");
    const { createElement } = await import("react");

    const container = document.createElement("div");
    Object.assign(container.style, {
      position: "fixed", left: "0", top: "0",
      opacity: "0.01", zIndex: "-1", pointerEvents: "none",
      background: "white", color: "black",
      fontFamily: "system-ui, sans-serif", fontSize: "14px", lineHeight: "1.6",
      padding: "16px", maxWidth: "800px", overflow: "hidden", height: "1px",
    });
    // Add basic block styling so spacing copies correctly
    const style = document.createElement("style");
    style.textContent = `
      .copy-md h1, .copy-md h2, .copy-md h3, .copy-md h4 { margin: 1em 0 0.5em; font-weight: bold; }
      .copy-md h1 { font-size: 1.5em; } .copy-md h2 { font-size: 1.3em; } .copy-md h3 { font-size: 1.1em; }
      .copy-md p { margin: 0.5em 0; } .copy-md ul, .copy-md ol { margin: 0.5em 0; padding-left: 1.5em; }
      .copy-md li { margin: 0.25em 0; } .copy-md blockquote { margin: 0.5em 0; padding-left: 1em; border-left: 3px solid #ccc; }
      .copy-md pre { margin: 0.5em 0; padding: 0.5em; background: #f5f5f5; font-family: monospace; font-size: 0.9em; white-space: pre-wrap; }
      .copy-md code { font-family: monospace; font-size: 0.9em; background: #f0f0f0; padding: 0.1em 0.3em; border-radius: 3px; }
      .copy-md pre code { background: none; padding: 0; }
      .copy-md table { border-collapse: collapse; margin: 0.5em 0; } .copy-md td, .copy-md th { border: 1px solid #ccc; padding: 0.3em 0.6em; }
      .copy-md hr { margin: 1em 0; border: none; border-top: 1px solid #ccc; }
      .copy-md strong { font-weight: bold; } .copy-md em { font-style: italic; }
    `;
    container.appendChild(style);
    container.classList.add("copy-md");
    document.body.appendChild(container);

    const root = ReactDOMClient.createRoot(container);
    root.render(createElement(ReactMarkdown, { remarkPlugins: [remarkGfm] }, md));
    // Wait for render
    await new Promise((res) => setTimeout(res, 50));

    const range = document.createRange();
    range.selectNodeContents(container);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    document.execCommand("copy");
    sel?.removeAllRanges();

    root.unmount();
    document.body.removeChild(container);
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
  pdfPage,
}: {
  sessionId: string;
  filePath: string;
  pdfPage?: number;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  const [renderedCopyState, setRenderedCopyState] = useState<"idle" | "copied">("idle");
  const [refState, setRefState] = useState<"idle" | "copied">("idle");
  const fileUrl = getFileUrl(sessionId, filePath);
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  const isMd = ext === "md";

  const handleCopy = async (mode: "source" | "rendered" = "source") => {
    try {
      await copyFileContent(fileUrl, ext, pdfPage, mode);
    } catch {
      const r = await fetch(fileUrl);
      await navigator.clipboard.writeText(await r.text());
    }
    if (mode === "rendered") {
      setRenderedCopyState("copied");
      setTimeout(() => setRenderedCopyState("idle"), 1500);
    } else {
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 1500);
    }
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
        onClick={() => void handleCopy("source")}
        className={ICON_BTN}
        title={copyState === "copied" ? "Copied!" : isMd ? "Copy markdown source" : "Copy content"}
      >
        {copyState === "copied" ? CHECK_ICON : COPY_ICON}
      </button>
      {isMd && (
        <button
          onClick={() => void handleCopy("rendered")}
          className={ICON_BTN}
          title={renderedCopyState === "copied" ? "Copied formatted!" : "Copy as formatted text"}
        >
          {renderedCopyState === "copied" ? CHECK_ICON : RENDERED_COPY_ICON}
        </button>
      )}
      <button
        onClick={handleRef}
        className={ICON_BTN}
        title={refState === "copied" ? "Copied @ref!" : `Copy @./${filePath}`}
      >
        {refState === "copied" ? CHECK_ICON : <span className="text-xs font-bold leading-none">@</span>}
      </button>
      <a
        href={fileUrl}
        download={getFileName(filePath)}
        className={ICON_BTN}
        title="Download"
      >
        {DOWNLOAD_ICON}
      </a>
    </>
  );
}
