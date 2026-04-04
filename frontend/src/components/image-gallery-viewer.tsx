"use client";

import { useState, useEffect } from "react";

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

  useEffect(() => { setIndex(startIndex); }, [startIndex]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft" && index > 0) setIndex(i => i - 1);
      if (e.key === "ArrowRight" && index < images.length - 1) setIndex(i => i + 1);
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, images.length, onClose]);

  return (
    <div className="flex flex-col h-full border-l border-gray-200 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-200 bg-gray-50 flex-shrink-0">
        <button
          disabled={index <= 0}
          onClick={() => setIndex(i => i - 1)}
          className="px-2 py-0.5 rounded border border-gray-300 text-xs disabled:opacity-30 cursor-pointer hover:bg-gray-100"
        >←</button>
        <span className="text-xs text-gray-600 flex-1 text-center">Page {index + 1} of {images.length}</span>
        <button
          disabled={index >= images.length - 1}
          onClick={() => setIndex(i => i + 1)}
          className="px-2 py-0.5 rounded border border-gray-300 text-xs disabled:opacity-30 cursor-pointer hover:bg-gray-100"
        >→</button>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-700 text-sm cursor-pointer ml-1" title="Close">✕</button>
      </div>
      <div className="flex-1 overflow-auto p-2 flex items-start justify-center bg-gray-50">
        <img src={images[index]} alt={`Page ${index + 1}`} className="max-w-full rounded shadow-sm" />
      </div>
      {/* Thumbnail strip */}
      {images.length > 1 && (
        <div className="flex-shrink-0 border-t border-gray-200 overflow-x-auto px-2 py-1.5 bg-white">
          <div className="flex gap-1">
            {images.map((src, i) => (
              <img
                key={i}
                src={src}
                alt={`Thumb ${i + 1}`}
                className={`h-12 rounded cursor-pointer flex-shrink-0 border-2 transition-colors ${
                  i === index ? "border-[var(--th-accent)]" : "border-transparent hover:border-gray-300"
                }`}
                onClick={() => setIndex(i)}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
