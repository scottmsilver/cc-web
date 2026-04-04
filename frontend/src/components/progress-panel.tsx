"use client";

import {
  formatBackgroundCount,
  formatConfidence,
  getProgressRunStatusText,
  getProgressRunStatusTone,
  normalizeProgressSnapshot,
  summarizeProgressSnapshot,
  type ProgressResponse,
  type ProgressSnapshotResponse,
} from "@/lib/progress";
import { ProgressEventFeed } from "@/components/progress-event-feed";
import { ProgressTimeline } from "@/components/progress-timeline";
import { QuestionCard } from "@/components/question-card";

type ProgressPanelProps = {
  progress: ProgressResponse;
  onAnswer?: (optionIndex: number) => void;
  className?: string;
};

function RawJsonBlock({ progress }: { progress: ProgressResponse }) {
  return (
    <details className="group rounded-3xl border border-white/10 bg-white/5 p-4 shadow-[0_20px_60px_rgba(0,0,0,0.14)] backdrop-blur">
      <summary className="cursor-pointer list-none text-sm font-medium text-zinc-200">
        <span className="flex items-center justify-between gap-3">
          <span>Raw JSON payload</span>
          <span className="text-xs text-zinc-500 group-open:hidden">Expand</span>
          <span className="hidden text-xs text-zinc-500 group-open:inline">Collapse</span>
        </span>
      </summary>
      <pre className="mt-4 overflow-auto rounded-2xl border border-white/10 bg-black/30 p-4 text-xs leading-5 text-zinc-300">
        {JSON.stringify(progress, null, 2)}
      </pre>
    </details>
  );
}

function StatusBlock({
  snapshot,
  run,
  onAnswer,
}: {
  snapshot: ProgressSnapshotResponse;
  run: ProgressResponse["run"];
  onAnswer?: (optionIndex: number) => void;
}) {
  const summary = summarizeProgressSnapshot(snapshot);
  const pillTone = getProgressRunStatusTone(run);
  const pillLabel = getProgressRunStatusText(run);

  return (
    <div className="rounded-3xl border border-white/10 bg-white/5 p-5 shadow-[0_20px_60px_rgba(0,0,0,0.18)] backdrop-blur">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-zinc-400">Current activity</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">{summary.activityText}</h2>
        </div>
        <div
          className={[
            "rounded-full px-3 py-1 text-xs font-medium",
            pillTone === "error"
              ? "border border-rose-400/25 bg-rose-500/15 text-rose-100"
              : pillTone === "warning"
                ? "border border-amber-400/25 bg-amber-400/15 text-amber-100"
                : pillTone === "success"
                  ? "border border-emerald-400/25 bg-emerald-400/15 text-emerald-100"
                  : "border border-white/10 bg-white/10 text-zinc-100",
          ].join(" ")}
        >
          {pillLabel}
        </div>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl border border-white/10 bg-black/15 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">Primary label</p>
          <p className="mt-2 text-sm text-zinc-100">{summary.primaryLabelText}</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-black/15 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">Confidence</p>
          <p className="mt-2 text-sm text-zinc-100">
            {formatConfidence(snapshot.primary_confidence)} {snapshot.primary_label ? `(${snapshot.primary_label_source})` : ""}
          </p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-black/15 p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">Background</p>
          <p className="mt-2 text-sm text-zinc-100">{formatBackgroundCount(snapshot.background_count)}</p>
        </div>
      </div>

      {run?.waiting_for_input ? <QuestionCard className="mt-5" run={run} onAnswer={onAnswer} /> : null}

      {snapshot.is_question && !run?.current_question && !run?.result?.questions?.length ? (
        <div className="mt-5 rounded-2xl border border-amber-400/20 bg-amber-400/10 px-4 py-3 text-sm text-amber-50">
          Waiting for input, but no question payload is attached.
        </div>
      ) : null}
    </div>
  );
}

export function ProgressPanel({ progress, onAnswer, className }: ProgressPanelProps) {
  const normalizedSnapshot = normalizeProgressSnapshot(progress.snapshot);

  return (
    <section className={className}>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.03fr)_minmax(0,0.97fr)] 2xl:grid-cols-2">
        <div className="space-y-6">
          <StatusBlock snapshot={normalizedSnapshot} run={progress.run} onAnswer={onAnswer} />
          <ProgressTimeline milestones={normalizedSnapshot.milestones} />
        </div>

        <div className="space-y-6">
          <ProgressEventFeed snapshot={normalizedSnapshot} />
          <RawJsonBlock progress={progress} />
        </div>
      </div>
    </section>
  );
}
