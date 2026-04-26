import { CCHOST_API } from "@/lib/config";
import type { ProgressResponse, RunResponse } from "@/lib/progress";
import type { JsonlEntry, Topic } from "@/lib/types";

export type GmailThread = {
  id: string;
  subject: string;
  sender: string;
  date: string;
  message_count: number;
  attachment_count: number;
  downloaded: boolean;
  score?: number;
  snippet?: string;
};

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

// ── Interrupt ──

export async function interruptSession(sessionId: string): Promise<void> {
  await fetch(`${CCHOST_API}/api/sessions/${sessionId}/interrupt`, {
    method: "POST",
  });
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

// ── Queue (send while busy) ──

export async function queueMessage(
  sessionId: string,
  message: string,
): Promise<{ status: string; was_busy: boolean }> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/queue`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    },
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// ── Sub-agents ──

export type SubAgent = {
  agent_id: string;
  description: string;
  status: "running" | "completed";
  last_activity: string;
};

export async function fetchSubAgents(
  sessionId: string,
): Promise<SubAgent[]> {
  try {
    const res = await fetch(
      `${CCHOST_API}/api/sessions/${sessionId}/subagents`,
    );
    if (!res.ok) return [];
    const data = (await res.json()) as { subagents?: SubAgent[] };
    return data.subagents || [];
  } catch {
    return [];
  }
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
  onProgress?: (fraction: number) => void,
): Promise<string[]> {
  const formData = new FormData();
  formData.append(file.name, file);

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${CCHOST_API}/api/sessions/${sessionId}/upload`);

    if (onProgress) {
      xhr.upload.addEventListener("progress", (e) => {
        if (e.lengthComputable) {
          onProgress(e.loaded / e.total);
        }
      });
    }

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText) as { uploaded?: string[] };
          resolve(data.uploaded || []);
        } catch {
          reject(new Error("Invalid JSON response"));
        }
      } else {
        reject(new Error(`Upload failed: HTTP ${xhr.status}`));
      }
    });

    xhr.addEventListener("error", () => reject(new Error("Upload network error")));
    xhr.addEventListener("abort", () => reject(new Error("Upload aborted")));

    xhr.send(formData);
  });
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

// ── Slash Commands ──

export type SlashCommand = {
  command: string;
  description: string;
};

export async function fetchCommands(): Promise<SlashCommand[]> {
  const res = await fetch(`${CCHOST_API}/api/commands`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.commands || [];
}

export async function runSlashCommand(
  sessionId: string,
  command: string,
): Promise<{ type: string; content: string }> {
  const res = await fetch(`${CCHOST_API}/api/sessions/${sessionId}/command`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command }),
  });
  if (!res.ok) throw new Error(`Command failed: HTTP ${res.status}`);
  return res.json();
}

// ── Silver-OAuth broker integration ──

const OAUTH_EMAIL_KEY = "silverOAuthEmail";

export function getSilverOAuthEmail(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(OAUTH_EMAIL_KEY) || "";
}

export function setSilverOAuthEmail(email: string): void {
  if (typeof window !== "undefined") window.localStorage.setItem(OAUTH_EMAIL_KEY, email);
}

export function clearSilverOAuthEmail(): void {
  if (typeof window !== "undefined") window.localStorage.removeItem(OAUTH_EMAIL_KEY);
}

/**
 * If the URL contains ?silver_oauth=<jwt> (broker handoff), POST it to the
 * backend to verify, stash the resulting email in localStorage, and strip the
 * param from the URL. Returns the email if captured, else null. Call once on
 * app startup (after the auth gate has confirmed the user is signed in).
 */
