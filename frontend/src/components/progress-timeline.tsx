"use client";

import { formatProgressLabel, type ProgressSnapshotResponse } from "@/lib/progress";

type ProgressTimelineProps = {
  milestones: ProgressSnapshotResponse["milestones"];
  className?: string;
};

export function ProgressTimeline({ milestones, className }: ProgressTimelineProps) {
  return (
    <section className={className}>
      <div className="rounded-xl border border-th-border bg-th-bg p-4">
        <div className="flex items-center justify-between gap-3 border-b border-th-border pb-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-th-text-muted">Timeline</p>
            <h3 className="mt-1 text-lg font-semibold text-th-text">Milestones</h3>
          </div>
          <div className="rounded-full border border-th-border bg-th-surface-hover px-3 py-1 text-xs text-th-text-muted">
            {milestones.length} milestones
          </div>
        </div>

        <div className="mt-4 space-y-2">
          {milestones.length === 0 ? (
            <p className="rounded-lg border border-dashed border-th-border bg-th-surface px-4 py-3 text-sm text-th-text-muted">
              No milestones yet.
            </p>
          ) : (
            milestones.map((milestone, index) => (
              <div key={`${milestone}-${index}`} className="flex gap-3 rounded-lg border border-th-border bg-th-surface px-4 py-3">
                <div className="mt-1 h-2.5 w-2.5 rounded-full bg-green-500 shadow-[0_0_0_4px_rgba(34,197,94,0.15)]" />
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-wider text-th-text-muted">Milestone {index + 1}</p>
                  <p className="mt-1 text-sm text-th-text">{formatProgressLabel(milestone)}</p>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
