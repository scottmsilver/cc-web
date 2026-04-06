"use client";

import { useEffect, useState } from "react";

import {
  fetchGmailStatus,
  getGmailAuthUrl,
  scanGmail,
  analyzeThread,
  type GmailThread,
} from "@/lib/api";

type InboxTabProps = {
  onAnalyzeComplete: (sessionId: string, runId: string) => void;
};

const SUGGESTED_SEARCHES = [
  { label: "Draw Requests", query: "from:landmarkswest.com subject:draw has:attachment" },
  { label: "Change Orders", query: "from:landmarkswest.com subject:(CO OR \"change order\")" },
  { label: "All from GC", query: "from:landmarkswest.com has:attachment" },
  { label: "Invoices", query: "from:landmarkswest.com subject:invoice has:attachment" },
];

export function InboxTab({ onAnalyzeComplete }: InboxTabProps) {
  const [connected, setConnected] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const [threads, setThreads] = useState<GmailThread[]>([]);
  const [scanning, setScanning] = useState(false);
  const [analyzingId, setAnalyzingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState(SUGGESTED_SEARCHES[0].query);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const gmailError = params.get("gmail_error");
    if (gmailError) {
      setError(`Gmail connection failed: ${gmailError}`);
      // Clean up the URL param
      params.delete("gmail_error");
      const qs = params.toString();
      window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
    }
  }, []);

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

  const handleScan = async (query?: string) => {
    setScanning(true);
    setError(null);
    try {
      const results = await scanGmail(query || searchQuery);
      setThreads(results);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Scan failed");
    } finally {
      setScanning(false);
    }
  };

  const handleAnalyze = async (threadId: string) => {
    setAnalyzingId(threadId);
    setError(null);
    try {
      const result = await analyzeThread(threadId);
      setThreads((prev) =>
        prev.map((t) => (t.id === threadId ? { ...t, analyzed: true } : t)),
      );
      onAnalyzeComplete(result.session_id, result.run_id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Analyze failed");
    } finally {
      setAnalyzingId(null);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-th-text-muted">Checking Gmail connection...</p>
      </div>
    );
  }

  if (!connected) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4">
        {error && (
          <div className="rounded-lg border border-th-error-text/30 bg-th-error-bg px-4 py-2 text-sm text-th-error-text">
            {error}
          </div>
        )}
        <p className="text-sm text-th-text-muted">
          Connect your Gmail to scan for invoices and construction documents.
        </p>
        <button
          onClick={() => { window.location.href = getGmailAuthUrl(); }}
          className="rounded-lg bg-th-accent px-6 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90"
        >
          Connect Gmail
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-th-border px-4 py-3 space-y-2">
        <div className="flex items-center gap-3">
          <span className="text-xs text-th-text-muted">Connected as</span>
          <span className="text-sm font-medium text-th-text">{email}</span>
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
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTED_SEARCHES.map((s) => (
            <button
              key={s.label}
              onClick={() => { setSearchQuery(s.query); void handleScan(s.query); }}
              disabled={scanning}
              className={`rounded-full px-3 py-0.5 text-xs transition-colors ${
                searchQuery === s.query
                  ? "bg-th-accent text-white"
                  : "border border-th-border text-th-text-muted hover:border-th-accent hover:text-th-accent"
              } disabled:opacity-50`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="mx-4 mt-3 flex items-center justify-between rounded-lg border border-th-error-text/30 bg-th-error-bg px-4 py-2 text-sm text-th-error-text">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="ml-3 text-th-error-text hover:opacity-70">
            ✕
          </button>
        </div>
      )}

      {/* Thread list */}
      <div className="flex-1 overflow-y-auto p-4">
        {scanning && threads.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <p className="text-sm text-th-text-muted">Scanning Gmail...</p>
          </div>
        )}

        {!scanning && threads.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <p className="text-sm text-th-text-muted">
              Click a filter above or type a Gmail search query and hit Scan.
            </p>
          </div>
        )}

        {threads.length > 0 && (
          <div className="space-y-2">
            {threads.map((thread) => (
              <div
                key={thread.id}
                className="flex items-center gap-4 rounded-lg border border-th-border px-4 py-3 hover:bg-th-surface"
              >
                <div className="flex-1 min-w-0">
                  <div className="truncate text-sm font-medium text-th-text">
                    {thread.subject}
                  </div>
                  <div className="flex items-center gap-3 mt-1">
                    <span className="truncate text-xs text-th-text-muted">
                      {thread.sender}
                    </span>
                    <span className="text-xs text-th-text-faint">
                      {thread.date}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 mt-0.5">
                    {thread.message_count > 1 && (
                      <span className="text-xs text-th-text-muted">
                        {thread.message_count} messages
                      </span>
                    )}
                    {thread.attachment_count > 0 && (
                      <span className="text-xs text-th-text-muted">
                        {thread.attachment_count} attachment{thread.attachment_count !== 1 ? "s" : ""}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex-shrink-0">
                  {thread.analyzed ? (
                    <span className="rounded-full bg-th-surface px-3 py-1 text-xs font-medium text-th-accent">
                      Analyzed
                    </span>
                  ) : analyzingId === thread.id ? (
                    <span className="text-xs text-th-text-muted">Analyzing...</span>
                  ) : (
                    <button
                      onClick={() => handleAnalyze(thread.id)}
                      disabled={analyzingId !== null}
                      className="rounded-lg border border-th-accent px-3 py-1 text-xs font-medium text-th-accent transition-colors hover:bg-th-accent hover:text-white disabled:opacity-50"
                    >
                      Analyze
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
