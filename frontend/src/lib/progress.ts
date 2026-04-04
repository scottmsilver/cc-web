export type QuestionOptionResponse = {
  label: string;
  index: number;
  description?: string;
};

export type QuestionResponse = {
  question: string;
  options: QuestionOptionResponse[];
};

export type SendResponse = {
  text: string;
  is_question: boolean;
  questions: QuestionResponse[];
  role: string;
};

export type RunResponse = {
  run_id: string;
  session_id: string;
  status: "pending" | "running" | "waiting_for_input" | "completed" | "error";
  started_at: string;
  finished_at: string | null;
  result: SendResponse | null;
  error: string | null;
  waiting_for_input: boolean;
  current_question: QuestionResponse | null;
};

export type ProgressEventResponse = {
  kind: string;
  label: string;
  confidence: number;
  label_source: "explicit" | "inferred";
  text: string;
  tool_name: string;
  command: string;
  data: Record<string, unknown>;
  raw: Record<string, unknown>;
};

export type ProgressSnapshotResponse = {
  events: ProgressEventResponse[];
  background_count: number;
  primary_label: string | null;
  primary_confidence: number;
  primary_label_source: "explicit" | "inferred";
  milestones: string[];
  is_question: boolean;
  is_prompt: boolean;
};

export type ProgressResponse = {
  snapshot: ProgressSnapshotResponse;
  run: RunResponse | null;
  pending_question?: {
    question: string;
    options: { label: string; description?: string; index: number }[];
  } | null;
};

export type ProgressEventCategory =
  | "all"
  | "messages"
  | "tools"
  | "background"
  | "notifications"
  | "thinking"
  | "other";

export type ProgressSnapshotSummary = {
  primaryLabelText: string;
  confidenceText: string;
  backgroundText: string;
  statusText: string;
  activityText: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function toText(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    return String(value);
  }
  return "";
}

function toNumber(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  return fallback;
}

function normalizeLabelParts(value: string): string {
  return value
    .replace(/[_\-]+/g, " ")
    .replace(/\./g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();
}

function titleCase(value: string): string {
  if (!value) {
    return value;
  }

  return value
    .split(" ")
    .filter(Boolean)
    .map((part, index) => {
      const lower = part.toLowerCase();
      if (index === 0) {
        return lower.charAt(0).toUpperCase() + lower.slice(1);
      }
      return lower;
    })
    .join(" ");
}

function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function formatProgressLabel(value: string | null | undefined): string {
  const raw = toText(value).trim();
  if (!raw) {
    return "Unknown";
  }

  const looksMachineLike = /[_\-.]/.test(raw) || /[a-z][A-Z]/.test(raw) || raw === raw.toUpperCase();
  if (!looksMachineLike) {
    return raw;
  }

  const text = normalizeLabelParts(raw);
  if (!text) {
    return "Unknown";
  }
  return titleCase(text);
}

export function formatConfidence(confidence: number): string {
  const value = Math.max(0, Math.min(1, confidence));
  return `${Math.round(value * 100)}%`;
}

export function formatBackgroundCount(count: number): string {
  return pluralize(Math.max(0, count), "background task");
}

export function formatActivityLabel(snapshot: ProgressSnapshotResponse): string {
  if (snapshot.is_question) {
    return "Waiting for input";
  }
  if (snapshot.is_prompt) {
    return "Ready for input";
  }
  if (snapshot.primary_label) {
    return formatProgressLabel(snapshot.primary_label);
  }
  return "Working";
}

export function summarizeProgressSnapshot(snapshot: ProgressSnapshotResponse): ProgressSnapshotSummary {
  return {
    primaryLabelText: snapshot.primary_label ? formatProgressLabel(snapshot.primary_label) : "No primary activity",
    confidenceText: formatConfidence(snapshot.primary_confidence),
    backgroundText: formatBackgroundCount(snapshot.background_count),
    statusText: snapshot.is_question ? "waiting_for_input" : snapshot.is_prompt ? "prompt" : "running",
    activityText: formatActivityLabel(snapshot),
  };
}

export function getProgressEventCategory(event: ProgressEventResponse): ProgressEventCategory {
  if (event.kind.startsWith("queue.")) {
    return "background";
  }
  if (event.kind === "assistant.tool_use") {
    return "tools";
  }
  if (event.kind === "assistant.thinking") {
    return "thinking";
  }
  if (event.kind === "task.notification") {
    return "notifications";
  }
  if (event.kind === "assistant.text") {
    return "messages";
  }
  return "other";
}

export function normalizeProgressSnapshot(snapshot: ProgressSnapshotResponse): ProgressSnapshotResponse {
  return {
    ...snapshot,
    events: snapshot.events.map((event) => normalizeProgressEvent(event)),
    milestones: [...snapshot.milestones],
  };
}

export function normalizeProgressEvent(event: ProgressEventResponse): ProgressEventResponse {
  return {
    kind: event.kind,
    label: event.label,
    confidence: toNumber(event.confidence, 0),
    label_source: event.label_source === "explicit" ? "explicit" : "inferred",
    text: toText(event.text),
    tool_name: toText(event.tool_name),
    command: toText(event.command),
    data: isRecord(event.data) ? { ...event.data } : {},
    raw: isRecord(event.raw) ? { ...event.raw } : {},
  };
}

export function sortQuestionOptions(options: QuestionOptionResponse[]): QuestionOptionResponse[] {
  return [...options].sort((left, right) => left.index - right.index);
}

export function getProgressRunStatusText(run: RunResponse | null | undefined): string {
  if (!run) {
    return "No run";
  }
  if (run.status === "error" || run.error) {
    return "Error";
  }
  if (run.status === "waiting_for_input") {
    return "Waiting for input";
  }
  if (run.status === "pending") {
    return "Queued";
  }
  if (run.status === "completed") {
    return "Completed";
  }
  return "Running";
}

export function getProgressRunStatusTone(run: RunResponse | null | undefined): "neutral" | "warning" | "success" | "error" {
  if (!run) {
    return "neutral";
  }
  if (run.status === "error" || run.error) {
    return "error";
  }
  if (run.status === "waiting_for_input") {
    return "warning";
  }
  if (run.status === "completed") {
    return "success";
  }
  return "neutral";
}

export function getProgressEventLabel(event: ProgressEventResponse): string {
  if (event.label) {
    return formatProgressLabel(event.label);
  }
  return formatProgressLabel(event.kind);
}

export function getProgressEventSubtitle(event: ProgressEventResponse): string {
  if (event.kind === "assistant.tool_use" && event.tool_name) {
    return event.command ? `${event.tool_name}: ${event.command}` : event.tool_name;
  }
  if (event.text) {
    return event.text;
  }
  if (event.data && Object.keys(event.data).length > 0) {
    return "Structured event payload";
  }
  return event.kind;
}

export function getProgressEventBadgeText(event: ProgressEventResponse): string {
  const category = getProgressEventCategory(event);
  if (category === "background") {
    return "background";
  }
  if (category === "tools") {
    return "tool";
  }
  if (category === "notifications") {
    return "notice";
  }
  if (category === "thinking") {
    return "thinking";
  }
  if (category === "messages") {
    return "message";
  }
  return "event";
}

export function getProgressEventPreview(event: ProgressEventResponse): string {
  const subtitle = getProgressEventSubtitle(event);
  return subtitle.length > 120 ? `${subtitle.slice(0, 117)}...` : subtitle;
}
