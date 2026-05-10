"use client";

import { useRef, useEffect, useState } from "react";
import type { Topic } from "@/lib/types";

/** Render a short relative time, e.g. "5m ago", "3h ago", "2d ago", "Apr 14".
 *  Falls back to empty string for missing/invalid input. */
function relativeTime(iso: string | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const diffSec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`;
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`;
  // Older: show absolute month/day so it's still readable.
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export type SessionState =
  | "working"
  | "awaiting_question"
  | "awaiting_permission"
  | "idle"
  | "dormant";

type TopicSelectorProps = {
  topics: Topic[];
  activeTopic: string | null;
  activeSession: string | null;
  /**
   * Map session_id → runtime state from /api/sessions. Used to render a
   * status pill next to each conversation. Sessions not in the map render
   * nothing (e.g. stale topic conv records).
   */
  sessionStates?: Record<string, SessionState>;
  /** Map session_id → backend ("tmux" | "bhatti"). Drives the VM badge. */
  sessionBackends?: Record<string, "tmux" | "bhatti">;
  onSelectTopic: (slug: string, sessionId: string) => void;
  onCreateTopic: (name: string, options?: { backend?: "tmux" | "bhatti" }) => Promise<void> | void;
  onDeleteTopic: (slug: string) => void;
};

const STATE_DISPLAY: Record<SessionState, { label: string; dot: string; title: string }> = {
  working: { label: "working", dot: "bg-yellow-500", title: "Claude is thinking" },
  awaiting_question: {
    label: "?",
    dot: "bg-th-accent",
    title: "Claude is asking a question",
  },
  awaiting_permission: {
    label: "!",
    dot: "bg-th-accent",
    title: "Claude is waiting on a permission prompt",
  },
  idle: { label: "idle", dot: "bg-green-500", title: "Idle, ready for input" },
  dormant: { label: "dormant", dot: "bg-th-text-faint", title: "Dormant — will resume on next message" },
};

function StatePill({
  state,
  backend,
}: {
  state: SessionState | undefined;
  backend?: "tmux" | "bhatti";
}) {
  if (!state) return null;
  const meta = STATE_DISPLAY[state];
  return (
    <span
      title={meta.title}
      className="inline-flex items-center gap-1 rounded-full bg-th-surface px-1.5 py-0.5 text-[10px] text-th-text-muted"
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {meta.label}
      {backend === "bhatti" && (
        <span className="ml-1 text-[9px] text-th-text-faint" title="Running in isolated VM">VM</span>
      )}
    </span>
  );
}

export function TopicSelector({
  topics,
  activeTopic,
  activeSession,
  sessionStates,
  sessionBackends,
  onSelectTopic,
  onCreateTopic,
  onDeleteTopic,
}: TopicSelectorProps) {
  const [open, setOpen] = useState(false);
  const [expandedTopic, setExpandedTopic] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [useVm, setUseVm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const nameInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      // Don't close mid-submit — topic creation is a multi-step server call
      // (create topic, then start a conversation/spawn claude). Closing the
      // form before it completes drops the user into a half-state where the
      // first message they type races with handleNewTopic and may seed a
      // duplicate auto-named topic via ensureSession.
      if (submitting) return;
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
        setExpandedTopic(null);
        setCreating(false);
        setNewName("");
        setUseVm(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [submitting]);

  const activeTopicObj = topics.find((t) => t.slug === activeTopic);
  // The active session's backend, if known. Drives the always-visible
  // "VM" badge on the topic chooser so the user can see at a glance whether
  // the current conversation runs in a bhatti microVM.
  const activeBackend: "tmux" | "bhatti" | undefined =
    activeSession ? sessionBackends?.[activeSession] : undefined;
  const activeIsVm = activeBackend === "bhatti";

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="true"
        aria-expanded={open}
        title={activeIsVm ? "Running in isolated VM (bhatti)" : undefined}
        className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-th-text transition-colors hover:text-th-text ${
          activeIsVm
            ? "border-th-accent bg-th-accent/10 hover:border-th-accent"
            : "border-th-border hover:border-th-accent"
        }`}
      >
        {activeIsVm && (
          <span className="rounded bg-th-accent px-1.5 py-0.5 text-[10px] font-bold text-white tracking-wide">
            VM
          </span>
        )}
        <span className="max-w-[200px] truncate transition-opacity duration-500">
          {activeTopicObj ? activeTopicObj.name : "New topic"}
        </span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-40 mt-1 w-80 rounded-lg border border-th-border bg-th-bg shadow-xl">
          {creating ? (
            <form
              className="flex flex-col gap-2 border-b border-th-border px-3 py-2"
              onSubmit={async (e) => {
                e.preventDefault();
                const trimmed = newName.trim();
                if (!trimmed || submitting) return;
                setSubmitting(true);
                try {
                  await onCreateTopic(trimmed, {
                    backend: useVm ? "bhatti" : "tmux",
                  });
                  // Only close on success — leave the form open on failure
                  // so the user can retry without retyping the name.
                  setCreating(false);
                  setNewName("");
                  setUseVm(false);
                  setOpen(false);
                } catch (err) {
                  console.warn("Topic create failed:", err);
                } finally {
                  setSubmitting(false);
                }
              }}
            >
              <div className="flex items-center gap-2">
                <input
                  ref={nameInputRef}
                  type="text"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  placeholder="Topic name..."
                  autoFocus
                  disabled={submitting}
                  className="flex-1 rounded border border-th-border bg-th-bg px-2 py-1.5 text-sm text-th-text placeholder:text-th-text-faint focus:border-th-accent focus:outline-none disabled:opacity-60"
                  onKeyDown={(e) => {
                    if (e.key === "Escape" && !submitting) { setCreating(false); setNewName(""); setUseVm(false); }
                  }}
                />
                <button
                  type="submit"
                  disabled={!newName.trim() || submitting}
                  className="rounded bg-th-accent px-2.5 py-1.5 text-xs font-medium text-white hover:bg-th-accent-hover disabled:opacity-40"
                >
                  {submitting ? "Creating…" : "Create"}
                </button>
              </div>
              <label className="flex items-center gap-1.5 text-xs text-th-text-muted cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={useVm}
                  onChange={(e) => setUseVm(e.target.checked)}
                  disabled={submitting}
                  className="cursor-pointer"
                />
                Run in isolated VM (experimental)
              </label>
            </form>
          ) : (
            <button
              onClick={() => setCreating(true)}
              className="w-full border-b border-th-border px-4 py-2.5 text-left text-sm text-th-accent hover:bg-th-surface"
            >
              + New topic
            </button>
          )}
          <div className="max-h-80 overflow-y-auto">
            {topics.length === 0 ? (
              <p className="px-4 py-3 text-xs text-th-text-muted">
                No topics yet
              </p>
            ) : (
              topics.map((topic) => (
                <div key={topic.slug}>
                  {/* Topic row */}
                  <div
                    className={`flex items-center px-4 py-2 text-sm hover:bg-th-surface ${
                      activeTopic === topic.slug
                        ? "bg-th-surface-hover text-th-text"
                        : "text-th-text-muted"
                    }`}
                  >
                    <button
                      onClick={() => {
                        if (expandedTopic === topic.slug) {
                          setExpandedTopic(null);
                        } else {
                          setExpandedTopic(topic.slug);
                          // Select the most recent conversation (last in array)
                          if (topic.conversations.length > 0) {
                            const latest = topic.conversations[topic.conversations.length - 1];
                            onSelectTopic(topic.slug, latest.session_id);
                          } else {
                            onSelectTopic(topic.slug, "");
                          }
                          setOpen(false);
                        }
                      }}
                      className="flex-1 min-w-0 text-left"
                    >
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm transition-opacity duration-300">
                          {topic.name}
                        </span>
                        {(() => {
                          const latest = topic.conversations[topic.conversations.length - 1];
                          const s = latest && sessionStates?.[latest.session_id];
                          const b = latest && sessionBackends?.[latest.session_id];
                          return s ? <StatePill state={s} backend={b} /> : null;
                        })()}
                      </div>
                      <div className="truncate text-xs text-th-text-faint">
                        {(() => {
                          const rel = relativeTime(topic.last_activity || topic.created_at);
                          const count = topic.conversations.length;
                          const label = `${count} ${count === 1 ? "conversation" : "conversations"}`;
                          return rel ? `${rel} · ${label}` : label;
                        })()}
                      </div>
                    </button>
                    {/* Expand/collapse chevron */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpandedTopic(
                          expandedTopic === topic.slug ? null : topic.slug,
                        );
                      }}
                      className="ml-1 rounded p-1 text-th-text-muted hover:bg-th-surface-hover hover:text-th-text"
                      title="Show conversations"
                    >
                      <svg
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        className={`transition-transform ${
                          expandedTopic === topic.slug ? "rotate-180" : ""
                        }`}
                      >
                        <polyline points="6 9 12 15 18 9" />
                      </svg>
                    </button>
                    {/* Delete button */}
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        void onDeleteTopic(topic.slug);
                      }}
                      className="ml-1 rounded p-1 text-th-text-muted hover:bg-th-surface-hover hover:text-th-accent"
                      title="Delete topic"
                    >
                      ✕
                    </button>
                  </div>
                  {/* Expanded conversations */}
                  {expandedTopic === topic.slug &&
                    topic.conversations.length > 0 && (
                      <div className="border-l-2 border-th-border ml-6">
                        {topic.conversations.map((conv) => (
                          <button
                            key={conv.id}
                            onClick={() => {
                              onSelectTopic(topic.slug, conv.session_id);
                              setOpen(false);
                              setExpandedTopic(null);
                            }}
                            className={`flex w-full items-center px-3 py-1.5 text-left text-xs hover:bg-th-surface ${
                              activeSession === conv.session_id
                                ? "bg-th-surface-hover text-th-text"
                                : "text-th-text-muted"
                            }`}
                          >
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2">
                                <span className="truncate">{conv.title || conv.id}</span>
                                <StatePill
                                  state={sessionStates?.[conv.session_id]}
                                  backend={sessionBackends?.[conv.session_id]}
                                />
                              </div>
                              <div className="truncate text-th-text-faint">
                                {conv.status}
                              </div>
                            </div>
                          </button>
                        ))}
                      </div>
                    )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
