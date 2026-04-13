"use client";

import { useEffect, useState } from "react";
import { CCHOST_API } from "@/lib/config";

/* ── Types matching the server /eml/ endpoint response ── */
type EmlLeaf = {
  index: number;
  content_type: string;
  filename: string | null;
  charset: string | null;
  is_multipart: false;
  size: number;
  body?: string;
  data_url?: string;
  cid?: string;
  children: [];
};

type EmlNode = {
  index: number;
  content_type: string;
  filename: string | null;
  charset: string | null;
  is_multipart: boolean;
  children: EmlNode[];
  size?: number;
  body?: string;
  data_url?: string;
  cid?: string;
};

type EmlData = {
  headers: Record<string, string>;
  text_body: string | null;
  html_body: string | null; // already has cid: resolved to data: URLs
  parts: EmlNode;
  leaves: EmlLeaf[];
};

const GMAIL_FONT = 'Roboto, "Google Sans", Arial, sans-serif';
const GMAIL_STYLE = `<style>body { font-family: ${GMAIL_FONT}; font-size: 14px; line-height: 1.5; color: #202124; margin: 8px; }</style>`;

/* ── Main EML viewer ── */
export function EmlViewer({ sessionId, filePath, onClose }: { sessionId: string; filePath: string; onClose?: () => void }) {
  const [data, setData] = useState<EmlData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"html" | "text" | "parts" | "headers">("html");

  useEffect(() => {
    setLoading(true);
    setError(null);
    setData(null);
    const encodedPath = filePath.split("/").map(encodeURIComponent).join("/");
    fetch(`${CCHOST_API}/api/sessions/${encodeURIComponent(sessionId)}/eml/${encodedPath}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => {
        setData(d as EmlData);
        // Default to HTML if available, else text
        if (!(d as EmlData).html_body && (d as EmlData).text_body) setActiveTab("text");
        setLoading(false);
      })
      .catch((e) => {
        setError(e.message);
        setLoading(false);
      });
  }, [sessionId, filePath]);

  if (loading) return <p className="p-4 text-sm text-th-text-muted">Parsing email...</p>;
  if (error) return <p className="p-4 text-sm text-red-500">Error: {error}</p>;
  if (!data) return null;

  const { headers, text_body, html_body, leaves } = data;
  const images = leaves.filter((l) => l.content_type.startsWith("image/") && l.data_url);
  const attachments = leaves.filter((l) => l.filename && !l.content_type.startsWith("text/"));

  const tabs: { key: typeof activeTab; label: string; show: boolean }[] = [
    { key: "html", label: "HTML", show: !!html_body },
    { key: "text", label: "Text", show: !!text_body },
    { key: "parts", label: `Parts (${leaves.length})`, show: leaves.length > 1 },
    { key: "headers", label: "Headers", show: true },
  ];

  const fmt = (n: number) =>
    n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : n > 1024 ? `${(n / 1024).toFixed(0)}KB` : `${n}B`;

  return (
    <div className="flex flex-col h-full">
      {/* Email header summary */}
      <div className="px-4 py-3 border-b border-th-border space-y-1">
        {onClose && (
          <div className="flex items-center gap-2 -mt-1 mb-1">
            <button onClick={onClose} className="text-th-text-muted hover:text-th-text text-xs cursor-pointer">{"\u2190"} Back</button>
          </div>
        )}
        <div className="text-sm font-medium text-th-text">{headers.subject || "(no subject)"}</div>
        <div className="text-xs text-th-text-muted">
          <span className="font-medium">From:</span> {headers.from || ""}
        </div>
        <div className="text-xs text-th-text-muted">
          <span className="font-medium">To:</span> {headers.to || ""}
        </div>
        {headers.cc && (
          <div className="text-xs text-th-text-muted">
            <span className="font-medium">Cc:</span> {headers.cc}
          </div>
        )}
        {headers.date && <div className="text-xs text-th-text-faint">{headers.date}</div>}
        {attachments.length > 0 && (
          <div className="text-xs text-th-text-muted">
            {attachments.length} attachment{attachments.length !== 1 ? "s" : ""}:{" "}
            {attachments.map((a) => a.filename || a.content_type).join(", ")}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-0 border-b border-th-border flex-shrink-0">
        {tabs
          .filter((t) => t.show)
          .map((t) => (
            <button
              key={t.key}
              onClick={() => setActiveTab(t.key)}
              className={`px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                activeTab === t.key
                  ? "border-th-accent text-th-accent"
                  : "border-transparent text-th-text-muted hover:text-th-text"
              }`}
            >
              {t.label}
            </button>
          ))}
      </div>

      {/* Tab content */}
      <div className={`flex-1 flex flex-col min-h-0 ${activeTab === "html" ? "" : "overflow-auto"}`}>
        {activeTab === "html" && html_body && (
          <div className="flex-1 flex flex-col p-2 min-h-0">
            <iframe
              srcDoc={`${GMAIL_STYLE}${html_body}`}
              className="flex-1 w-full border border-th-border rounded bg-white min-h-0"
              sandbox="allow-same-origin"
              title="Email HTML"
            />
          </div>
        )}

        {activeTab === "text" && text_body && (
          <div
            className="whitespace-pre-wrap p-4 text-sm text-th-text leading-relaxed"
            style={{ fontFamily: GMAIL_FONT }}
          >
            {text_body}
          </div>
        )}

        {activeTab === "parts" && (
          <div className="p-4 space-y-2">
            {leaves.map((leaf, i) => (
              <LeafRow key={i} leaf={leaf} index={i} fmt={fmt} />
            ))}
          </div>
        )}

        {activeTab === "headers" && (
          <div className="p-4">
            <table className="text-xs w-full border-collapse">
              <tbody>
                {Object.entries(headers).map(([key, val]) => (
                  <tr key={key} className="border-b border-th-border">
                    <td className="py-1.5 pr-3 font-medium text-th-text-muted whitespace-nowrap align-top">
                      {key}
                    </td>
                    <td className="py-1.5 text-th-text break-all">{val}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Inline image strip at bottom of text/html views */}
        {(activeTab === "text" || activeTab === "html") && images.length > 0 && (
          <div className="px-4 pb-4">
            <div className="text-xs font-medium text-th-text-muted mb-2">
              Inline Images ({images.length})
            </div>
            <ImageStrip images={images} />
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Leaf part row (expandable) ── */
function LeafRow({ leaf, index, fmt }: { leaf: EmlLeaf; index: number; fmt: (n: number) => string }) {
  const [expanded, setExpanded] = useState(false);
  const isImage = leaf.content_type.startsWith("image/");
  const isText = leaf.content_type.startsWith("text/");

  return (
    <div className="border border-th-border rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-th-surface transition-colors"
      >
        <span className="text-xs text-th-text-faint">{index + 1}</span>
        <span className="text-xs font-mono text-th-text flex-1">
          {leaf.filename || leaf.content_type}
        </span>
        <span className="text-xs text-th-text-muted">{fmt(leaf.size)}</span>
        <span className="text-xs text-th-text-faint">{expanded ? "\u25B2" : "\u25BC"}</span>
      </button>
      {expanded && (
        <div className="border-t border-th-border max-h-64 overflow-auto">
          {isImage && leaf.data_url ? (
            <div className="p-3">
              <img src={leaf.data_url} alt={leaf.filename || ""} className="max-w-full rounded" />
            </div>
          ) : isText && leaf.body ? (
            <pre className="p-3 text-xs font-mono text-th-text whitespace-pre-wrap">
              {leaf.body.slice(0, 5000)}
            </pre>
          ) : (
            <div className="p-3 text-xs text-th-text-muted">
              Binary content ({leaf.content_type}, {fmt(leaf.size)})
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Thumbnail image strip — click to expand ── */
function ImageStrip({ images }: { images: EmlLeaf[] }) {
  const [selected, setSelected] = useState<number | null>(null);

  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {images.map((img, i) =>
          img.data_url ? (
            <button
              key={i}
              onClick={() => setSelected(selected === i ? null : i)}
              className={`rounded border transition-all cursor-pointer ${
                selected === i
                  ? "border-th-accent ring-1 ring-th-accent"
                  : "border-th-border hover:border-th-accent/50"
              }`}
              title={img.filename || img.content_type}
            >
              <img src={img.data_url} alt={img.filename || ""} className="h-12 w-12 object-cover rounded" />
            </button>
          ) : null,
        )}
      </div>
      {selected !== null && images[selected]?.data_url && (
        <div className="mt-2 p-2 border border-th-border rounded-lg bg-th-surface">
          <div className="flex items-center justify-between mb-1">
            <span className="text-xs text-th-text-muted font-mono">
              {images[selected].filename || `image ${selected}`}
            </span>
            <button onClick={() => setSelected(null)} className="text-xs text-th-text-muted hover:text-th-text">
              ✕
            </button>
          </div>
          <img
            src={images[selected].data_url}
            alt={images[selected].filename || ""}
            className="max-w-full max-h-[300px] rounded"
          />
        </div>
      )}
    </div>
  );
}
