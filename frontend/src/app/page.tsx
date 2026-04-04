"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import ReactMarkdown from "react-markdown";

import { ChatInput } from "@/components/chat-input";
import { linkifyFiles } from "@/components/file-link";
import { ProgressPanel } from "@/components/progress-panel";
import type { ProgressResponse, RunResponse } from "@/lib/progress";

const CCHOST_API = "http://localhost:8420";
const DEFAULT_RUN_TIMEOUT_SECONDS = 900;
const POLL_INTERVAL_MS = 1200;
const POLL_FAILURE_LIMIT = 3;
const WORKING_DIR_ROOT = "/tmp/cchost-ui";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

type SessionRecord = {
  id: string;
  working_dir?: string | null;
};

type ConversationEntry = {
  role?: unknown;
  text?: unknown;
};

function makeMessageId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function generateSessionId(): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `session-${stamp}-${Math.random().toString(36).slice(2, 8)}`;
}

function getSessionWorkingDir(sessionId: string): string {
  return `${WORKING_DIR_ROOT}/${sessionId}`;
}

function normalizeSessionRecord(value: unknown): SessionRecord | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const id =
    typeof (value as { id?: unknown }).id === "string"
      ? (value as { id: string }).id
      : typeof (value as { session_id?: unknown }).session_id === "string"
        ? (value as { session_id: string }).session_id
        : null;

  if (!id) {
    return null;
  }

  return {
    id,
    working_dir:
      typeof (value as { working_dir?: unknown }).working_dir === "string"
        ? (value as { working_dir: string }).working_dir
        : null,
  };
}

function normalizeConversationEntry(entry: ConversationEntry, index: number): Message | null {
  const role = entry.role === "user" || entry.role === "assistant" ? entry.role : null;
  const content = typeof entry.text === "string" ? entry.text : "";

  if (!role || !content) {
    return null;
  }

  return {
    id: `${role}-${index}`,
    role,
    content,
  };
}

function createEmptyProgress(run: RunResponse): ProgressResponse {
  return {
    run,
    snapshot: {
      events: [],
      background_count: 0,
      primary_label: null,
      primary_confidence: 0,
      primary_label_source: "inferred",
      milestones: [],
      is_question: false,
      is_prompt: false,
    },
  };
}

