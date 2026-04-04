import { CCHOST_API } from "@/lib/config";
import type { ProgressResponse, RunResponse } from "@/lib/progress";
import type { JsonlEntry } from "@/lib/types";

// ── Sessions ──

export async function fetchSessions(): Promise<unknown[]> {
  const res = await fetch(`${CCHOST_API}/api/sessions`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as unknown[];
}

export async function createSession(
  sessionId: string,
  workingDir: string,
): Promise<unknown> {
  const res = await fetch(`${CCHOST_API}/api/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, working_dir: workingDir }),
  });
  if (!res.ok) throw new Error(`Failed to create session: HTTP ${res.status}`);
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  await fetch(`${CCHOST_API}/api/sessions/${sessionId}`, { method: "DELETE" });
}

// ── Progress & Runs ──

export async function fetchProgress(
  sessionId: string,
): Promise<ProgressResponse> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/progress`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as ProgressResponse;
}

export async function fetchRun(
  sessionId: string,
  runId: string,
): Promise<RunResponse> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/runs/${runId}`,
  );
  if (!res.ok) throw new Error(`Failed to fetch run: HTTP ${res.status}`);
  return (await res.json()) as RunResponse;
}

export async function startRun(
  sessionId: string,
  message: string,
  timeout: number,
): Promise<RunResponse> {
  const res = await fetch(`${CCHOST_API}/api/sessions/${sessionId}/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, timeout }),
  });
  if (!res.ok) throw new Error(`Failed to start run: HTTP ${res.status}`);
  return (await res.json()) as RunResponse;
}

// ── Questions ──

export async function answerQuestion(
  sessionId: string,
  optionIndex: number,
): Promise<{ is_question?: boolean }> {
  const res = await fetch(`${CCHOST_API}/api/sessions/${sessionId}/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ option_index: optionIndex }),
  });
  if (!res.ok)
    throw new Error(`Failed to answer question: HTTP ${res.status}`);
  return res.json();
}

export async function toggleOption(
  sessionId: string,
  optionIndex: number,
): Promise<void> {
  await fetch(`${CCHOST_API}/api/sessions/${sessionId}/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ option_index: optionIndex }),
  });
}

export async function submitMultiSelect(sessionId: string): Promise<void> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/submit-multiselect`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

// ── Messages ──

export async function sendMessage(
  sessionId: string,
  message: string,
  timeout: number,
): Promise<RunResponse> {
  return startRun(sessionId, message, timeout);
}

// ── Files ──

export async function fetchFiles(
  sessionId: string,
): Promise<string[]> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/files`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const payload = (await res.json()) as { files?: string[] };
  return payload.files || [];
}

export async function readFile(
  sessionId: string,
  filePath: string,
): Promise<string> {
  const encodedPath = filePath.split('/').map(encodeURIComponent).join('/');
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/files/${encodedPath}`,
  );
  return (await res.text()).substring(0, 50000);
}

export function getFileUrl(sessionId: string, filePath: string): string {
  const encodedPath = filePath.split('/').map(encodeURIComponent).join('/');
  return `${CCHOST_API}/api/sessions/${sessionId}/files/${encodedPath}`;
}

export async function uploadFile(
  sessionId: string,
  file: File,
): Promise<string[]> {
  const formData = new FormData();
  formData.append(file.name, file);
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/upload`,
    { method: "POST", body: formData },
  );
  if (!res.ok) throw new Error(`Upload failed: HTTP ${res.status}`);
  const data = (await res.json()) as { uploaded?: string[] };
  return data.uploaded || [];
}

export async function uploadFiles(
  sessionId: string,
  files: FileList,
): Promise<string[]> {
  const formData = new FormData();
  Array.from(files).forEach((file) => formData.append(file.name, file));
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/upload`,
    { method: "POST", body: formData },
  );
  if (!res.ok) throw new Error(`Upload failed: HTTP ${res.status}`);
  const data = (await res.json()) as { uploaded?: string[] };
  return data.uploaded || [];
}

// ── Conversation ──

export async function fetchConversation(
  sessionId: string,
): Promise<{ conversation?: { role?: unknown; text?: unknown }[] }> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/conversation`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── JSONL ──

export async function fetchJsonl(
  sessionId: string,
): Promise<{ entries: JsonlEntry[]; path?: string }> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/jsonl`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Terminal ──

export async function fetchTerminalOutput(
  sessionId: string,
  lines = 80,
): Promise<string> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/terminal?lines=${lines}`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = (await res.json()) as { terminal?: string };
  return data.terminal || "";
}
