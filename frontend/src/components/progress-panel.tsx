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
import { JsonlViewer } from "@/components/jsonl-viewer";
import { ProgressEventFeed } from "@/components/progress-event-feed";
import { ProgressTimeline } from "@/components/progress-timeline";
import { QuestionCard } from "@/components/question-card";

type ProgressPanelProps = {
  progress: ProgressResponse;
  sessionId?: string | null;
  onAnswer?: (optionIndex: number) => void;
  className?: string;
};

function RawJsonBlock({ progress }: { progress: ProgressResponse }) {
  return (
    <details className="group rounded-xl border border-th-border bg-th-bg p-4">
      <summary className="cursor-pointer list-none text-sm font-medium text-th-text">
        <span className="flex items-center justify-between gap-3">
          <span>Raw JSON</span>
          <span className="text-xs text-th-text-muted group-open:hidden">Expand</span>
          <span className="hidden text-xs text-th-text-muted group-open:inline">Collapse</span>
        </span>
      </summary>
      <pre className="mt-4 overflow-auto rounded-lg border border-th-border bg-th-surface p-4 text-xs leading-5 text-th-text">
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
    <div className="rounded-xl border border-th-border bg-th-bg p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-th-text-muted">Current activity</p>
          <h2 className="mt-2 text-xl font-semibold text-th-text">{summary.activityText}</h2>
        </div>
        <div
          className={[
            "rounded-full px-3 py-1 text-xs font-medium",
            pillTone === "error"
              ? "border border-red-300 bg-red-50 text-red-700"
              : pillTone === "warning"
                ? "border border-amber-300 bg-amber-50 text-amber-700"
                : pillTone === "success"
                  ? "border border-green-300 bg-green-50 text-green-700"
                  : "border border-th-border bg-th-surface text-th-text",
          ].join(" ")}
        >
          {pillLabel}
        </div>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-th-border bg-th-surface p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-th-text-muted">Primary label</p>
          <p className="mt-2 text-sm text-th-text">{summary.primaryLabelText}</p>
        </div>
        <div className="rounded-lg border border-th-border bg-th-surface p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-th-text-muted">Confidence</p>
          <p className="mt-2 text-sm text-th-text">
            {formatConfidence(snapshot.primary_confidence)} {snapshot.primary_label ? `(${snapshot.primary_label_source})` : ""}
          </p>
        </div>
        <div className="rounded-lg border border-th-border bg-th-surface p-4">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-th-text-muted">Background</p>
          <p className="mt-2 text-sm text-th-text">{formatBackgroundCount(snapshot.background_count)}</p>
        </div>
      </div>

      {run?.waiting_for_input ? <QuestionCard className="mt-5" run={run} onAnswer={onAnswer} /> : null}

      {snapshot.is_question && !run?.current_question && !run?.result?.questions?.length ? (
        <div className="mt-5 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Waiting for input, but no question payload is attached.
        </div>
      ) : null}
    </div>
  );
}

export function ProgressPanel({ progress, sessionId, onAnswer, className }: ProgressPanelProps) {
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
          <JsonlViewer sessionId={sessionId ?? null} />
          <RawJsonBlock progress={progress} />
        </div>
      </div>
    </section>
  );
}
