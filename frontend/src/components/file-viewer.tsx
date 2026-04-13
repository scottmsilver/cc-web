"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as XLSX from "xlsx";

import { getFileUrl } from "@/lib/api";
import { EmlViewer } from "@/components/eml-viewer";

type FileViewerProps = {
  sessionId: string;
  filePath: string;
  onClose: () => void;
  hideHeader?: boolean;
  onNavigate?: (path: string) => void;
  pdfPage?: number;
  onPdfPageChange?: (page: number) => void;
  onPdfPageCountChange?: (count: number) => void;
  pdfSidebarOpen?: boolean;
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

/** Sidebar toggle button for PDF thumbnail panel. */
export function PdfSidebarToggle({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className="w-7 h-7 flex items-center justify-center rounded border border-th-border text-th-text-muted hover:text-th-accent hover:border-th-accent/50 hover:bg-th-surface-hover transition-colors cursor-pointer"
      title={open ? "Hide thumbnails" : "Show thumbnails"}
    >
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <rect x="3" y="3" width="6" height="18" rx="1" />
        <line x1="13" y1="6" x2="21" y2="6" />
        <line x1="13" y1="12" x2="21" y2="12" />
        <line x1="13" y1="18" x2="21" y2="18" />
      </svg>
    </button>
  );
}

/** Compact PDF page navigation for use in toolbar headers. */
export function PdfPageNav({ page, pageCount, onPageChange }: { page: number; pageCount: number; onPageChange: (p: number) => void }) {
  if (pageCount <= 1) return null;
  const navBtn = "w-7 h-7 flex items-center justify-center rounded border border-th-border text-th-text-muted hover:text-th-accent hover:border-th-accent/50 hover:bg-th-surface-hover transition-colors cursor-pointer disabled:opacity-25 disabled:cursor-default";
  return (
    <div className="flex items-center gap-1">
      <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)} className={navBtn} title="Previous page">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="15 18 9 12 15 6" /></svg>
      </button>
      <span className="text-xs text-th-text-muted tabular-nums min-w-[2.5rem] text-center">{page}/{pageCount}</span>
      <button type="button" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)} className={navBtn} title="Next page">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="9 18 15 12 9 6" /></svg>
      </button>
    </div>
  );
}

/* ── PDF: Page slot (lazy-rendered single page) ── */
type PageViewport = { width: number; height: number };

