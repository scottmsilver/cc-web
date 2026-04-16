"use client";

import type { TranscriptTask } from "@/lib/types";

export function TaskPanel({ tasks }: { tasks: TranscriptTask[] }) {
  if (tasks.length === 0) return null;

  const done = tasks.filter((t) => t.status === "completed").length;
  const active = tasks.find((t) => t.status === "in_progress");

  return (
    <div className="rounded-lg border border-th-border bg-th-surface/50 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-th-border bg-th-surface">
        <span className="text-xs font-semibold text-th-text">Tasks</span>
        <span className="text-[11px] text-th-text-muted">
          {done}/{tasks.length}
        </span>
      </div>
      <ul className="divide-y divide-th-border/60">
        {tasks.map((t) => {
          const isDone = t.status === "completed";
          const isActive = t.status === "in_progress";
          return (
            <li
              key={t.id}
              className={`flex items-start gap-2 px-3 py-2 text-xs ${isActive ? "bg-th-accent/5" : ""}`}
            >
              <StatusIcon status={t.status} />
              <div className="flex-1 min-w-0">
                <div className={`${isDone ? "line-through text-th-text-faint" : "text-th-text"}`}>
                  {isActive && t.activeForm ? t.activeForm : t.subject}
                </div>
                {t.description && !isDone && (
                  <div className="mt-0.5 text-[11px] text-th-text-muted line-clamp-2">{t.description}</div>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {active && (
        <div className="px-3 py-1.5 text-[11px] text-th-text-muted border-t border-th-border bg-th-surface/80">
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-th-accent animate-pulse mr-1.5 align-middle" />
          {active.activeForm || active.subject}
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: TranscriptTask["status"] }) {
  if (status === "completed") {
    return (
      <span className="mt-0.5 flex-shrink-0 text-th-accent" aria-label="completed">
        ✓
      </span>
    );
  }
  if (status === "in_progress") {
    return (
      <span
        className="mt-1 flex-shrink-0 inline-block w-2 h-2 rounded-full bg-th-accent animate-pulse"
        aria-label="in progress"
      />
    );
  }
  return (
    <span
      className="mt-1 flex-shrink-0 inline-block w-2 h-2 rounded-full border border-th-text-faint"
      aria-label="pending"
    />
  );
}