function getRunText(run: RunResponse | null | undefined): string {
  return run?.result?.text?.trim() ?? "";
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isAnswering, setIsAnswering] = useState(false);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState("");
  const [selectedFile, setSelectedFile] = useState("");
  const [activeTab, setActiveTab] = useState<"chat" | "files">("chat");
  const [uploadDrag, setUploadDrag] = useState(false);
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const handledRunIdsRef = useRef<Set<string>>(new Set());
  const pollFailureCountRef = useRef(0);
  const skipConversationLoadForSessionRef = useRef<string | null>(null);

  const fetchSessions = useCallback(async () => {
    try {
      const response = await fetch(`${CCHOST_API}/api/sessions`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const payload = (await response.json()) as unknown[];
      const nextSessions = payload
        .map(normalizeSessionRecord)
        .filter((session): session is SessionRecord => Boolean(session));

      setSessions(nextSessions);
    } catch {}
  }, []);

  const fetchFiles = useCallback(async (sessionId: string) => {
    try {
      const response = await fetch(`${CCHOST_API}/api/sessions/${sessionId}/files`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const payload = (await response.json()) as { files?: string[] };
      setFiles(payload.files || []);
    } catch {}
  }, []);

  const fetchConversation = useCallback(async (sessionId: string) => {
    try {
      const response = await fetch(`${CCHOST_API}/api/sessions/${sessionId}/conversation`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const payload = (await response.json()) as { conversation?: ConversationEntry[] };
      const nextMessages = (payload.conversation ?? [])
        .map(normalizeConversationEntry)
        .filter((message): message is Message => Boolean(message));

      setMessages(nextMessages);
    } catch {
      setMessages([]);
    }
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, progress]);

  useEffect(() => {
    void fetchSessions();
    const intervalId = window.setInterval(() => {
      void fetchSessions();
    }, 10000);
    return () => window.clearInterval(intervalId);
  }, [fetchSessions]);

  useEffect(() => {
    if (!activeSession) {
      setFiles([]);
      setMessages([]);
      setSelectedFile("");
      setFileContent("");
      setProgress(null);
      setActiveRunId(null);
      setIsAnswering(false);
      return;
    }

    setSelectedFile("");
    setFileContent("");
    setIsAnswering(false);

    if (skipConversationLoadForSessionRef.current === activeSession) {
      skipConversationLoadForSessionRef.current = null;
    } else {
      void fetchConversation(activeSession);
    }

    void fetchFiles(activeSession);
    const intervalId = window.setInterval(() => {
      void fetchFiles(activeSession);
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, [activeSession, fetchConversation, fetchFiles]);

  useEffect(() => {
    if (!activeSession) {
      return;
    }

    let cancelled = false;

    const loadProgress = async () => {
      try {
        const response = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/progress`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const nextProgress = (await response.json()) as ProgressResponse;
        if (cancelled) {
          return;
        }

        setProgress(nextProgress);
        pollFailureCountRef.current = 0;

        if (nextProgress.run && ["pending", "running", "waiting_for_input"].includes(nextProgress.run.status)) {
          setActiveRunId(nextProgress.run.run_id);
          setIsLoading(true);
          setIsAnswering(false);
        } else {
          setActiveRunId(null);
          setIsLoading(false);
          setIsAnswering(false);
        }
      } catch {
        if (!cancelled) {
          setProgress(null);
          setActiveRunId(null);
          setIsLoading(false);
          setIsAnswering(false);
        }
      }
    };

    void loadProgress();

    return () => {
      cancelled = true;
    };
  }, [activeSession]);

  useEffect(() => {
    if (!activeSession || !activeRunId) {
      return;
    }

    let cancelled = false;

    const appendAssistantMessage = (run: RunResponse, fallbackText: string) => {
      if (handledRunIdsRef.current.has(run.run_id)) {
        return;
      }

      handledRunIdsRef.current.add(run.run_id);
      const content = fallbackText || getRunText(run) || "Run completed with no assistant output.";
      setMessages((prev) => [...prev, { id: makeMessageId("assistant"), role: "assistant", content }]);
    };

    const poll = async () => {
      try {
        const [progressResponse, runResponse] = await Promise.all([
          fetch(`${CCHOST_API}/api/sessions/${activeSession}/progress`),
          fetch(`${CCHOST_API}/api/sessions/${activeSession}/runs/${activeRunId}`),
        ]);

        if (!progressResponse.ok) {
          throw new Error(`Failed to fetch progress: HTTP ${progressResponse.status}`);
        }
        if (!runResponse.ok) {
          throw new Error(`Failed to fetch run: HTTP ${runResponse.status}`);
        }

        const nextProgress = (await progressResponse.json()) as ProgressResponse;
        const run = (await runResponse.json()) as RunResponse;

        if (cancelled) {
          return;
        }

        pollFailureCountRef.current = 0;
        setProgress({
          ...nextProgress,
          run,
        });

        if (run.status !== "waiting_for_input") {
          setIsAnswering(false);
        }

        if (run.status === "completed") {
          appendAssistantMessage(run, "");
          setIsLoading(false);
          setIsAnswering(false);
          setActiveRunId(null);
          void fetchSessions();
          void fetchFiles(activeSession);
          return;
        }

        if (run.status === "error") {
          appendAssistantMessage(run, `Error: ${run.error || "Run failed."}`);
          setIsLoading(false);
          setIsAnswering(false);
          setActiveRunId(null);
        }
      } catch (error) {
        if (cancelled) {
          return;
        }

        pollFailureCountRef.current += 1;
        if (pollFailureCountRef.current < POLL_FAILURE_LIMIT) {
          return;
        }

        const message = error instanceof Error ? error.message : "Failed to poll progress.";
        setMessages((prev) => [
          ...prev,
          { id: makeMessageId("assistant"), role: "assistant", content: `Error: ${message}` },
        ]);
        setIsLoading(false);
        setIsAnswering(false);
        setActiveRunId(null);
      }
    };

    void poll();
    const intervalId = window.setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [activeRunId, activeSession, fetchFiles, fetchSessions]);

  const readFile = async (path: string) => {
    if (!activeSession) {
      return;
    }

    try {
      const response = await fetch(
        `${CCHOST_API}/api/sessions/${activeSession}/files/${encodeURIComponent(path)}`,
      );
      setFileContent((await response.text()).substring(0, 50000));
      setSelectedFile(path);
    } catch (error: unknown) {
      setFileContent(`Error: ${error instanceof Error ? error.message : "Failed to read file."}`);
    }
  };

  const downloadFile = (path: string) => {
    if (!activeSession) {
      return;
    }

    window.open(`${CCHOST_API}/api/sessions/${activeSession}/files/${encodeURIComponent(path)}`, "_blank");
  };

  const uploadFiles = async (fileList: FileList) => {
    if (!activeSession) {
      alert("Send a message first to create a session, then upload files.");
      return;
    }

    const formData = new FormData();
    Array.from(fileList).forEach((file) => formData.append(file.name, file));

    try {
      const response = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/upload`, {
        method: "POST",
        body: formData,
      });
      const data = (await response.json()) as { uploaded?: string[] };
      const uploaded = data.uploaded ?? [];
      if (uploaded.length) {
        setMessages((prev) => [
          ...prev,
          {
            id: makeMessageId("assistant"),
            role: "assistant",
            content: `Uploaded ${uploaded.length} file(s): ${uploaded.join(", ")}`,
          },
        ]);
        void fetchFiles(activeSession);
      }
    } catch (error: unknown) {
      alert(`Upload failed: ${error instanceof Error ? error.message : "Unknown error"}`);
    }
  };

  const ensureSession = async (): Promise<string> => {
    if (activeSession) {
      return activeSession;
    }

    const sessionId = generateSessionId();
    const response = await fetch(`${CCHOST_API}/api/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        working_dir: getSessionWorkingDir(sessionId),
      }),
    });

    if (!response.ok) {
      throw new Error(`Failed to create session: HTTP ${response.status}`);
    }

    const payload =
      normalizeSessionRecord(await response.json()) ?? { id: sessionId, working_dir: getSessionWorkingDir(sessionId) };
    skipConversationLoadForSessionRef.current = payload.id;
    setActiveSession(payload.id);
    setSessions((prev) => {
      if (prev.some((session) => session.id === payload.id)) {
        return prev;
      }
      return [payload, ...prev];
    });
    return payload.id;
  };

  const startRun = async (sessionId: string, message: string) => {
    const response = await fetch(`${CCHOST_API}/api/sessions/${sessionId}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, timeout: DEFAULT_RUN_TIMEOUT_SECONDS }),
    });

    if (!response.ok) {
      throw new Error(`Failed to start run: HTTP ${response.status}`);
    }

    const run = (await response.json()) as RunResponse;
    handledRunIdsRef.current.delete(run.run_id);
    pollFailureCountRef.current = 0;
    setProgress(createEmptyProgress(run));
    setActiveRunId(run.run_id);
    setIsAnswering(false);
    setIsLoading(true);
  };

  const sendMessage = async (messageText: string) => {
    const message = messageText.trim();
    if (!message || isLoading) {
      return;
    }

    setMessages((prev) => [...prev, { id: makeMessageId("user"), role: "user", content: message }]);
    setIsLoading(true);

    try {
      const sessionId = await ensureSession();
      await startRun(sessionId, message);
      setActiveTab("chat");
    } catch (error: unknown) {
      const content = error instanceof Error ? `Error: ${error.message}` : "Error: Failed to start run.";
      setMessages((prev) => [...prev, { id: makeMessageId("assistant"), role: "assistant", content }]);
      setIsLoading(false);
    }
  };

  const answerQuestion = async (optionIndex: number) => {
    if (!activeSession || !progress?.run || isAnswering) {
      return;
    }

    setIsAnswering(true);
    setIsLoading(true);

    try {
      const response = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ option_index: optionIndex }),
      });

      if (!response.ok) {
        throw new Error(`Failed to answer question: HTTP ${response.status}`);
      }

      setProgress((prev) =>
        prev
          ? {
              ...prev,
              run: prev.run
                ? {
                    ...prev.run,
                    status: "running",
                    waiting_for_input: false,
                    current_question: null,
                  }
                : prev.run,
              snapshot: {
                ...prev.snapshot,
                is_question: false,
              },
            }
          : prev,
      );
      pollFailureCountRef.current = 0;
      setActiveRunId(progress.run.run_id);
    } catch (error: unknown) {
      const content =
        error instanceof Error ? `Error: ${error.message}` : "Error: Failed to answer question.";
      setMessages((prev) => [...prev, { id: makeMessageId("assistant"), role: "assistant", content }]);
      setIsLoading(false);
      setIsAnswering(false);
    }
  };

  const startNewSessionDraft = () => {
    skipConversationLoadForSessionRef.current = null;
    pollFailureCountRef.current = 0;
    setActiveSession(null);
    setMessages([]);
    setFiles([]);
    setSelectedFile("");
    setFileContent("");
    setProgress(null);
    setActiveRunId(null);
    setActiveTab("chat");
    setIsLoading(false);
    setIsAnswering(false);
  };

  const isBinary = (path: string) => /\.(pdf|xlsx|xls|zip|png|jpg|gif)$/i.test(path);
  const showProgressPanel = progress !== null;

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setUploadDrag(false);
    if (e.dataTransfer.files.length) {
      void uploadFiles(e.dataTransfer.files);
    }
  };

  return (
    <div
      className="flex h-screen bg-[#1a1a1a] text-[#f0ece8]"
      onDragOver={(e) => {
        e.preventDefault();
        setUploadDrag(true);
      }}
      onDragLeave={() => setUploadDrag(false)}
      onDrop={handleDrop}
    >
      {uploadDrag && (
        <div className="fixed inset-0 z-50 flex items-center justify-center border-4 border-dashed border-[#d77757] bg-[#d77757]/20">
          <p className="text-2xl font-semibold text-[#d77757]">Drop files to upload</p>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => e.target.files && void uploadFiles(e.target.files)}
      />

      <div className="flex w-64 flex-col border-r border-[#3a3a3a]">
        <div className="border-b border-[#3a3a3a] p-4">
          <h1 className="text-lg font-semibold text-[#d77757]">cchost</h1>
          <p className="text-xs text-[#8a8580]">Claude Code Chat</p>
        </div>
        <div className="border-b border-[#3a3a3a] p-3">
          <button
            type="button"
            onClick={startNewSessionDraft}
            className="w-full rounded-lg border border-[#444444] px-3 py-2 text-left text-sm text-[#d4d0cc] transition-colors hover:border-[#d77757] hover:text-[#e8946e]"
          >
            New session
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          <h2 className="mb-2 text-xs font-medium uppercase tracking-wider text-[#8a8580]">Sessions</h2>
          {sessions.length === 0 ? (
            <p className="text-xs text-[#6a6560]">No active sessions</p>
          ) : (
            sessions.map((session) => (
              <button
                key={session.id}
                onClick={() => {
                  setActiveSession(session.id);
                  setActiveTab("files");
                }}
                className={`mb-1 w-full truncate rounded-lg px-3 py-2 text-left text-sm ${
                  activeSession === session.id ? "bg-[#2d2d2d] text-[#f0ece8]" : "text-[#a09a94] hover:bg-[#232323]"
                }`}
              >
                {session.id}
              </button>
            ))
          )}
        </div>
      </div>

      <div className="flex flex-1 flex-col">
        <div className="flex items-center border-b border-[#3a3a3a]">
          <button
            onClick={() => setActiveTab("chat")}
            className={`px-6 py-3 text-sm font-medium ${
              activeTab === "chat" ? "border-b-2 border-[#d77757] text-[#d77757]" : "text-[#8a8580] hover:text-[#d4d0cc]"
            }`}
          >
            Chat
          </button>
          <button
            onClick={() => setActiveTab("files")}
            className={`px-6 py-3 text-sm font-medium ${
              activeTab === "files" ? "border-b-2 border-[#d77757] text-[#d77757]" : "text-[#8a8580] hover:text-[#d4d0cc]"
            }`}
          >
            Files{files.length > 0 && ` (${files.length})`}
          </button>
          <div className="ml-auto flex items-center gap-3 pr-4">
            <span className="text-xs text-[#8a8580]">{activeSession ? `Session: ${activeSession}` : "Draft session"}</span>
            <button
              onClick={() => fileInputRef.current?.click()}
              className="rounded-lg border border-[#444444] px-3 py-1.5 text-xs text-[#8a8580] transition-colors hover:border-[#d77757] hover:text-[#d77757]"
            >
              Upload File
            </button>
          </div>
        </div>

        {activeTab === "chat" && (
          <div className="flex flex-1 flex-col">
            <div className="flex-1 space-y-4 overflow-y-auto p-4">
              {messages.length === 0 && !showProgressPanel && (
                <div className="flex h-full items-center justify-center">
                  <div className="text-center">
                    <p className="mb-2 text-2xl font-light text-[#a09a94]">Claude Code</p>
                    <p className="mb-4 text-sm text-[#6a6560]">Send a message or drop a file to start</p>
                  </div>
                </div>
              )}

              {messages.map((message) => (
                <div key={message.id} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
                      message.role === "user" ? "bg-[#d77757]/20 text-[#f0ece8]" : "bg-[#2d2d2d] text-[#e8e4df]"
                    }`}
                  >
                    {message.role === "assistant" ? (
                      <div className="prose-chat">
                        <ReactMarkdown
                          components={{
                            // Make code blocks with file paths clickable
                            code: ({ children, className }) => {
                              const text = String(children).trim();
                              const isFilePath = /\.\w{2,4}$/.test(text) && !className;
                              if (isFilePath && activeSession) {
                                return (
                                  <a
                                    href={`${CCHOST_API}/api/sessions/${activeSession}/files/${encodeURIComponent(text)}`}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[#2d2d2d] hover:bg-[#333333] text-[#d77757] hover:text-[#e8946e] text-xs font-mono no-underline border border-[#444444] hover:border-[#d77757]/50"
                                  >
                                    {text} <span className="opacity-40 text-[10px]">↓</span>
                                  </a>
                                );
                              }
                              return <code className={className}>{children}</code>;
                            },
                          }}
                        >
                          {message.content}
                        </ReactMarkdown>
                      </div>
                    ) : (
                      <pre className="whitespace-pre-wrap font-sans text-sm">{message.content}</pre>
                    )}
                  </div>
                </div>
              ))}

              {isLoading && (
                <div className="flex justify-start">
                  <div className="flex items-center gap-2 rounded-2xl bg-[#2d2d2d] px-4 py-3 text-sm text-[#a09a94]">
                    <span className="inline-block w-2 h-2 rounded-full bg-[#d77757] animate-pulse" />
                    {progress?.snapshot?.primary_label
                      ? `${progress.snapshot.primary_label}...`
                      : "Claude is working..."}
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
            <ChatInput
              onSend={sendMessage}
              disabled={isLoading}
              sessionId={activeSession}
              ensureSession={ensureSession}
              onFilesUploaded={() => activeSession && fetchFiles(activeSession)}
            />
          </div>
        )}

        {activeTab === "files" && (
          <div className="flex flex-1">
            <div className="w-72 overflow-y-auto border-r border-[#3a3a3a] p-3">
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-[#8a8580]">Files</h3>
              {!activeSession ? (
                <p className="text-xs text-[#6a6560]">Send a message to create a session</p>
              ) : files.length === 0 ? (
                <p className="text-xs text-[#6a6560]">No files yet. Upload or ask Claude to create some.</p>
              ) : (
                files.map((file) => (
                  <div
                    key={file}
                    className={`flex cursor-pointer items-center gap-1 truncate rounded px-2 py-1.5 text-xs font-mono ${
                      selectedFile === file ? "bg-[#2d2d2d] text-[#d77757]" : "text-[#a09a94] hover:bg-[#232323]"
                    }`}
                  >
                    <button
                      onClick={() => (isBinary(file) ? downloadFile(file) : void readFile(file))}
                      className="flex-1 truncate text-left"
                    >
                      {file}
                    </button>
                    <button
                      onClick={() => downloadFile(file)}
                      title="Download"
                      className="flex-shrink-0 text-[#6a6560] hover:text-[#d77757]"
                    >
                      ↓
                    </button>
                  </div>
                ))
              )}
            </div>
            <div className="flex-1 overflow-auto p-4">
              {selectedFile ? (
                <>
                  <div className="mb-3 flex items-center gap-3">
                    <h3 className="text-sm font-medium text-[#a09a94]">{selectedFile}</h3>
                    <button
                      onClick={() => downloadFile(selectedFile)}
                      className="rounded border border-[#444444] px-2 py-1 text-xs text-[#8a8580] hover:border-[#d77757] hover:text-[#d77757]"
                    >
                      Download
                    </button>
                  </div>
                  <pre className="whitespace-pre-wrap rounded-lg bg-[#232323] p-4 text-xs font-mono text-[#d4d0cc]">{fileContent}</pre>
                </>
              ) : (
                <p className="text-sm text-[#6a6560]">Click a file to view. Binary files (PDF, XLSX) will download directly.</p>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Right drawer: Progress / Debug panel */}
      {showProgressPanel && progress && (
        <div className="w-80 border-l border-[#3a3a3a] flex flex-col bg-[#1a1a1a] overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-[#3a3a3a]">
            <h2 className="text-xs font-medium uppercase tracking-wider text-[#8a8580]">Activity</h2>
            <button
              onClick={() => setProgress(null)}
              className="text-[#6a6560] hover:text-[#a09a94] text-xs"
            >
              Close
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-3">
            <ProgressPanel progress={progress} onAnswer={isAnswering ? undefined : answerQuestion} />
          </div>
        </div>
      )}
    </div>
  );
}
