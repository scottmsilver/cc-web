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

import type { SubAgent } from "@/lib/api";

type ProgressPanelProps = {
  progress: ProgressResponse;
  sessionId?: string | null;
  onAnswer?: (optionIndex: number) => void;
  className?: string;
  subagents?: SubAgent[];
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
              ? "border border-th-error-text/30 bg-th-error-bg text-th-error-text"
              : pillTone === "warning"
                ? "border border-th-warning-border bg-th-warning-bg text-th-warning-text"
                : pillTone === "success"
                  ? "border border-th-success-text/30 bg-th-success-bg text-th-success-text"
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
        <div className="mt-5 rounded-lg border border-th-warning-border bg-th-warning-bg px-4 py-3 text-sm text-th-warning-text">
          Waiting for input, but no question payload is attached.
        </div>
      ) : null}
    </div>
  );
}

function SubAgentCards({ subagents }: { subagents: SubAgent[] }) {
  if (subagents.length === 0) return null;
  const running = subagents.filter(a => a.status === "running");
  const completed = subagents.filter(a => a.status === "completed");
  return (
    <div className="rounded-xl border border-th-border bg-th-bg p-4">
      <h3 className="text-sm font-medium text-th-text mb-3">
        Sub-agents {running.length > 0 && <span className="text-th-accent ml-1">({running.length} active)</span>}
      </h3>
      <div className="space-y-2">
        {running.map(a => (
          <div key={a.agent_id} className="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-th-surface border border-th-accent/20">
            <span className="mt-0.5 w-2 h-2 rounded-full bg-th-accent animate-pulse flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="text-xs text-th-text truncate">{a.description}</div>
              <div className="text-[11px] text-th-text-faint mt-0.5">Running</div>
            </div>
          </div>
        ))}
        {completed.map(a => (
          <div key={a.agent_id} className="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-th-surface opacity-60">
            <span className="mt-0.5 w-2 h-2 rounded-full bg-th-text-faint flex-shrink-0" />
            <div className="min-w-0 flex-1">
              <div className="text-xs text-th-text-muted truncate">{a.description}</div>
              <div className="text-[11px] text-th-text-faint mt-0.5">Completed</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function ProgressPanel({ progress, sessionId, onAnswer, className, subagents }: ProgressPanelProps) {
  const normalizedSnapshot = normalizeProgressSnapshot(progress.snapshot);

  return (
    <section className={className}>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.03fr)_minmax(0,0.97fr)] 2xl:grid-cols-2">
        <div className="space-y-6">
          <StatusBlock snapshot={normalizedSnapshot} run={progress.run} onAnswer={onAnswer} />
          {subagents && subagents.length > 0 && <SubAgentCards subagents={subagents} />}
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
