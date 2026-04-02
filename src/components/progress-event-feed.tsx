"use client";

import { useMemo, useState } from "react";

import {
  formatConfidence,
  getProgressEventBadgeText,
  getProgressEventCategory,
  getProgressEventLabel,
  getProgressEventPreview,
  type ProgressEventResponse,
  type ProgressEventCategory,
  type ProgressSnapshotResponse,
} from "@/lib/progress";

const FILTERS: { key: ProgressEventCategory; label: string }[] = [
  { key: "all", label: "All" },
  { key: "messages", label: "Messages" },
  { key: "tools", label: "Tools" },
  { key: "background", label: "Background" },
  { key: "notifications", label: "Notifications" },
  { key: "thinking", label: "Thinking" },
  { key: "other", label: "Other" },
];

type ProgressEventFeedProps = {
  snapshot: ProgressSnapshotResponse;
  className?: string;
};

export function ProgressEventFeed({ snapshot, className }: ProgressEventFeedProps) {
  const [filter, setFilter] = useState<ProgressEventCategory>("all");
  const events = snapshot.events;

  const filteredEvents = useMemo(() => {
    if (filter === "all") {
      return events;
    }
    return events.filter((event) => getProgressEventCategory(event) === filter);
  }, [events, filter]);

  return (
    <section className={className}>
      <div className="rounded-3xl border border-white/10 bg-white/5 p-4 shadow-[0_20px_60px_rgba(0,0,0,0.16)] backdrop-blur">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-zinc-400">Event feed</p>
            <h3 className="mt-1 text-lg font-semibold text-white">Compact event stream</h3>
          </div>
          <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1 text-xs text-zinc-300">
            {filteredEvents.length}/{events.length}
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {FILTERS.map((item) => {
            const active = item.key === filter;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setFilter(item.key)}
                className={[
                  "rounded-full border px-3 py-1.5 text-xs font-medium transition",
                  active
                    ? "border-emerald-400/40 bg-emerald-400/15 text-emerald-200"
                    : "border-white/10 bg-black/15 text-zinc-400 hover:border-white/20 hover:text-zinc-200",
                ].join(" ")}
              >
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="mt-4 space-y-2">
          {filteredEvents.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-white/10 bg-black/10 px-4 py-3 text-sm text-zinc-400">
              No events match this filter.
            </p>
          ) : (
            filteredEvents.map((event: ProgressEventResponse, index) => (
              <article key={`${event.kind}-${index}`} className="rounded-2xl border border-white/10 bg-black/15 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
                      {getProgressEventLabel(event)}
                    </p>
                    <p className="mt-1 text-sm text-zinc-100">{getProgressEventPreview(event)}</p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-[10px] uppercase tracking-[0.16em] text-zinc-300">
                      {getProgressEventBadgeText(event)}
                    </span>
                    <span className="text-[11px] text-zinc-500">{formatConfidence(event.confidence)}</span>
                  </div>
                </div>
              </article>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