export async function captureSilverOAuthEmailFromUrl(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  const token = params.get("silver_oauth");
  if (!token) return null;
  // Strip immediately so we don't re-process on rerender.
  params.delete("silver_oauth");
  const qs = params.toString();
  const newUrl = window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash;
  window.history.replaceState({}, "", newUrl);
  try {
    const res = await fetch(`${CCHOST_API}/api/auth/verify-handoff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ token }),
    });
    if (!res.ok) return null;
    const data = (await res.json()) as { email: string };
    setSilverOAuthEmail(data.email);
    return data.email;
  } catch {
    return null;
  }
}

function silverOAuthHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const email = getSilverOAuthEmail();
  return email ? { ...extra, "X-Silver-OAuth-Email": email } : extra;
}

// ── Gmail / Inbox ──

export type SilverOAuthAccount = { email: string; scopes: string[]; updated_at?: number };

export async function fetchGmailStatus(): Promise<{
  connected: boolean;
  email?: string;
  accounts?: SilverOAuthAccount[];
}> {
  const res = await fetch(`${CCHOST_API}/api/auth/silver-oauth/status`);
  if (!res.ok) return { connected: false };
  const data = await res.json();
  if (!data.configured) return { connected: false };
  const accounts: SilverOAuthAccount[] = data.accounts ?? [];
  const stored = getSilverOAuthEmail();
  if (stored && accounts.some((a) => a.email === stored)) {
    return { connected: true, email: stored, accounts };
  }
  // Auto-pick the first account if nothing stored yet (single-user default).
  if (!stored && accounts.length > 0) {
    setSilverOAuthEmail(accounts[0].email);
    return { connected: true, email: accounts[0].email, accounts };
  }
  return { connected: false, accounts };
}

/** Returns the URL the browser should be sent to, to start an OAuth flow via the broker. */
export async function getGmailAuthUrl(returnUrl?: string): Promise<string> {
  const ru =
    returnUrl ??
    (typeof window !== "undefined" ? window.location.origin + window.location.pathname : "");
  const res = await fetch(
    `${CCHOST_API}/api/auth/silver-oauth/start-url?return_url=${encodeURIComponent(ru)}`,
  );
  if (!res.ok) throw new Error(`Failed to get auth URL: HTTP ${res.status}`);
  const data = await res.json();
  return data.url as string;
}

export async function scanGmail(query?: string): Promise<GmailThread[]> {
  const res = await fetch(`${CCHOST_API}/api/gmail/scan`, {
    method: "POST",
    headers: silverOAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query: query || "has:attachment newer_than:90d" }),
  });
  if (!res.ok) throw new Error(`Scan failed: HTTP ${res.status}`);
  return res.json();
}

export async function searchGmailSemantic(query: string, k = 20): Promise<GmailThread[]> {
  const res = await fetch(`${CCHOST_API}/api/gmail/semantic-search`, {
    method: "POST",
    headers: silverOAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ query, k }),
  });
  if (!res.ok) throw new Error(`Search failed: HTTP ${res.status}`);
  return res.json();
}

export type GmailPreviewMessage = {
  from: string;
  to: string;
  date: string;
  body_text: string;
};

export type GmailThreadPreview = {
  id: string;
  subject: string;
  messages: GmailPreviewMessage[];
  attachments: string[];
};

export async function fetchGmailThreadPreview(threadId: string): Promise<GmailThreadPreview> {
  const res = await fetch(
    `${CCHOST_API}/api/gmail/thread/${encodeURIComponent(threadId)}/preview`,
    { headers: silverOAuthHeaders() },
  );
  if (!res.ok) throw new Error(`Preview failed: HTTP ${res.status}`);
  return res.json();
}

export type DraftFromFileResponse = {
  draft_id: string;
  message: string;
  draft_url: string;
};

export async function createDraftFromFile(sessionId: string, path: string): Promise<DraftFromFileResponse> {
  const res = await fetch(`${CCHOST_API}/api/gmail/draft-from-file`, {
    method: "POST",
    headers: silverOAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ session_id: sessionId, path }),
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detail = String(j.detail);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function analyzeThread(threadId: string): Promise<{ session_id: string; run_id: string }> {
  const res = await fetch(`${CCHOST_API}/api/inbox/analyze/${threadId}`, {
    method: "POST",
    headers: silverOAuthHeaders(),
  });
  if (!res.ok) throw new Error(`Analyze failed: HTTP ${res.status}`);
  return res.json();
}

export async function createGmailDraft(sessionId: string, threadId: string): Promise<{ draft_id: string }> {
  const res = await fetch(`${CCHOST_API}/api/sessions/${sessionId}/gmail/draft`, {
    method: "POST",
    headers: silverOAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ thread_id: threadId }),
  });
  if (!res.ok) throw new Error(`Draft failed: HTTP ${res.status}`);
  return res.json();
}

// ── Terminal ──

export async function fetchTerminalOutput(
  sessionId: string,
  lines = 0,
): Promise<string> {
  const res = await fetch(
    `${CCHOST_API}/api/sessions/${sessionId}/terminal?lines=${lines}`,
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = (await res.json()) as { terminal?: string };
  return data.terminal || "";
}

// ── Topics ──

export type { Topic, TopicConversation } from "@/lib/types";

export async function fetchTopics(): Promise<Topic[]> {
  const res = await fetch(`${CCHOST_API}/api/topics`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Topic[];
}

export async function createTopic(name: string): Promise<Topic> {
  const res = await fetch(`${CCHOST_API}/api/topics`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`Failed to create topic: HTTP ${res.status}`);
  return (await res.json()) as Topic;
}

export async function fetchTopic(slug: string): Promise<Topic> {
  const res = await fetch(`${CCHOST_API}/api/topics/${encodeURIComponent(slug)}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Topic;
}

export async function deleteTopic(slug: string): Promise<void> {
  const res = await fetch(`${CCHOST_API}/api/topics/${encodeURIComponent(slug)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
}

export async function startTopicConversation(slug: string): Promise<{ session_id: string; conversation_id: string }> {
  const res = await fetch(`${CCHOST_API}/api/topics/${encodeURIComponent(slug)}/conversations`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`Failed to start conversation: HTTP ${res.status}`);
  return (await res.json()) as { session_id: string; conversation_id: string };
}

export async function resumeTopicConversation(slug: string, convId: string): Promise<{ session_id: string }> {
  const res = await fetch(
    `${CCHOST_API}/api/topics/${encodeURIComponent(slug)}/conversations/${encodeURIComponent(convId)}/resume`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Failed to resume conversation: HTTP ${res.status}`);
  return (await res.json()) as { session_id: string };
}
