"use client";

import { useState, useEffect, useRef } from "react";

async function copyImageToClipboard(src: string): Promise<boolean> {
  try {
    const res = await fetch(src);
    const blob = await res.blob();
    const pngBlob = blob.type === "image/png" ? blob : await new Promise<Blob>((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        canvas.getContext("2d")!.drawImage(img, 0, 0);
        canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("toBlob failed"))), "image/png");
      };
      img.onerror = () => reject(new Error("image load failed"));
      img.src = src;
    });
    await navigator.clipboard.write([new ClipboardItem({ "image/png": pngBlob })]);
    return true;
  } catch {
    return false;
  }
}

function downloadDataUri(src: string, filename: string) {
  const a = document.createElement("a");
  a.href = src;
  a.download = filename;
  a.click();
}

const NAV_BTN = "w-7 h-7 flex items-center justify-center rounded border border-th-border text-th-text-muted hover:text-th-accent hover:border-th-accent/50 hover:bg-th-surface-hover transition-colors cursor-pointer disabled:opacity-25 disabled:cursor-default";
const ICON_BTN = "w-7 h-7 flex items-center justify-center rounded text-th-text-faint hover:text-th-accent hover:bg-th-surface-hover transition-colors cursor-pointer";

export function ImageGalleryViewer({
  images,
  startIndex,
  onClose,
}: {
  images: string[];
  startIndex: number;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(startIndex);
  const [copyFeedback, setCopyFeedback] = useState(false);
  const imageAreaRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);

  useEffect(() => { setIndex(startIndex); }, [startIndex]);

  // Scroll main image to top on page change
  useEffect(() => {
    if (imageAreaRef.current) imageAreaRef.current.scrollTop = 0;
  }, [index]);

  // Auto-scroll sidebar to keep active thumbnail visible
  useEffect(() => {
    const el = sidebarRef.current?.querySelector(`[data-thumb="${index}"]`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [index]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") setIndex(i => Math.max(0, i - 1));
      if (e.key === "ArrowRight") setIndex(i => Math.min(images.length - 1, i + 1));
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [images.length, onClose]);

  const handleCopy = async () => {
    const ok = await copyImageToClipboard(images[index]);
    if (ok) { setCopyFeedback(true); setTimeout(() => setCopyFeedback(false), 1500); }
  };

  return (
    <div className="flex flex-col h-full border-l border-th-border overflow-hidden" role="dialog" aria-label="Image gallery">
      {/* Header */}
      <div className="flex items-center gap-1 px-2 py-1.5 border-b border-th-border bg-th-surface flex-shrink-0">
        <span className="text-xs text-th-text-muted flex-1 truncate pl-1">
          Page {index + 1} of {images.length}
        </span>
        {/* Page nav */}
        <div className="flex items-center gap-1">
          <button type="button" disabled={index <= 0} onClick={() => setIndex(i => i - 1)} className={NAV_BTN} title="Previous page">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="15 18 9 12 15 6" /></svg>
          </button>
          <span className="text-xs text-th-text-muted tabular-nums min-w-[2.5rem] text-center">{index + 1}/{images.length}</span>
          <button type="button" disabled={index >= images.length - 1} onClick={() => setIndex(i => i + 1)} className={NAV_BTN} title="Next page">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="9 18 15 12 9 6" /></svg>
          </button>
        </div>
        {/* Copy */}
        <button type="button" onClick={() => void handleCopy()} className={ICON_BTN} title="Copy image">
          {copyFeedback ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12" /></svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </svg>
          )}
        </button>
        {/* Download */}
        <button type="button" onClick={() => downloadDataUri(images[index], `page-${index + 1}.png`)} className={ICON_BTN} title="Download image">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
          </svg>
        </button>
        {/* Close */}
        <button type="button" onClick={onClose} className={ICON_BTN} title="Close" aria-label="Close gallery">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
        </button>
      </div>

      {/* Body: sidebar + main image */}
      <div className="flex flex-1 min-h-0">
        {/* Thumbnail sidebar */}
        {images.length > 1 && (
          <>
            <div ref={sidebarRef} className="w-[80px] overflow-y-auto flex-shrink-0 py-2 px-1.5 space-y-1.5 bg-th-surface">
              {images.map((src, i) => (
                <button
                  key={i}
                  type="button"
                  data-thumb={i}
                  onClick={() => setIndex(i)}
                  className={`block w-full rounded overflow-hidden border-2 transition-colors ${
                    i === index ? "border-th-accent" : "border-transparent hover:border-th-border"
                  }`}
                  title={`Page ${i + 1}`}
                >
                  <img src={src} alt={`Page ${i + 1}`} className="w-full" />
                </button>
              ))}
            </div>
            <div className="w-px bg-th-border flex-shrink-0" />
          </>
        )}

        {/* Main image */}
        <div ref={imageAreaRef} className="flex-1 overflow-auto p-2 flex items-start justify-center bg-th-bg">
          <img src={images[index]} alt={`Page ${index + 1}`} className="max-w-full rounded shadow-sm" />
        </div>
      </div>
    </div>
  );
}
