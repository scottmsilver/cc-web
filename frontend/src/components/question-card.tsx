"use client";

import { sortQuestionOptions, getProgressRunStatusText, type RunResponse } from "@/lib/progress";

type QuestionCardProps = {
  run: RunResponse | null;
  onAnswer?: (optionIndex: number) => void;
  className?: string;
};

export function QuestionCard({ run, onAnswer, className }: QuestionCardProps) {
  if (!run?.waiting_for_input) {
    return null;
  }

  const question = run.current_question ?? run.result?.questions[0] ?? null;
  const options = sortQuestionOptions(question?.options ?? []);

  return (
    <section className={className}>
      <div className="rounded-xl border border-amber-300 bg-amber-50 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-amber-700">Question</p>
            <h3 className="mt-1 text-lg font-semibold text-th-text">
              {question?.question || "Waiting for a response"}
            </h3>
          </div>
          <span className="rounded-full border border-amber-400 bg-amber-100 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider text-amber-700">
            {getProgressRunStatusText(run)}
          </span>
        </div>

        <div className="mt-4 space-y-2">
          {options.length === 0 ? (
            <p className="rounded-lg border border-dashed border-amber-300 bg-amber-100/50 px-4 py-3 text-sm text-amber-800">
              The model is waiting for input, but no answer options were provided.
            </p>
          ) : !onAnswer ? (
            <p className="rounded-lg border border-dashed border-th-border bg-th-surface px-4 py-3 text-sm text-th-text-muted">
              Answer buttons are read-only until an answer handler is connected.
            </p>
          ) : (
            options.map((option) => (
              <button
                key={option.index}
                type="button"
                onClick={() => onAnswer(option.index)}
                className="flex w-full items-center justify-between gap-3 rounded-lg border border-th-border bg-th-bg px-4 py-3 text-left transition hover:border-th-accent hover:bg-th-surface"
              >
                <span className="min-w-0">
                  <span className="block text-[11px] font-semibold uppercase tracking-wider text-th-text-muted">
                    Option {option.index}
                  </span>
                  <span className="mt-1 block text-sm text-th-text">{option.label}</span>
                </span>
                <span className="shrink-0 rounded-full border border-th-accent bg-th-surface px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider text-th-accent">
                  Send
                </span>
              </button>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
