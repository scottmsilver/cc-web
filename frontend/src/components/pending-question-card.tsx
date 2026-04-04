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
      <div className="max-w-[80%] rounded-xl border border-amber-300 bg-amber-50 p-4">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-amber-700 mb-2">
          {multi ? "Select all that apply" : "Choose one"}
        </p>
        <p className="text-sm text-gray-900 mb-3">{question.question}</p>
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
                        ? "border-[var(--th-accent)] bg-orange-50"
                        : "border-gray-300 bg-white hover:border-[var(--th-accent)] hover:bg-orange-50"
                    }`}
                  >
                    <span className={`flex-shrink-0 w-5 h-5 rounded border-2 flex items-center justify-center text-xs ${
                      checked ? "border-[var(--th-accent)] bg-[var(--th-accent)] text-white" : "border-gray-400"
                    }`}>
                      {checked ? "\u2713" : ""}
                    </span>
                    <span>
                      <span className="block text-sm text-gray-800">{label}</span>
                      {opt.description && <span className="mt-0.5 block text-xs text-gray-500">{opt.description}</span>}
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
                      ? "border-[var(--th-accent)] bg-orange-50 ring-1 ring-[var(--th-accent)]/30"
                      : "border-gray-300 bg-white hover:border-[var(--th-accent)] hover:bg-orange-50"
                  }`}
                >
                  <span>
                    <span className="block text-sm text-gray-800">{label}</span>
                    {opt.description && <span className="mt-0.5 block text-xs text-gray-500">{opt.description}</span>}
                  </span>
                  {isSelected && <span className="text-[var(--th-accent)] text-xs font-medium">Sending...</span>}
                </button>
              );
            })}
        </div>
        {multi && (
          <button
            type="button"
            disabled={isDisabled}
            onClick={() => void handleSubmitMulti()}
            className="mt-3 w-full rounded-lg bg-[var(--th-accent)] px-4 py-2.5 text-sm font-medium text-white transition hover:bg-[var(--th-accent-hover)] disabled:opacity-50"
          >
            Submit
          </button>
        )}
      </div>
    </div>
  );
}
