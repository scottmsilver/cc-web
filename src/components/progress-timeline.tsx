"use client";

import { formatProgressLabel, type ProgressSnapshotResponse } from "@/lib/progress";

type ProgressTimelineProps = {
  milestones: ProgressSnapshotResponse["milestones"];
  className?: string;
};

export function ProgressTimeline({ milestones, className }: ProgressTimelineProps) {
  return (
    <section className={className}>
      <div className="rounded-3xl border border-white/10 bg-white/5 p-4 shadow-[0_20px_60px_rgba(0,0,0,0.18)] backdrop-blur">
        <div className="flex items-center justify-between gap-3 border-b border-white/10 pb-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-zinc-400">Timeline</p>
            <h3 className="mt-1 text-lg font-semibold text-white">Persistent milestones</h3>
          </div>
          <div className="rounded-full border border-white/10 bg-black/20 px-3 py-1 text-xs text-zinc-300">
            {milestones.length} milestones
          </div>
        </div>

        <div className="mt-4 space-y-2">
          {milestones.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-white/10 bg-black/10 px-4 py-3 text-sm text-zinc-400">
              No milestones yet.
            </p>
          ) : (
            milestones.map((milestone, index) => (
              <div key={`${milestone}-${index}`} className="flex gap-3 rounded-2xl border border-white/10 bg-black/10 px-4 py-3">
                <div className="mt-1 h-2.5 w-2.5 rounded-full bg-emerald-400 shadow-[0_0_0_4px_rgba(52,211,153,0.14)]" />
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-[0.2em] text-zinc-500">Milestone {index + 1}</p>
                  <p className="mt-1 text-sm text-zinc-100">{formatProgressLabel(milestone)}</p>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
