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
      <div className="rounded-3xl border border-amber-400/20 bg-amber-400/10 p-4 shadow-[0_20px_60px_rgba(0,0,0,0.14)] backdrop-blur">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.24em] text-amber-200/80">Question</p>
            <h3 className="mt-1 text-lg font-semibold text-white">
              {question?.question || "Waiting for a response"}
            </h3>
          </div>
          <span className="rounded-full border border-amber-300/20 bg-amber-300/10 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.16em] text-amber-100">
            {getProgressRunStatusText(run)}
          </span>
        </div>

        <div className="mt-4 space-y-2">
          {options.length === 0 ? (
            <p className="rounded-2xl border border-dashed border-amber-300/20 bg-black/15 px-4 py-3 text-sm text-amber-50/80">
              The model is waiting for input, but no answer options were provided.
            </p>
          ) : !onAnswer ? (
            <p className="rounded-2xl border border-dashed border-white/10 bg-black/10 px-4 py-3 text-sm text-zinc-400">
              Answer buttons are read-only until an answer handler is connected.
            </p>
          ) : (
            options.map((option) => (
              <button
                key={option.index}
                type="button"
                onClick={() => onAnswer(option.index)}
                className="flex w-full items-center justify-between gap-3 rounded-2xl border border-white/10 bg-black/20 px-4 py-3 text-left transition hover:border-amber-300/30 hover:bg-black/30"
              >
                <span className="min-w-0">
                  <span className="block text-[11px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
                    Option {option.index}
                  </span>
                  <span className="mt-1 block text-sm text-zinc-100">{option.label}</span>
                </span>
                <span className="shrink-0 rounded-full border border-amber-300/20 bg-amber-300/10 px-2.5 py-1 text-[11px] font-medium uppercase tracking-[0.14em] text-amber-100">
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
