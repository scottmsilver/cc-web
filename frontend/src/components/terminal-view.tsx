"use client";

import { useEffect, useRef, useState } from "react";
import { CCHOST_API } from "@/lib/config";

export function TerminalView({
  sessionId,
}: {
  sessionId: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<import("@xterm/xterm").Terminal | null>(null);
  const fitRef = useRef<import("@xterm/addon-fit").FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("disconnected");

  useEffect(() => {
    if (!sessionId || !containerRef.current) return;

    let disposed = false;

    (async () => {
      const { Terminal } = await import("@xterm/xterm");
      const { FitAddon } = await import("@xterm/addon-fit");
      // Load CSS
      await import("@xterm/xterm/css/xterm.css");

      if (disposed) return;

      const fitAddon = new FitAddon();
      const term = new Terminal({
        fontSize: 13,
        fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
        theme: {
          background: getComputedStyle(document.documentElement).getPropertyValue("--th-term-bg").trim() || "#1a1a2e",
          foreground: getComputedStyle(document.documentElement).getPropertyValue("--th-term-text").trim() || "#e0e0e0",
          cursor: getComputedStyle(document.documentElement).getPropertyValue("--th-accent").trim() || "#e07a5f",
          selectionBackground: "rgba(255,255,255,0.15)",
        },
        cursorBlink: false,
        disableStdin: true,
        scrollback: 10000,
        convertEol: true,
      });

      term.loadAddon(fitAddon);
      term.open(containerRef.current!);
      fitAddon.fit();

      termRef.current = term;
      fitRef.current = fitAddon;

      // Connect WebSocket
      const wsBase = CCHOST_API.replace(/^http/, "ws");
      const wsUrl = `${wsBase}/api/sessions/${encodeURIComponent(sessionId)}/terminal/ws`;
      setStatus("connecting");

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!disposed) setStatus("connected");
      };

      ws.onmessage = (event) => {
        if (disposed) return;
        // Each message is the full terminal content; write it fresh
        term.reset();
        term.write(event.data);
        // Scroll to bottom
        term.scrollToBottom();
      };

      ws.onclose = () => {
        if (!disposed) setStatus("disconnected");
      };

      ws.onerror = () => {
        if (!disposed) setStatus("disconnected");
      };

      // Handle resize
      const ro = new ResizeObserver(() => {
        try { fitAddon.fit(); } catch { /* ignore */ }
      });
      ro.observe(containerRef.current!);

      // Cleanup stored for the return
      const cleanup = () => {
        disposed = true;
        ro.disconnect();
        ws.close();
        term.dispose();
        termRef.current = null;
        fitRef.current = null;
        wsRef.current = null;
      };

      // Store cleanup for the effect return
      (containerRef.current as HTMLElement & { _cleanup?: () => void })._cleanup = cleanup;
    })();

    return () => {
      const el = containerRef.current as HTMLElement & { _cleanup?: () => void } | null;
      el?._cleanup?.();
    };
  }, [sessionId]);

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
        <span className="text-xs font-medium text-th-text-muted">
          Terminal — {sessionId}
        </span>
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${
          status === "connected"
            ? "bg-th-success-bg text-th-success-text"
            : status === "connecting"
              ? "bg-th-warning-bg text-th-warning-text"
              : "bg-th-surface text-th-text-faint"
        }`}>
          {status}
        </span>
      </div>
      <div ref={containerRef} className="flex-1 min-h-0" />
    </div>
  );
}
