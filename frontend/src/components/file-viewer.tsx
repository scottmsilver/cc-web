"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as XLSX from "xlsx";

import { getFileUrl } from "@/lib/api";

type FileViewerProps = {
  sessionId: string;
  filePath: string;
  onClose: () => void;
  hideHeader?: boolean;
};

type PdfDocument = {
  numPages: number;
  getPage: (n: number) => Promise<{
    getViewport: (o: { scale: number }) => { width: number; height: number };
    render: (o: { canvasContext: CanvasRenderingContext2D; viewport: unknown }) => { promise: Promise<void> };
  }>;
};

function DownloadLink({ sessionId, filePath, label }: { sessionId: string; filePath: string; label?: string }) {
  const url = getFileUrl(sessionId, filePath);
  return (
    <a href={url} download={filePath.split("/").pop()} className="text-th-accent hover:text-th-accent-hover text-xs underline underline-offset-2 cursor-pointer">
      {label || "Download"}
    </a>
  );
}

/* ── Thread JSON (Gmail conversation view) ── */
type ThreadMessage = {
  message_id: string;
  from: string;
  to: string;
  cc?: string;
  subject: string;
  date: string;
  body_text: string;
  body_html: string;
  attachments: string[];
  inline_images: { filename: string; cid: string }[];
};

