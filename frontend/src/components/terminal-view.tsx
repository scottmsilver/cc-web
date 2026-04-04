"use client";

import { useEffect, useRef, useState } from "react";

import { fetchTerminalOutput } from "@/lib/api";

export function TerminalView({
  sessionId,
}: {
  sessionId: string | null;
}) {
  const [output, setOutput] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;

    const poll = async () => {
      try {
        const text = await fetchTerminalOutput(sessionId);
        if (!cancelled) setOutput(text);
      } catch (error) {
        console.warn("Failed to fetch terminal output:", error);
      }
    };

    void poll();
    const id = setInterval(() => void poll(), 1500);
    return () => { cancelled = true; clearInterval(id); };
  }, [sessionId]);

  useEffect(() => {
    if (autoScroll && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [output, autoScroll]);

  if (!sessionId) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-th-text-muted">No session selected.</p>
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between px-4 py-2 border-b border-th-border">
        <span className="text-xs font-medium text-th-text-muted">tmux capture — {sessionId}</span>
        <label className="flex items-center gap-1.5 text-xs text-th-text-muted">
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
            className="accent-th-accent"
          />
          Auto-scroll
        </label>
      </div>
      <pre
        ref={preRef}
        className="flex-1 overflow-auto bg-th-term-bg text-th-term-text p-4 font-mono text-xs leading-5 whitespace-pre"
      >
        {output || "Waiting for output..."}
      </pre>
    </div>
  );
}
