"use client";

import { useRef, useEffect, useState } from "react";

type SessionRecord = {
  id: string;
  working_dir?: string | null;
};

type SessionSelectorProps = {
  sessions: SessionRecord[];
  activeSession: string | null;
  onSelectSession: (sessionId: string) => void;
  onNewSession: () => void;
  onDeleteSession: (sessionId: string) => void;
};

export function SessionSelector({
  sessions,
  activeSession,
  onSelectSession,
  onNewSession,
  onDeleteSession,
}: SessionSelectorProps) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700 transition-colors hover:border-[var(--th-accent)] hover:text-gray-700"
      >
        <span className="max-w-[200px] truncate">
          {activeSession ? activeSession : "New session"}
        </span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-40 mt-1 w-80 rounded-lg border border-gray-300 bg-white shadow-xl">
          <button
            onClick={() => { onNewSession(); setOpen(false); }}
            className="w-full border-b border-gray-300 px-4 py-2.5 text-left text-sm text-[var(--th-accent)] hover:bg-gray-50"
          >
            + New session
          </button>
          <div className="max-h-64 overflow-y-auto">
            {sessions.length === 0 ? (
              <p className="px-4 py-3 text-xs text-gray-600">No sessions</p>
            ) : (
              sessions.map((session) => (
                <div
                  key={session.id}
                  className={`flex items-center px-4 py-2 text-sm hover:bg-gray-50 ${
                    activeSession === session.id ? "bg-gray-100 text-gray-900" : "text-gray-600"
                  }`}
                >
                  <button
                    onClick={() => { onSelectSession(session.id); setOpen(false); }}
                    className="flex-1 truncate text-left"
                  >
                    {session.id}
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); void onDeleteSession(session.id); }}
                    className="ml-2 rounded p-1 text-gray-600 hover:bg-gray-100 hover:text-[var(--th-accent)]"
                    title="Delete session"
                  >
                    ✕
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