function PdfPageSlot({
  pageNum,
  pdfDoc,
  containerWidth,
  vp,
  visible,
}: {
  pageNum: number;
  pdfDoc: PdfDocument;
  containerWidth: number;
  vp: PageViewport;
  visible: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderedForWidth = useRef(0);

  const scale = Math.max(containerWidth - 16, 100) / vp.width;
  const height = vp.height * scale;

  // Render when visible and not yet rendered at this width
  useEffect(() => {
    if (!visible || renderedForWidth.current === containerWidth) return;
    const canvas = canvasRef.current;
    if (!canvas) return;

    let cancelled = false;
    (async () => {
      const page = await pdfDoc.getPage(pageNum);
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = "100%";
      canvas.style.height = "auto";
      const ctx = canvas.getContext("2d")!;
      await page.render({ canvasContext: ctx, viewport }).promise;
      if (!cancelled) renderedForWidth.current = containerWidth;
    })();
    return () => { cancelled = true; };
  }, [visible, containerWidth, pdfDoc, pageNum, scale]);

  return (
    <div
      data-page={pageNum}
      className="relative flex-shrink-0 bg-white rounded shadow-sm"
      style={{ height, width: "100%" }}
    >
      <canvas ref={canvasRef} className="absolute inset-0" />
      <span className="absolute bottom-1 right-2 text-[10px] text-gray-400 select-none">{pageNum}</span>
    </div>
  );
}

/* ── PDF: Thumbnail sidebar ── */
function PdfThumbnailSidebar({
  pdfDoc,
  pageCount,
  viewports,
  currentPage,
  onPageClick,
}: {
  pdfDoc: PdfDocument;
  pageCount: number;
  viewports: PageViewport[];
  currentPage: number;
  onPageClick: (page: number) => void;
}) {
  const thumbsRef = useRef<Map<number, HTMLCanvasElement>>(new Map());
  const containerRef = useRef<HTMLDivElement>(null);
  const [rendered, setRendered] = useState(0);

  // Render thumbnails progressively
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (let i = 1; i <= pageCount; i++) {
        if (cancelled) break;
        const page = await pdfDoc.getPage(i);
        if (cancelled) break;
        const vp = viewports[i - 1];
        if (!vp) continue;
        const thumbScale = 80 / vp.width;
        const viewport = page.getViewport({ scale: thumbScale });
        const canvas = document.createElement("canvas");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const ctx = canvas.getContext("2d")!;
        await page.render({ canvasContext: ctx, viewport }).promise;
        if (cancelled) break;
        thumbsRef.current.set(i, canvas);
        setRendered(i);
        // Yield between pages
        await new Promise((r) => setTimeout(r, 0));
      }
    })();
    return () => { cancelled = true; };
  }, [pdfDoc, pageCount, viewports]);

  // Auto-scroll to keep active thumbnail visible
  useEffect(() => {
    const el = containerRef.current?.querySelector(`[data-thumb="${currentPage}"]`);
    el?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [currentPage]);

  return (
    <div ref={containerRef} className="w-[100px] overflow-y-auto flex-shrink-0 py-2 px-1.5 space-y-1.5 bg-th-surface">
      {Array.from({ length: pageCount }, (_, i) => {
        const p = i + 1;
        const vp = viewports[i];
        const thumbH = vp ? (80 / vp.width) * vp.height : 100;
        const isActive = p === currentPage;
        return (
          <button
            key={p}
            type="button"
            data-thumb={p}
            onClick={() => onPageClick(p)}
            className={`block w-full rounded overflow-hidden border-2 transition-colors ${
              isActive ? "border-th-accent" : "border-transparent hover:border-th-border"
            }`}
            title={`Page ${p}`}
          >
            {rendered >= p ? (
              <canvas
                className="w-full"
                style={{ height: thumbH }}
                ref={(el) => {
                  if (el && thumbsRef.current.has(p)) {
                    const src = thumbsRef.current.get(p)!;
                    el.width = src.width;
                    el.height = src.height;
                    el.getContext("2d")!.drawImage(src, 0, 0);
                  }
                }}
              />
            ) : (
              <div className="bg-th-surface-hover flex items-center justify-center text-[10px] text-th-text-faint" style={{ height: thumbH }}>
                {p}
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ── PDF: Main viewer (orchestrator) ── */
function PdfView({ url, page, onPageChange, onPageCountChange, sidebarOpen }: { url: string; page?: number; onPageChange?: (page: number) => void; onPageCountChange?: (count: number) => void; sidebarOpen?: boolean }) {
  const [pdfDoc, setPdfDoc] = useState<PdfDocument | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [viewports, setViewports] = useState<PageViewport[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const showSidebar = sidebarOpen ?? false;
  const [containerWidth, setContainerWidth] = useState(600);
  const [error, setError] = useState<string | null>(null);
  const [visiblePages, setVisiblePages] = useState<Set<number>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);
  const programmaticScroll = useRef(false);

  // Load PDF and collect all page viewports
  useEffect(() => {
    let cancelled = false;
    setError(null);
    setPdfDoc(null);
    setPageCount(0);
    setViewports([]);
    setCurrentPage(1);
    (async () => {
      try {
        const pdfjsLib = await import("pdfjs-dist");
        pdfjsLib.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        const pdf = await pdfjsLib.getDocument(url).promise;
        if (cancelled) return;
        const doc = pdf as unknown as PdfDocument;
        const vps: PageViewport[] = [];
        for (let i = 1; i <= doc.numPages; i++) {
          const p = await doc.getPage(i);
          const v = p.getViewport({ scale: 1 });
          vps.push({ width: v.width, height: v.height });
        }
        if (cancelled) return;
        setPdfDoc(doc);
        setPageCount(doc.numPages);
        setViewports(vps);
        onPageCountChange?.(doc.numPages);
        onPageChange?.(1);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load PDF");
      }
    })();
    return () => { cancelled = true; };
  }, [url]); // eslint-disable-line react-hooks/exhaustive-deps

  // Observe page slots for lazy rendering
  useEffect(() => {
    const container = scrollRef.current;
    if (!container || pageCount === 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        setVisiblePages((prev) => {
          const next = new Set(prev);
          for (const entry of entries) {
            const p = parseInt((entry.target as HTMLElement).dataset.page || "0", 10);
            if (p > 0) {
              if (entry.isIntersecting) next.add(p);
              // Don't remove — once visible, stay rendered (keep canvas alive)
            }
          }
          return next.size !== prev.size ? next : prev;
        });
      },
      { root: container, rootMargin: "200% 0px" },
    );
    // Observe all page slots after they mount
    requestAnimationFrame(() => {
      container.querySelectorAll<HTMLElement>("[data-page]").forEach((el) => obs.observe(el));
    });
    return () => obs.disconnect();
  }, [pageCount]);

  // Track container width for responsive scaling
  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const ro = new ResizeObserver(([entry]) => {
      setContainerWidth(entry.contentRect.width);
    });
    ro.observe(container);
    return () => ro.disconnect();
  }, []);

  // Detect current page from scroll position
  useEffect(() => {
    const container = scrollRef.current;
    if (!container || pageCount === 0) return;
    const onScroll = () => {
      if (programmaticScroll.current) return;
      const slots = container.querySelectorAll<HTMLElement>("[data-page]");
      const containerTop = container.scrollTop;
      const threshold = container.clientHeight * 0.3;
      let best = 1;
      for (const slot of slots) {
        const slotTop = slot.offsetTop - container.offsetTop;
        if (slotTop <= containerTop + threshold) {
          best = parseInt(slot.dataset.page || "1", 10);
        }
      }
      if (best !== currentPage) {
        setCurrentPage(best);
        onPageChange?.(best);
      }
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => container.removeEventListener("scroll", onScroll);
  }, [pageCount, currentPage, onPageChange]);

  // Scroll to page when parent changes the page prop
  useEffect(() => {
    if (!page || page === currentPage || !scrollRef.current) return;
    scrollToPage(page);
  }, [page]); // eslint-disable-line react-hooks/exhaustive-deps

  const scrollToPage = (p: number) => {
    const container = scrollRef.current;
    if (!container) return;
    const slot = container.querySelector<HTMLElement>(`[data-page="${p}"]`);
    if (!slot) return;
    programmaticScroll.current = true;
    container.scrollTo({ top: slot.offsetTop - container.offsetTop, behavior: "smooth" });
    setCurrentPage(p);
    onPageChange?.(p);
    setTimeout(() => { programmaticScroll.current = false; }, 500);
  };

  if (error) return <p className="p-4 text-sm text-red-600">{error}</p>;
  if (!pdfDoc || pageCount === 0) return <p className="p-4 text-sm text-th-text-muted">Loading PDF...</p>;

  return (
    <div className="flex h-full">
      {/* Thumbnail sidebar */}
      {showSidebar && pdfDoc && (
        <>
          <PdfThumbnailSidebar
            pdfDoc={pdfDoc}
            pageCount={pageCount}
            viewports={viewports}
            currentPage={currentPage}
            onPageClick={scrollToPage}
          />
          <div className="w-px bg-th-border flex-shrink-0" />
        </>
      )}

      {/* Scroll viewport with all pages */}
      <div ref={scrollRef} className="flex-1 overflow-auto p-2 space-y-2">
        {viewports.map((vp, i) => (
          <PdfPageSlot
            key={i + 1}
            pageNum={i + 1}
            pdfDoc={pdfDoc}
            containerWidth={containerWidth}
            vp={vp}
            visible={visiblePages.has(i + 1)}
          />
        ))}
      </div>
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

/* ── Directory listing with breadcrumb navigation ── */
function DirView({ entries, dirPath, onNavigate }: {
  entries: { name: string; path: string; is_dir: boolean }[];
  dirPath: string;
  onNavigate: (path: string) => void;
}) {
  const fileIcon = (ext: string) => {
    if (/^(pdf)$/i.test(ext)) return "\u{1F4C4}";
    if (/^(xlsx?|csv)$/i.test(ext)) return "\u{1F4CA}";
    if (/^(png|jpg|jpeg|gif|webp)$/i.test(ext)) return "\u{1F5BC}";
    if (/^(eml)$/i.test(ext)) return "\u{2709}";
    if (/^(json|txt|md)$/i.test(ext)) return "\u{1F4DD}";
    if (/^(zip)$/i.test(ext)) return "\u{1F4E6}";
    return "\u{1F4CE}";
  };

  // Build breadcrumb segments from the path
  const segments = dirPath.replace(/\/$/, "").split("/").filter(Boolean);
  const crumbs: { label: string; path: string }[] = [
    { label: "\u{1F3E0}", path: "/" },
  ];
  for (let i = 0; i < segments.length; i++) {
    crumbs.push({
      label: segments[i],
      path: segments.slice(0, i + 1).join("/") + "/",
    });
  }

  // Sort: directories first, then files
  const sorted = [...entries].sort((a, b) => {
    if (a.is_dir && !b.is_dir) return -1;
    if (!a.is_dir && b.is_dir) return 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <div className="p-4">
      {/* Breadcrumb */}
      <div className="flex items-center gap-1 text-xs mb-3 flex-wrap">
        {crumbs.map((crumb, i) => (
          <span key={crumb.path} className="flex items-center gap-1">
            {i > 0 && <span className="text-th-text-faint">/</span>}
            <button
              onClick={() => onNavigate(crumb.path)}
              className="text-th-accent hover:text-th-accent-hover hover:underline underline-offset-2"
            >
              {crumb.label}
            </button>
          </span>
        ))}
      </div>

      <div className="space-y-0.5">
        {sorted.map((entry) => {
          const ext = entry.name.split(".").pop()?.toLowerCase() || "";
          return (
            <button
              key={entry.path}
              onClick={() => onNavigate(entry.is_dir ? entry.path + "/" : entry.path)}
              className="w-full flex items-center gap-3 px-3 py-2 rounded-lg hover:bg-th-surface transition-colors text-left"
              title={entry.path}
            >
              <span className="text-base flex-shrink-0">
                {entry.is_dir ? "\u{1F4C1}" : fileIcon(ext)}
              </span>
              <span className="text-sm text-th-text font-mono truncate flex-1">{entry.name}</span>
              {entry.is_dir && <span className="text-xs text-th-text-faint">{"\u203A"}</span>}
            </button>
          );
        })}
        {sorted.length === 0 && (
          <p className="text-xs text-th-text-muted py-4 text-center">Empty folder</p>
        )}
      </div>
    </div>
  );
}

/* ── Main viewer ── */
export function FileViewer({ sessionId, filePath, onClose, hideHeader, onNavigate, pdfPage, onPdfPageChange, onPdfPageCountChange, pdfSidebarOpen }: FileViewerProps) {
  const [content, setContent] = useState<string | null>(null);
  const [binaryData, setBinaryData] = useState<ArrayBuffer | null>(null);
  const [dirEntries, setDirEntries] = useState<{ name: string; path: string; is_dir: boolean }[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  const isDir = filePath.endsWith("/");
  const fileName = filePath.split("/").filter(Boolean).pop() || filePath;
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  const isMd = !isDir && ext === "md";
  const isSpreadsheet = !isDir && (ext === "xlsx" || ext === "xls");
  const isPdf = !isDir && ext === "pdf";
  const isZip = !isDir && ext === "zip";
  const isEml = !isDir && ext === "eml";
  const isThreadJson = !isDir && fileName === "thread.json";
  const isImage = !isDir && /^(png|jpg|jpeg|gif|webp)$/.test(ext);
  const needsBinary = isSpreadsheet || isZip;
  const needsText = !isDir && !needsBinary && !isPdf && !isImage && !isEml;
  const fileUrl = getFileUrl(sessionId, filePath);

  useEffect(() => {
    setLoading(true);
    setContent(null);
    setBinaryData(null);
    setDirEntries(null);
    setStale(false);

    if (isDir) {
      fetch(fileUrl).then(r => r.json()).then(d => { setDirEntries(d.entries || []); setLoading(false); }).catch(() => { setDirEntries([]); setLoading(false); });
    } else if (needsBinary) {
      fetch(fileUrl).then(r => r.arrayBuffer()).then(buf => { setBinaryData(buf); setLoading(false); }).catch((error) => { console.warn("Failed to load binary file:", error); setLoading(false); });
    } else if (needsText) {
      fetch(fileUrl).then(r => r.text()).then(t => { setContent(t.substring(0, 100000)); setLoading(false); }).catch((error) => { console.warn("Failed to load text file:", error); setLoading(false); });
    } else {
      setLoading(false);
    }
  }, [sessionId, filePath, isDir, needsBinary, needsText, fileUrl, refreshKey]);

  // Poll for file changes via lightweight mtime endpoint
  useEffect(() => {
    if (isDir || isEml || loading) return;
    const mtimeUrl = fileUrl.replace("/files/", "/file-mtime/");
    // Capture initial mtime
    let initialMtime: number | null = null;
    fetch(mtimeUrl).then(r => r.json()).then(d => { initialMtime = d.mtime; }).catch(() => {});

    const interval = setInterval(async () => {
      if (initialMtime === null) return;
      try {
        const r = await fetch(mtimeUrl);
        const d = await r.json();
        if (d.mtime && d.mtime !== initialMtime) {
          setStale(true);
        }
      } catch { /* ignore */ }
    }, 5000);
    return () => clearInterval(interval);
  }, [fileUrl, isDir, isEml, loading, refreshKey]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden relative">
      {!hideHeader && !isEml && (
        <div className="flex items-center gap-3 px-4 py-2 border-b border-th-border">
          <button onClick={onClose} className="text-th-text-muted hover:text-th-text text-sm cursor-pointer">{"\u2190"} Back</button>
          <h3 className="text-sm font-medium text-th-text truncate flex-1">{isDir ? filePath : fileName}</h3>
          {!isDir && <DownloadLink sessionId={sessionId} filePath={filePath} />}
        </div>
      )}
      {/* File updated toast — bottom-right, Gmail style */}
      {stale && (
        <button
          onClick={() => { setStale(false); setRefreshKey((k) => k + 1); }}
          className="absolute bottom-4 right-4 z-20 flex items-center gap-2 px-4 py-2.5 rounded-lg bg-th-bg border border-th-accent/40 shadow-lg text-xs text-th-accent hover:bg-th-surface transition-all animate-in fade-in slide-in-from-bottom-2"
        >
          <span className="w-2 h-2 rounded-full bg-th-accent animate-pulse" />
          <span>File updated</span>
          <span className="font-medium underline underline-offset-2">Refresh</span>
        </button>
      )}
      {/* Action buttons handled by parent (artifacts-pane header or files-tab header) */}
      <div className="flex-1 flex flex-col min-h-0">
        {/* Fill-height viewers (manage their own layout) */}
        {loading ? (
          <p className="p-4 text-sm text-th-text-muted">Loading...</p>
        ) : isPdf ? (
          <PdfView url={fileUrl} page={pdfPage} onPageChange={onPdfPageChange} onPageCountChange={onPdfPageCountChange} sidebarOpen={pdfSidebarOpen} />
        ) : isEml ? (
          <EmlViewer sessionId={sessionId} filePath={filePath} onClose={hideHeader ? undefined : onClose} />
        ) : (
          /* Scrollable viewers */
          <div className="flex-1 overflow-auto">
            {isDir && dirEntries !== null ? (
              <DirView entries={dirEntries} dirPath={filePath} onNavigate={onNavigate || onClose} />
            ) : isZip && binaryData ? (
              <ZipView data={binaryData} />
            ) : isSpreadsheet && binaryData ? (
              <SpreadsheetView data={binaryData} />
            ) : isImage ? (
              <div className="p-4"><img src={fileUrl} alt={fileName} className="max-w-full rounded border border-th-border" /></div>
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
        )}
      </div>
    </div>
  );
}
