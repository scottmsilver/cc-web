"use client";

import { useEffect, useState } from "react";

import {
  fetchGmailStatus,
  fetchGmailThreadPreview,
  getGmailAuthUrl,
  scanGmail,
  searchGmailSemantic,
  type GmailThread,
  type GmailThreadPreview,
} from "@/lib/api";
import { formatEmailDate } from "@/lib/format-date";

export type SelectedThread = {
  id: string;
  subject: string;
  attachmentCount: number;
};

export type SuggestedSearch = {
  label: string;
  query: string;
};

type GmailPickerProps = {
  sessionId: string | null;
  sessionFiles?: string[];
  suggestions?: SuggestedSearch[];
  suggestionsLoading?: boolean;
  onSelect: (threads: SelectedThread[]) => void;
  onClose: () => void;
  ensureSession: () => Promise<string>;
  onGmailConnected?: () => void;
};

export function GmailPicker({
  sessionId,
  onSelect,
  onClose,
  ensureSession,
  onGmailConnected,
  sessionFiles,
  suggestions,
  suggestionsLoading,
}: GmailPickerProps) {
  const [connected, setConnected] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const [threads, setThreads] = useState<GmailThread[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [pollingAuth, setPollingAuth] = useState(false);

  // Check Gmail connection status on mount
  useEffect(() => {
    let cancelled = false;
    async function checkStatus() {
      try {
        const status = await fetchGmailStatus();
        if (cancelled) return;
        setConnected(status.connected);
        setEmail(status.email ?? null);
      } catch {
        if (!cancelled) setConnected(false);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void checkStatus();
    return () => { cancelled = true; };
  }, []);

  // Check for ?gmail_connected=true URL param (OAuth callback)
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("gmail_connected") === "true") {
      params.delete("gmail_connected");
      const qs = params.toString();
      window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
      // Re-check auth status
      void fetchGmailStatus().then((status) => {
        setConnected(status.connected);
        setEmail(status.email ?? null);
        setLoading(false);
      });
    }
  }, []);

  // Poll auth status while OAuth popup is open (max 5 minutes)
  useEffect(() => {
    if (!pollingAuth) return;
    const startTime = Date.now();
    const interval = setInterval(async () => {
      if (Date.now() - startTime > 5 * 60 * 1000) {
        setPollingAuth(false);
        setError("Authorization timed out. Please try again.");
        return;
      }
      try {
        const status = await fetchGmailStatus();
        if (status.connected) {
          setConnected(true);
          setEmail(status.email ?? null);
          setPollingAuth(false);
          onGmailConnected?.();
        }
      } catch { /* ignore */ }
    }, 2000);
    return () => clearInterval(interval);
  }, [pollingAuth, onGmailConnected]);

  const handleConnectGmail = () => {
    const popup = window.open(getGmailAuthUrl(), "gmail-auth", "width=600,height=700");
    if (!popup || popup.closed) {
      setError("Popup was blocked. Please allow popups for this site and try again.");
      return;
    }
    setPollingAuth(true);
  };

  const handleScan = async (query?: string) => {
    setScanning(true);
    setError(null);
    setSelected(new Set());
    const q = query || searchQuery;
    try {
      // Use semantic search (falls back to Gmail API scan on failure)
      const results = await searchGmailSemantic(q);
      setThreads(results);
    } catch {
      // Direct fallback to scan
      try {
        const results = await scanGmail(q);
        setThreads(results);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Search failed");
      }
    } finally {
      setScanning(false);
    }
  };

  const toggleThread = (threadId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(threadId)) next.delete(threadId);
      else next.add(threadId);
      return next;
    });
  };

  const handleAttach = () => {
    if (selected.size === 0) return;
    const selectedThreads: SelectedThread[] = threads
      .filter((t) => selected.has(t.id))
      .map((t) => ({ id: t.id, subject: t.subject, attachmentCount: t.attachment_count }));
    onSelect(selectedThreads);
    onClose();
  };

  if (loading) {
    return (
      <div className="w-full max-h-[min(80vh,640px)] rounded-lg border border-th-border bg-th-bg shadow-lg p-8 flex items-center justify-center">
        <p className="text-sm text-th-text-muted">Checking Gmail connection...</p>
      </div>
    );
  }

  if (!connected) {
    return (
      <div className="w-full max-h-[min(80vh,640px)] rounded-lg border border-th-border bg-th-bg shadow-lg p-8">
        <div className="flex items-center justify-between mb-4">
          <span className="text-sm font-medium text-th-text">From Gmail</span>
          <button onClick={onClose} className="text-th-text-muted hover:text-th-text text-sm">✕</button>
        </div>
        {error && (
          <div className="mb-4 rounded-lg border border-th-error-text/30 bg-th-error-bg px-4 py-2 text-sm text-th-error-text">
            {error}
          </div>
        )}
        <div className="flex flex-col items-center gap-3">
          <p className="text-sm text-th-text-muted text-center">
            Connect your Gmail to attach invoices and construction documents.
          </p>
          {pollingAuth ? (
            <p className="text-sm text-th-text-muted">Waiting for authorization...</p>
          ) : (
            <button
              onClick={handleConnectGmail}
              className="rounded-lg bg-th-accent px-6 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90"
            >
              Connect Gmail
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="w-full max-h-[min(80vh,640px)] rounded-lg border border-th-border bg-th-bg shadow-lg flex flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-th-border px-4 py-3 space-y-2 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-th-text">From Gmail</span>
            <span className="text-xs text-th-text-muted">{email}</span>
          </div>
          <button onClick={onClose} className="text-th-text-muted hover:text-th-text text-sm">✕</button>
        </div>
        <form
          onSubmit={(e) => { e.preventDefault(); void handleScan(); }}
          className="flex items-center gap-2"
        >
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Gmail search query..."
            className="flex-1 rounded-lg border border-th-border bg-th-bg px-3 py-1.5 text-sm text-th-text placeholder-th-text-muted focus:border-th-accent focus:outline-none"
          />
          <button
            type="submit"
            disabled={scanning}
            className="rounded-lg border border-th-border px-4 py-1.5 text-xs font-medium text-th-text transition-colors hover:border-th-accent hover:text-th-accent disabled:opacity-50"
          >
            {scanning ? "Scanning..." : "Scan"}
          </button>
        </form>
        {suggestionsLoading && (!suggestions || suggestions.length === 0) && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-th-text-muted animate-pulse">Generating search suggestions...</span>
          </div>
        )}
        {suggestions && suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {suggestions.map((s) => (
              <button
                key={s.label}
                onClick={() => { setSearchQuery(s.query); void handleScan(s.query); }}
                disabled={scanning}
                className={`rounded-full px-3 py-0.5 text-xs transition-colors ${
                  searchQuery === s.query
                    ? "bg-th-accent text-white"
                    : "border border-th-accent/40 text-th-accent hover:bg-th-accent hover:text-white"
                } disabled:opacity-50`}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {error && (
        <div className="mx-4 mt-3 flex items-center justify-between rounded-lg border border-th-error-text/30 bg-th-error-bg px-4 py-2 text-sm text-th-error-text flex-shrink-0">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-3 text-th-error-text hover:opacity-70">✕</button>
        </div>
      )}

      {/* Thread list */}
      <div className="flex-1 overflow-y-auto p-4 min-h-0">
        {scanning && threads.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-th-text-muted">Scanning Gmail...</p>
          </div>
        )}

        {!scanning && threads.length === 0 && (
          <div className="flex items-center justify-center py-8">
            <p className="text-sm text-th-text-muted">
              {suggestions && suggestions.length > 0
                ? "Click a suggestion above or type a Gmail search query."
                : "Type a Gmail search query and hit Scan."}
            </p>
          </div>
        )}

        {threads.length > 0 && (
          <div className="space-y-1.5">
            {threads.map((thread) => (
              <ThreadRow
                key={thread.id}
                thread={thread}
                checked={selected.has(thread.id)}
                onToggle={() => toggleThread(thread.id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer with attach button */}
      {threads.length > 0 && (
        <div className="border-t border-th-border px-4 py-3 flex justify-end flex-shrink-0">
          <button
            onClick={handleAttach}
            disabled={selected.size === 0}
            className="rounded-lg bg-th-accent px-4 py-1.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Attach Selected ({selected.size})
          </button>
        </div>
      )}
    </div>
  );
}

function ThreadRow({
  thread,
  checked,
  onToggle,
}: {
  thread: GmailThread;
  checked: boolean;
  onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [preview, setPreview] = useState<GmailThreadPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const toggleExpanded = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && !preview && !previewLoading) {
      setPreviewLoading(true);
      setPreviewError(null);
      try {
        const p = await fetchGmailThreadPreview(thread.id);
        setPreview(p);
      } catch (err) {
        setPreviewError(err instanceof Error ? err.message : "Preview failed");
      } finally {
        setPreviewLoading(false);
      }
    }
  };

  const formattedDate = formatEmailDate(thread.date);

  return (
    <div className="rounded-lg border border-th-border hover:bg-th-surface/40">
      <div className="flex items-start gap-2 px-3 py-2">
        <input
          type="checkbox"
          checked={checked}
          onChange={onToggle}
          className="mt-1 rounded border-th-border flex-shrink-0"
        />
        <button
          type="button"
          onClick={() => void toggleExpanded()}
          className="flex-1 min-w-0 text-left"
          aria-expanded={expanded}
        >
          <div className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium text-th-text">
              {thread.subject || "(no subject)"}
            </span>
            {thread.downloaded && <span className="text-xs text-th-accent flex-shrink-0">✓</span>}
            {thread.score != null && (
              <span className="ml-auto text-[10px] text-th-text-faint flex-shrink-0">
                {Math.round(thread.score * 100)}%
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-xs">
            <span className="truncate text-th-text-muted">{thread.sender}</span>
            <span className="text-th-text-faint flex-shrink-0">·</span>
            <span className="text-th-text-faint flex-shrink-0" title={thread.date}>
              {formattedDate}
            </span>
            {thread.message_count > 1 && (
              <>
                <span className="text-th-text-faint flex-shrink-0">·</span>
                <span className="text-th-text-faint flex-shrink-0">{thread.message_count} msgs</span>
              </>
            )}
            {thread.attachment_count > 0 && (
              <>
                <span className="text-th-text-faint flex-shrink-0">·</span>
                <span className="text-th-text-muted flex-shrink-0">
                  📎 {thread.attachment_count}
                </span>
              </>
            )}
          </div>
          {thread.snippet && !expanded && (
            <div
              className="mt-1 text-xs text-th-text-faint overflow-hidden"
              style={{
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
              }}
            >
              {thread.snippet}
            </div>
          )}
        </button>
        <span
          className={`mt-1 text-xs text-th-text-faint transition-transform flex-shrink-0 ${expanded ? "rotate-90" : ""}`}
          aria-hidden
        >
          ▶
        </span>
      </div>
      {expanded && (
        <div className="border-t border-th-border px-3 py-2 bg-th-surface/30">
          {previewLoading && (
            <p className="text-xs text-th-text-muted">Loading preview…</p>
          )}
          {previewError && (
            <p className="text-xs text-th-error-text">{previewError}</p>
          )}
          {preview && (
            <div className="space-y-3">
              {preview.messages.map((m, i) => (
                <div key={i} className="space-y-1">
                  <div className="text-[11px] text-th-text-muted">
                    <span className="font-medium">{m.from}</span>
                    {m.date && (
                      <span className="ml-2 text-th-text-faint" title={m.date}>
                        {formatEmailDate(m.date)}
                      </span>
                    )}
                  </div>
                  <pre className="whitespace-pre-wrap break-words text-xs text-th-text font-sans max-h-64 overflow-y-auto">
                    {m.body_text.trim() || "(no text body)"}
                  </pre>
                </div>
              ))}
              {preview.attachments.length > 0 && (
                <div className="text-[11px] text-th-text-muted">
                  Attachments: {preview.attachments.join(", ")}
                </div>
              )}
              <a
                href={`https://mail.google.com/mail/u/0/#inbox/${thread.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block text-[11px] text-th-accent hover:underline"
              >
                Open in Gmail ↗
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
