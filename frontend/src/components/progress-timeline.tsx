"use client";

import { formatProgressLabel, type ProgressSnapshotResponse } from "@/lib/progress";

type ProgressTimelineProps = {
  milestones: ProgressSnapshotResponse["milestones"];
  className?: string;
};

export function ProgressTimeline({ milestones, className }: ProgressTimelineProps) {
  return (
    <section className={className}>
      <div className="rounded-xl border border-gray-300 bg-white p-4">
        <div className="flex items-center justify-between gap-3 border-b border-gray-200 pb-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-gray-500">Timeline</p>
            <h3 className="mt-1 text-lg font-semibold text-gray-900">Milestones</h3>
          </div>
          <div className="rounded-full border border-gray-300 bg-gray-100 px-3 py-1 text-xs text-gray-600">
            {milestones.length} milestones
          </div>
        </div>

        <div className="mt-4 space-y-2">
          {milestones.length === 0 ? (
            <p className="rounded-lg border border-dashed border-gray-300 bg-gray-50 px-4 py-3 text-sm text-gray-500">
              No milestones yet.
            </p>
          ) : (
            milestones.map((milestone, index) => (
              <div key={`${milestone}-${index}`} className="flex gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
                <div className="mt-1 h-2.5 w-2.5 rounded-full bg-green-500 shadow-[0_0_0_4px_rgba(34,197,94,0.15)]" />
                <div className="min-w-0">
                  <p className="text-xs font-medium uppercase tracking-wider text-gray-500">Milestone {index + 1}</p>
                  <p className="mt-1 text-sm text-gray-800">{formatProgressLabel(milestone)}</p>
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
