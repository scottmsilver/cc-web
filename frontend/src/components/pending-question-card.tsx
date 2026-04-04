"use client";

import { useState } from "react";

import {
  answerQuestion as apiAnswer,
  toggleOption as apiToggle,
  submitMultiSelect as apiSubmitMulti,
} from "@/lib/api";
import type { PendingQuestion } from "@/lib/types";

function isMultiSelect(options: PendingQuestion["options"]): boolean {
  return options.some((o) => o.label.startsWith("[ ]") || o.label.startsWith("[✔]"));
}

function cleanLabel(label: string): string {
  return label.replace(/^\[[ ✔]\]\s*/, "").trim();
}

function isChecked(label: string): boolean {
  return label.startsWith("[✔]");
}

function isSubmitOption(label: string): boolean {
  const clean = cleanLabel(label).toLowerCase();
  return clean === "submit" || clean.includes("submit");
}

export function PendingQuestionCard({
  question,
  sessionId,
  disabled,
  onAnswered,
}: {
  question: PendingQuestion;
  sessionId: string | null;
  disabled: boolean;
  onAnswered: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const multi = isMultiSelect(question.options);
  // Local checkbox state for optimistic UI
  const [localChecked, setLocalChecked] = useState<Record<number, boolean>>(() => {
    const initial: Record<number, boolean> = {};
    for (const opt of question.options) {
      initial[opt.index] = isChecked(opt.label);
    }
    return initial;
  });

  const handleSingleSelect = async (optionIndex: number) => {
    if (!sessionId || busy) return;
    setSelectedIndex(optionIndex);
    setBusy(true);
    try {
      await apiAnswer(sessionId, optionIndex);
      await onAnswered();
    } catch (error) {
      console.warn("Failed to answer question:", error);
    } finally {
      setBusy(false);
    }
  };

  const handleToggle = async (optionIndex: number) => {
    if (!sessionId || busy) return;
    // Optimistic UI update
    setLocalChecked((prev) => ({ ...prev, [optionIndex]: !prev[optionIndex] }));
    setBusy(true);
    try {
      await apiToggle(sessionId, optionIndex);
    } catch (error) {
      console.warn("Failed to toggle option:", error);
      // Revert on failure
      setLocalChecked((prev) => ({ ...prev, [optionIndex]: !prev[optionIndex] }));
    } finally {
      setBusy(false);
    }
  };

  const handleSubmitMulti = async () => {
    if (!sessionId || busy) return;
    setBusy(true);
    try {
      await apiSubmitMulti(sessionId);
      await onAnswered();
    } catch (error) {
      console.warn("Failed to submit multi-select:", error);
    } finally {
      setBusy(false);
    }
  };

  const isDisabled = disabled || busy;

  return (
    <div className="flex justify-start">
      <div className="max-w-[80%] rounded-xl border border-th-warning-border bg-th-warning-bg p-4">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-th-warning-text mb-2">
          {multi ? "Select all that apply" : "Choose one"}
        </p>
        <p className="text-sm text-th-text mb-3">{question.question}</p>
        <div className="space-y-2">
          {question.options
            .filter((opt) => !isSubmitOption(opt.label))
            .map((opt) => {
              const checked = localChecked[opt.index] ?? isChecked(opt.label);
              const label = cleanLabel(opt.label);

              if (multi) {
                return (
                  <button
                    key={opt.index}
                    type="button"
                    disabled={isDisabled}
                    onClick={() => void handleToggle(opt.index)}
                    className={`flex w-full items-center gap-3 rounded-lg border px-4 py-3 text-left transition disabled:opacity-50 ${
                      checked
                        ? "border-th-accent bg-th-surface"
                        : "border-th-border bg-th-bg hover:border-th-accent hover:bg-th-surface"
                    }`}
                  >
                    <span className={`flex-shrink-0 w-5 h-5 rounded border-2 flex items-center justify-center text-xs ${
                      checked ? "border-th-accent bg-th-accent text-white" : "border-th-text-faint"
                    }`}>
                      {checked ? "\u2713" : ""}
                    </span>
                    <span>
                      <span className="block text-sm text-th-text">{label}</span>
                      {opt.description && <span className="mt-0.5 block text-xs text-th-text-muted">{opt.description}</span>}
                    </span>
                  </button>
                );
              }

              const isSelected = selectedIndex === opt.index;
              return (
                <button
                  key={opt.index}
                  type="button"
                  disabled={isDisabled}
                  onClick={() => void handleSingleSelect(opt.index)}
                  className={`flex w-full items-center justify-between gap-3 rounded-lg border px-4 py-3 text-left transition disabled:opacity-50 ${
                    isSelected
                      ? "border-th-accent bg-th-surface ring-1 ring-th-accent/30"
                      : "border-th-border bg-th-bg hover:border-th-accent hover:bg-th-surface"
                  }`}
                >
                  <span>
                    <span className="block text-sm text-th-text">{label}</span>
                    {opt.description && <span className="mt-0.5 block text-xs text-th-text-muted">{opt.description}</span>}
                  </span>
                  {isSelected && <span className="text-th-accent text-xs font-medium">Sending...</span>}
                </button>
              );
            })}
        </div>
        {multi && (
          <button
            type="button"
            disabled={isDisabled}
            onClick={() => void handleSubmitMulti()}
            className="mt-3 w-full rounded-lg bg-th-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-th-accent-hover disabled:opacity-50"
          >
            Submit
          </button>
        )}
      </div>
    </div>
  );
}