function ThreadJsonView({ content, sessionId }: { content: string; sessionId: string }) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  let thread: { thread_id: string; messages: ThreadMessage[] };
  try {
    thread = JSON.parse(content);
  } catch {
    return <pre className="whitespace-pre-wrap p-4 text-xs font-mono text-th-text">{content}</pre>;
  }

  if (!thread.messages || thread.messages.length === 0) {
    return <p className="p-4 text-sm text-th-text-muted">No messages in thread.</p>;
  }

  return (
    <div className="p-4 space-y-2">
      <div className="text-xs text-th-text-muted mb-3">{thread.messages.length} message{thread.messages.length !== 1 ? "s" : ""} in thread</div>
      {thread.messages.map((msg, i) => {
        const isExpanded = expandedIdx === i;
        const isLast = i === thread.messages.length - 1;
        // Auto-expand the last message
        const show = isExpanded || (expandedIdx === null && isLast);
        const fromName = msg.from.replace(/<[^>]+>/, "").trim();

        return (
          <div key={i} className="border border-th-border rounded-lg overflow-hidden">
            <button
              onClick={() => setExpandedIdx(show ? (isLast ? -1 : null) : i)}
              className="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-th-surface transition-colors"
            >
              <div className="w-8 h-8 rounded-full bg-th-surface-hover flex items-center justify-center text-xs font-medium text-th-accent flex-shrink-0">
                {fromName.charAt(0).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  <span className="text-sm font-medium text-th-text truncate">{fromName}</span>
                  <span className="text-xs text-th-text-faint flex-shrink-0">{msg.date}</span>
                </div>
                {!show && (
                  <div className="text-xs text-th-text-muted truncate mt-0.5">{msg.body_text.slice(0, 120)}</div>
                )}
              </div>
              {msg.attachments.length > 0 && (
                <span className="text-xs text-th-text-muted flex-shrink-0">{"\u{1F4CE}"} {msg.attachments.length}</span>
              )}
            </button>
            {show && (
              <div className="border-t border-th-border">
                <div className="px-4 py-1.5 text-xs text-th-text-muted bg-th-surface space-y-0.5">
                  <div><span className="font-medium">To:</span> {msg.to}</div>
                  {msg.cc && <div><span className="font-medium">Cc:</span> {msg.cc}</div>}
                </div>
                {msg.body_html ? (
                  <div className="px-4 py-3">
                    <iframe
                      srcDoc={`<style>body { font-family: Roboto, 'Google Sans', Arial, sans-serif; font-size: 14px; line-height: 1.5; color: #202124; margin: 8px; }</style>${msg.body_html}`}
                      className="w-full min-h-[200px] border border-th-border rounded bg-white"
                      sandbox="allow-same-origin"
                      title={`Message ${i + 1}`}
                    />
                  </div>
                ) : (
                  <div className="px-4 py-3 whitespace-pre-wrap text-sm text-th-text leading-relaxed" style={{ fontFamily: "Roboto, 'Google Sans', Arial, sans-serif" }}>
                    {msg.body_text}
                  </div>
                )}
                {msg.attachments.length > 0 && (
                  <div className="px-4 py-2 border-t border-th-border">
                    <div className="text-xs text-th-text-muted mb-1">Attachments</div>
                    <div className="flex flex-wrap gap-2">
                      {msg.attachments.map((a, j) => (
                        <span key={j} className="inline-flex items-center gap-1 px-2 py-1 rounded bg-th-surface border border-th-border text-xs font-mono text-th-text">
                          {"\u{1F4CE}"} {a}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── Markdown ── */
function MarkdownView({ content }: { content: string }) {
  return (
    <div className="prose-chat p-4">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

/* ── Spreadsheet ── */
function SpreadsheetView({ data }: { data: ArrayBuffer }) {
  const [sheets, setSheets] = useState<{ name: string; rows: string[][] }[]>([]);
  const [activeSheet, setActiveSheet] = useState(0);

  useEffect(() => {
    try {
      const wb = XLSX.read(data, { type: "array" });
      setSheets(wb.SheetNames.map((name) => {
        const rows = XLSX.utils.sheet_to_json<string[]>(wb.Sheets[name], { header: 1 }) as string[][];
        return { name, rows };
      }));
    } catch (error) {
      console.warn("Failed to parse spreadsheet:", error);
      setSheets([]);
    }
  }, [data]);

  if (sheets.length === 0) return <p className="p-4 text-sm text-th-text-muted">Could not parse spreadsheet.</p>;
  const sheet = sheets[activeSheet];

  return (
    <div>
      {sheets.length > 1 && (
        <div className="flex gap-1 px-4 pt-3 pb-1">
          {sheets.map((s, i) => (
            <button key={s.name} onClick={() => setActiveSheet(i)}
              className={`rounded px-2.5 py-1 text-xs font-medium cursor-pointer ${i === activeSheet ? "bg-th-accent text-white" : "text-th-text-muted hover:bg-th-surface-hover border border-th-border"}`}
            >{s.name}</button>
          ))}
        </div>
      )}
      <div className="overflow-auto p-4">
        <table className="border-collapse text-xs w-full">
          <thead>{sheet.rows.length > 0 && (
            <tr>{sheet.rows[0].map((cell, j) => (
              <th key={j} className="border border-th-border bg-th-surface-hover px-3 py-1.5 text-left font-medium text-th-text whitespace-nowrap">{cell ?? ""}</th>
            ))}</tr>
          )}</thead>
          <tbody>{sheet.rows.slice(1).map((row, i) => (
            <tr key={i} className={i % 2 ? "bg-th-surface" : ""}>
              {row.map((cell, j) => <td key={j} className="border border-th-border px-3 py-1.5 text-th-text whitespace-nowrap">{cell ?? ""}</td>)}
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

/* ── PDF ── */
function PdfView({ url }: { url: string }) {
  const canvasContainerRef = useRef<HTMLDivElement>(null);
  const [pageCount, setPageCount] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const pdfDocRef = useRef<PdfDocument | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const pdfjsLib = await import("pdfjs-dist");
        pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        const pdf = await pdfjsLib.getDocument(url).promise;
        if (cancelled) return;
        pdfDocRef.current = pdf as unknown as PdfDocument;
        setPageCount(pdf.numPages);
        setCurrentPage(1);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load PDF");
      }
    })();
    return () => { cancelled = true; };
  }, [url]);

  useEffect(() => {
    if (!pdfDocRef.current || !canvasContainerRef.current) return;
    let cancelled = false;
    (async () => {
      const pdf = pdfDocRef.current!;
      const page = await pdf.getPage(currentPage);
      if (cancelled) return;
      const container = canvasContainerRef.current!;
      const containerWidth = container.clientWidth - 16;
      const unscaledViewport = page.getViewport({ scale: 1 });
      const scale = containerWidth / unscaledViewport.width;
      const viewport = page.getViewport({ scale });

      let canvas = container.querySelector("canvas");
      if (!canvas) { canvas = document.createElement("canvas"); container.appendChild(canvas); }
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = "100%";
      canvas.style.height = "auto";
      const ctx = canvas.getContext("2d")!;
      await page.render({ canvasContext: ctx, viewport }).promise;
    })();
    return () => { cancelled = true; };
  }, [currentPage, pageCount]);

  if (error) return <p className="p-4 text-sm text-red-600">{error}</p>;
  if (pageCount === 0) return <p className="p-4 text-sm text-th-text-muted">Loading PDF...</p>;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-1.5 border-b border-th-border flex-shrink-0">
        <button disabled={currentPage <= 1} onClick={() => setCurrentPage(p => p - 1)} className="px-2 py-0.5 rounded border border-th-border text-xs disabled:opacity-30 cursor-pointer hover:bg-th-surface">\u2190</button>
        <span className="text-xs text-th-text-muted">{currentPage} / {pageCount}</span>
        <button disabled={currentPage >= pageCount} onClick={() => setCurrentPage(p => p + 1)} className="px-2 py-0.5 rounded border border-th-border text-xs disabled:opacity-30 cursor-pointer hover:bg-th-surface">\u2192</button>
      </div>
      <div ref={canvasContainerRef} className="flex-1 overflow-auto p-2" />
    </div>
  );
}

/* ── Zip ── */
function ZipView({ data }: { data: ArrayBuffer }) {
  const [files, setFiles] = useState<{ name: string; size: number; compressed: number }[]>([]);

  useEffect(() => {
    (async () => {
      try {
        const view = new DataView(data);
        const entries: { name: string; size: number; compressed: number }[] = [];

        // Find End of Central Directory
        let eocdOffset = -1;
        for (let i = data.byteLength - 22; i >= 0; i--) {
          if (view.getUint32(i, true) === 0x06054b50) { eocdOffset = i; break; }
        }
        if (eocdOffset < 0) { setFiles([]); return; }

        const cdOffset = view.getUint32(eocdOffset + 16, true);
        const cdEntries = view.getUint16(eocdOffset + 10, true);
        let offset = cdOffset;

        for (let i = 0; i < cdEntries && offset < data.byteLength; i++) {
          if (view.getUint32(offset, true) !== 0x02014b50) break;
          const compressed = view.getUint32(offset + 20, true);
          const size = view.getUint32(offset + 24, true);
          const nameLen = view.getUint16(offset + 28, true);
          const extraLen = view.getUint16(offset + 30, true);
          const commentLen = view.getUint16(offset + 32, true);
          const name = new TextDecoder().decode(new Uint8Array(data, offset + 46, nameLen));
          entries.push({ name, size, compressed });
          offset += 46 + nameLen + extraLen + commentLen;
        }
        setFiles(entries);
      } catch (error) {
        console.warn("Failed to parse zip:", error);
        setFiles([]);
      }
    })();
  }, [data]);

  if (files.length === 0) return <p className="p-4 text-sm text-th-text-muted">Could not read zip contents.</p>;

  const totalSize = files.reduce((s, f) => s + f.size, 0);
  const fmt = (n: number) => n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : n > 1024 ? `${(n / 1024).toFixed(0)}KB` : `${n}B`;

  return (
    <div className="p-4">
      <p className="text-xs text-th-text-muted mb-2">{files.length} files, {fmt(totalSize)} total</p>
      <table className="border-collapse text-xs w-full">
        <thead>
          <tr>
            <th className="border border-th-border bg-th-surface-hover px-3 py-1.5 text-left font-medium text-th-text">Name</th>
            <th className="border border-th-border bg-th-surface-hover px-3 py-1.5 text-right font-medium text-th-text w-20">Size</th>
          </tr>
        </thead>
        <tbody>
          {files.filter(f => !f.name.endsWith("/")).map((f, i) => (
            <tr key={i} className={i % 2 ? "bg-th-surface" : ""}>
              <td className="border border-th-border px-3 py-1 text-th-text font-mono">{f.name}</td>
              <td className="border border-th-border px-3 py-1 text-th-text-muted text-right">{fmt(f.size)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ── EML (RFC 2822 email) ── */
type EmlPart = {
  headers: Record<string, string>;
  contentType: string;
  filename?: string;
  body: string;       // decoded text for text/* parts
  rawBody: string;    // raw body (base64 or quoted-printable encoded)
  parts: EmlPart[];   // child parts for multipart/*
};

function parseEml(raw: string): EmlPart {
  return parseOnePart(raw);
}

function parseOnePart(raw: string): EmlPart {
  // Split headers from body at first blank line
  const headerEnd = raw.indexOf("\r\n\r\n");
  const altEnd = raw.indexOf("\n\n");
  let splitIdx: number;
  let sep: string;
  if (headerEnd >= 0 && (altEnd < 0 || headerEnd <= altEnd)) {
    splitIdx = headerEnd; sep = "\r\n\r\n";
  } else if (altEnd >= 0) {
    splitIdx = altEnd; sep = "\n\n";
  } else {
    splitIdx = raw.length; sep = "";
  }

  const headerBlock = raw.slice(0, splitIdx);
  const bodyRaw = sep ? raw.slice(splitIdx + sep.length) : "";

  // Parse headers (unfold continuation lines)
  const headers: Record<string, string> = {};
  const unfolded = headerBlock.replace(/\r?\n[ \t]+/g, " ");
  for (const line of unfolded.split(/\r?\n/)) {
    const colon = line.indexOf(":");
    if (colon > 0) {
      const key = line.slice(0, colon).trim().toLowerCase();
      const val = line.slice(colon + 1).trim();
      headers[key] = val;
    }
  }

  const ct = headers["content-type"] || "text/plain";
  const cte = (headers["content-transfer-encoding"] || "").toLowerCase();

  // Multipart: split on boundary
  const boundaryMatch = ct.match(/boundary="?([^";\s]+)"?/i);
  if (boundaryMatch) {
    const boundary = boundaryMatch[1];
    const parts: EmlPart[] = [];
    const segments = bodyRaw.split("--" + boundary);
    for (let i = 1; i < segments.length; i++) {
      const seg = segments[i];
      if (seg.startsWith("--")) break; // closing boundary
      parts.push(parseOnePart(seg.replace(/^\r?\n/, "")));
    }
    return { headers, contentType: ct, body: "", rawBody: bodyRaw, parts };
  }

  // Leaf part: decode body
  // For text/* parts, decode using the charset from Content-Type (default UTF-8).
  const isTextType = ct.split(";")[0].trim().toLowerCase().startsWith("text/");
  const charsetMatch = ct.match(/charset="?([^";\s]+)"?/i);
  const charset = charsetMatch ? charsetMatch[1].toLowerCase() : "utf-8";
  let decoded = bodyRaw;
  if (cte === "base64") {
    try {
      const binaryStr = atob(bodyRaw.replace(/\s/g, ""));
      if (isTextType) {
        // Decode binary string as UTF-8
        const bytes = Uint8Array.from(binaryStr, c => c.charCodeAt(0));
        decoded = new TextDecoder(charset, { fatal: false }).decode(bytes);
      } else {
        decoded = binaryStr;
      }
    } catch { decoded = bodyRaw; }
  } else if (cte === "quoted-printable") {
    // Decode QP to bytes, then interpret using charset
    const qpDecoded = bodyRaw
      .replace(/=\r?\n/g, "")
      .replace(/=([0-9A-Fa-f]{2})/g, (_, hex) => String.fromCharCode(parseInt(hex, 16)));
    if (isTextType) {
      const bytes = Uint8Array.from(qpDecoded, c => c.charCodeAt(0));
      decoded = new TextDecoder(charset, { fatal: false }).decode(bytes);
    } else {
      decoded = qpDecoded;
    }
  }

  // Extract filename from Content-Disposition or Content-Type
  let filename: string | undefined;
  const cd = headers["content-disposition"] || "";
  const fnMatch = cd.match(/filename="?([^";\n]+)"?/i) || ct.match(/name="?([^";\n]+)"?/i);
  if (fnMatch) filename = fnMatch[1].trim();

  return { headers, contentType: ct.split(";")[0].trim().toLowerCase(), body: decoded, rawBody: bodyRaw, parts: [], filename };
}

function EmlView({ content }: { content: string }) {
  const eml = parseEml(content);
  const [activeTab, setActiveTab] = useState<"headers" | "text" | "html" | "parts">("text");

  // Collect all parts flat
  const allParts: EmlPart[] = [];
  function collectParts(part: EmlPart) {
    if (part.parts.length > 0) part.parts.forEach(collectParts);
    else allParts.push(part);
  }
  collectParts(eml);

  const textPart = allParts.find(p => p.contentType === "text/plain");
  const htmlPart = allParts.find(p => p.contentType === "text/html");
  const attachments = allParts.filter(p => !p.contentType.startsWith("text/") || p.filename);
  const inlineImages = allParts.filter(p => p.contentType.startsWith("image/"));

  const from = eml.headers["from"] || "";
  const to = eml.headers["to"] || "";
  const subject = eml.headers["subject"] || "(no subject)";
  const date = eml.headers["date"] || "";

  const tabs: { key: typeof activeTab; label: string; show: boolean }[] = [
    { key: "text", label: "Text", show: !!textPart },
    { key: "html", label: "HTML", show: !!htmlPart },
    { key: "parts", label: `Parts (${allParts.length})`, show: allParts.length > 1 },
    { key: "headers", label: "Headers", show: true },
  ];

  // Auto-select first available tab
  useEffect(() => {
    if (activeTab === "text" && !textPart && htmlPart) setActiveTab("html");
  }, [activeTab, textPart, htmlPart]);

  return (
    <div className="flex flex-col h-full">
      {/* Email header summary */}
      <div className="px-4 py-3 border-b border-th-border space-y-1">
        <div className="text-sm font-medium text-th-text">{subject}</div>
        <div className="text-xs text-th-text-muted">
          <span className="font-medium">From:</span> {from}
        </div>
        <div className="text-xs text-th-text-muted">
          <span className="font-medium">To:</span> {to}
        </div>
        {date && <div className="text-xs text-th-text-faint">{date}</div>}
        {attachments.length > 0 && (
          <div className="text-xs text-th-text-muted">
            {attachments.length} attachment{attachments.length !== 1 ? "s" : ""}: {attachments.map(a => a.filename || a.contentType).join(", ")}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-0 border-b border-th-border flex-shrink-0">
        {tabs.filter(t => t.show).map(t => (
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
      <div className="flex-1 overflow-auto">
        {activeTab === "text" && textPart && (
          <div className="whitespace-pre-wrap p-4 text-sm text-th-text leading-relaxed" style={{ fontFamily: "Roboto, 'Google Sans', Arial, sans-serif" }}>{textPart.body}</div>
        )}

        {activeTab === "html" && htmlPart && (
          <div className="p-4">
            <iframe
              srcDoc={`<style>body { font-family: Roboto, 'Google Sans', Arial, sans-serif; font-size: 14px; line-height: 1.5; color: #202124; margin: 8px; }</style>${htmlPart.body}`}
              className="w-full min-h-[400px] border border-th-border rounded bg-white"
              sandbox="allow-same-origin"
              title="Email HTML"
            />
          </div>
        )}

        {activeTab === "parts" && (
          <div className="p-4 space-y-2">
            {allParts.map((part, i) => (
              <PartRow key={i} part={part} index={i} />
            ))}
          </div>
        )}

        {activeTab === "headers" && (
          <div className="p-4">
            <table className="text-xs w-full border-collapse">
              <tbody>
                {Object.entries(eml.headers).map(([key, val]) => (
                  <tr key={key} className="border-b border-th-border">
                    <td className="py-1.5 pr-3 font-medium text-th-text-muted whitespace-nowrap align-top">{key}</td>
                    <td className="py-1.5 text-th-text break-all">{val}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Inline images at bottom of text/html views */}
        {(activeTab === "text" || activeTab === "html") && inlineImages.length > 0 && (
          <div className="px-4 pb-4">
            <div className="text-xs font-medium text-th-text-muted mb-2">Inline Images</div>
            <div className="flex flex-wrap gap-2">
              {inlineImages.map((img, i) => {
                const dataUrl = `data:${img.contentType};base64,${btoa(img.body)}`;
                return (
                  <img key={i} src={dataUrl} alt={img.filename || `image ${i}`}
                    className="max-h-32 rounded border border-th-border"
                    title={img.filename || img.contentType}
                  />
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function PartRow({ part, index }: { part: EmlPart; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const isImage = part.contentType.startsWith("image/");
  const isText = part.contentType.startsWith("text/");
  const size = part.rawBody.length;
  const fmt = (n: number) => n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : n > 1024 ? `${(n / 1024).toFixed(0)}KB` : `${n}B`;

  return (
    <div className="border border-th-border rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-th-surface transition-colors"
      >
        <span className="text-xs text-th-text-faint">{index + 1}</span>
        <span className="text-xs font-mono text-th-text flex-1">{part.filename || part.contentType}</span>
        <span className="text-xs text-th-text-muted">{fmt(size)}</span>
        <span className="text-xs text-th-text-faint">{expanded ? "\u25B2" : "\u25BC"}</span>
      </button>
      {expanded && (
        <div className="border-t border-th-border">
          {/* Part headers */}
          <div className="px-3 py-1.5 bg-th-surface">
            {Object.entries(part.headers).map(([k, v]) => (
              <div key={k} className="text-[11px] text-th-text-muted"><span className="font-medium">{k}:</span> {v}</div>
            ))}
          </div>
          {/* Part body preview */}
          <div className="max-h-64 overflow-auto">
            {isImage ? (
              <div className="p-3">
                <img src={`data:${part.contentType};base64,${btoa(part.body)}`} alt={part.filename || ""} className="max-w-full rounded" />
              </div>
            ) : isText ? (
              <pre className="p-3 text-xs font-mono text-th-text whitespace-pre-wrap">{part.body.slice(0, 5000)}</pre>
            ) : (
              <div className="p-3 text-xs text-th-text-muted">Binary content ({part.contentType}, {fmt(size)})</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Main viewer ── */
export function FileViewer({ sessionId, filePath, onClose, hideHeader }: FileViewerProps) {
  const [content, setContent] = useState<string | null>(null);
  const [binaryData, setBinaryData] = useState<ArrayBuffer | null>(null);
  const [loading, setLoading] = useState(true);

  const fileName = filePath.split("/").pop() || filePath;
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  const isMd = ext === "md";
  const isSpreadsheet = ext === "xlsx" || ext === "xls";
  const isPdf = ext === "pdf";
  const isZip = ext === "zip";
  const isEml = ext === "eml";
  const isThreadJson = fileName === "thread.json";
  const isImage = /^(png|jpg|jpeg|gif|webp)$/.test(ext);
  const needsBinary = isSpreadsheet || isZip;
  const needsText = !needsBinary && !isPdf && !isImage;
  const fileUrl = getFileUrl(sessionId, filePath);

  useEffect(() => {
    setLoading(true);
    setContent(null);
    setBinaryData(null);

    if (needsBinary) {
      fetch(fileUrl).then(r => r.arrayBuffer()).then(buf => { setBinaryData(buf); setLoading(false); }).catch((error) => { console.warn("Failed to load binary file:", error); setLoading(false); });
    } else if (needsText) {
      // EML files can be large (base64 attachments) — allow up to 10MB, others 100KB
      const maxSize = isEml ? 10 * 1024 * 1024 : 100000;
      fetch(fileUrl).then(r => r.text()).then(t => { setContent(t.substring(0, maxSize)); setLoading(false); }).catch((error) => { console.warn("Failed to load text file:", error); setLoading(false); });
    } else {
      setLoading(false);
    }
  }, [sessionId, filePath, needsBinary, needsText, fileUrl]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {!hideHeader && (
        <div className="flex items-center gap-3 px-4 py-2 border-b border-th-border">
          <button onClick={onClose} className="text-th-text-muted hover:text-th-text text-sm cursor-pointer">\u2190 Back</button>
          <h3 className="text-sm font-medium text-th-text truncate flex-1">{fileName}</h3>
          <DownloadLink sessionId={sessionId} filePath={filePath} />
        </div>
      )}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <p className="p-4 text-sm text-th-text-muted">Loading...</p>
        ) : isPdf ? (
          <PdfView url={fileUrl} />
        ) : isZip && binaryData ? (
          <ZipView data={binaryData} />
        ) : isSpreadsheet && binaryData ? (
          <SpreadsheetView data={binaryData} />
        ) : isImage ? (
          <div className="p-4"><img src={fileUrl} alt={fileName} className="max-w-full rounded border border-th-border" /></div>
        ) : isEml && content !== null ? (
          <EmlView content={content} />
        ) : isThreadJson && content !== null ? (
          <ThreadJsonView content={content} sessionId={sessionId} />
        ) : isMd && content !== null ? (
          <MarkdownView content={content} />
        ) : content !== null ? (
          <pre className="whitespace-pre-wrap p-4 text-xs font-mono text-th-text">{content}</pre>
        ) : (
          <div className="p-4 text-center text-sm text-th-text-muted">
            <p className="mb-2">Cannot preview .{ext} files</p>
            <DownloadLink sessionId={sessionId} filePath={filePath} label={`Download ${fileName}`} />
          </div>
        )}
      </div>
    </div>
  );
}
