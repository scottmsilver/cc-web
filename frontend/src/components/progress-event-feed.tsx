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
  { key: "messages", label: "Msg" },
  { key: "tools", label: "Tools" },
  { key: "thinking", label: "Think" },
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
    if (filter === "all") return events;
    return events.filter((event) => getProgressEventCategory(event) === filter);
  }, [events, filter]);

  return (
    <section className={className}>
      <div className="rounded-lg border border-gray-300 bg-white">
        <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200">
          <span className="text-xs font-medium text-gray-700 mr-1">Events</span>
          {FILTERS.map((item) => {
            const active = item.key === filter;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setFilter(item.key)}
                className={`rounded px-2 py-0.5 text-[11px] font-medium transition ${
                  active
                    ? "bg-[var(--th-accent)] text-white"
                    : "text-gray-600 hover:bg-gray-100"
                }`}
              >
                {item.label}
              </button>
            );
          })}
          <span className="ml-auto text-[11px] text-gray-500">{filteredEvents.length}/{events.length}</span>
        </div>

        <div className="divide-y divide-gray-100 max-h-[500px] overflow-y-auto">
          {filteredEvents.length === 0 ? (
            <p className="px-3 py-2 text-xs text-gray-500">No events.</p>
          ) : (
            filteredEvents.map((event: ProgressEventResponse, index) => (
              <div key={`${event.kind}-${index}`} className="flex items-baseline gap-2 px-3 py-1.5 text-xs">
                <span className="w-14 flex-shrink-0 text-[10px] uppercase tracking-wider text-gray-500 font-medium">
                  {getProgressEventBadgeText(event)}
                </span>
                <span className="text-gray-800 truncate flex-1">
                  {getProgressEventPreview(event) || getProgressEventLabel(event)}
                </span>
                <span className="text-[10px] text-gray-400 flex-shrink-0">
                  {formatConfidence(event.confidence)}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
