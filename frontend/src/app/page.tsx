"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ChatInput } from "@/components/chat-input";
import { ProgressPanel } from "@/components/progress-panel";
import { ArtifactsPane } from "@/components/artifacts-pane";
import { ImageGalleryViewer } from "@/components/image-gallery-viewer";
import { ResizablePanel } from "@/components/resizable-panel";
import { themes, getTheme, applyTheme, loadSavedThemeId } from "@/lib/themes";
import { FileViewer } from "@/components/file-viewer";
import { JsonlChat } from "@/components/jsonl-chat";
import { PendingQuestionCard } from "@/components/pending-question-card";
import { QuestionCard } from "@/components/question-card";
import { TerminalView } from "@/components/terminal-view";
import { GmailPicker, type SelectedThread, type SuggestedSearch } from "@/components/gmail-picker";
import type { GmailDownload } from "@/components/chat-input";
import { DraftExportButtons } from "@/components/draft-export-buttons";
import { SessionSelector } from "@/components/session-selector";
import { TabBar, type TabId } from "@/components/tab-bar";
import { isBinaryFile } from "@/lib/config";
import { CCHOST_API } from "@/lib/config";
import {
  fetchGmailStatus,
  fetchSessions as apiFetchSessions,
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  fetchProgress as apiFetchProgress,
  fetchRun as apiFetchRun,
  startRun as apiStartRun,
  answerQuestion as apiAnswerQuestion,
  fetchFiles as apiFetchFiles,
  fetchConversation as apiFetchConversation,
  uploadFiles as apiUploadFiles,
  runSlashCommand as apiRunSlashCommand,
  getFileUrl,
} from "@/lib/api";
import type { ProgressResponse, RunResponse } from "@/lib/progress";

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
  title?: string;
  status?: string;
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

  const v = value as Record<string, unknown>;
  return {
    id,
    working_dir: typeof v.working_dir === "string" ? v.working_dir : null,
    title: typeof v.title === "string" ? v.title : undefined,
    status: typeof v.status === "string" ? v.status : undefined,
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
  const [sendError, setSendError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isAnswering, setIsAnswering] = useState(false);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("chat");
  const [viewingFile, setViewingFile] = useState<string | null>(null);
  const [viewingImages, setViewingImages] = useState<{ images: string[]; index: number } | null>(null);
  const [pendingMessage, setPendingMessage] = useState<string | null>(null);
  const [draftInput, setDraftInput] = useState("");
  const [themeId, setThemeId] = useState("light");
  const [showSettings, setShowSettings] = useState(false);
  const [uploadDrag, setUploadDrag] = useState(false);
  const [showGmailPicker, setShowGmailPicker] = useState(false);
  const [gmailThreadIds, setGmailThreadIds] = useState<string[]>([]);
  const [gmailConnected, setGmailConnected] = useState(false);
  const [gmailDownloads, setGmailDownloads] = useState<GmailDownload[]>([]);
  const [gmailSuggestions, setGmailSuggestions] = useState<SuggestedSearch[]>([]);
  const [gmailSuggestionsLoading, setGmailSuggestionsLoading] = useState(false);
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [commandResult, setCommandResult] = useState<string | null>(null);
  const [jsonlRefreshKey, setJsonlRefreshKey] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const handledRunIdsRef = useRef<Set<string>>(new Set());
  const pollFailureCountRef = useRef(0);
  const skipConversationLoadForSessionRef = useRef<string | null>(null);
  const activeSessionRef = useRef<string | null>(null);
  const urlInitializedRef = useRef(false);
  const progressRef = useRef<ProgressResponse | null>(null);

  // Keep progressRef in sync so answerQuestion avoids stale closure
  useEffect(() => {
    progressRef.current = progress;
  }, [progress]);

  const fetchSessions = useCallback(async () => {
    try {
      const payload = await apiFetchSessions();
      const nextSessions = (payload as unknown[])
        .map(normalizeSessionRecord)
        .filter((session): session is SessionRecord => Boolean(session));
      setSessions(nextSessions);
    } catch (error) {
      console.warn("Failed to fetch sessions:", error);
    }
  }, []);

  const fetchFiles = useCallback(async (sessionId: string) => {
    try {
      const fileList = await apiFetchFiles(sessionId);
      setFiles(fileList);
    } catch (error) {
      console.warn("Failed to fetch files:", error);
    }
  }, []);

  const fetchConversation = useCallback(async (sessionId: string) => {
    try {
      const payload = await apiFetchConversation(sessionId);
      const nextMessages = (payload.conversation ?? [])
        .map(normalizeConversationEntry)
        .filter((message): message is Message => Boolean(message));
      setMessages(nextMessages);
    } catch {
      setMessages([]);
    }
  }, []);

  // Load theme on mount
  useEffect(() => {
    const saved = loadSavedThemeId();
    setThemeId(saved);
    applyTheme(getTheme(saved));
  }, []);

  // Check Gmail connection status on mount
  useEffect(() => {
    void fetchGmailStatus().then((status) => setGmailConnected(status.connected)).catch(() => {});
  }, []);

  // Fetch Gmail suggestions when picker opens. Polls if backend is still generating.
  useEffect(() => {
    if (!showGmailPicker || !activeSession) return;
    let cancelled = false;
    let retries = 0;
    const fetchSuggestions = () => {
      void fetch(`${CCHOST_API}/api/sessions/${activeSession}/gmail/suggestions`)
        .then((res) => res.ok ? res.json() : null)
        .then((data) => {
          if (cancelled) return;
          if (data?.suggestions?.length) {
            setGmailSuggestions(data.suggestions);
            setGmailSuggestionsLoading(false);
          } else if (data?.generating && retries < 6) {
            setGmailSuggestionsLoading(true);
            retries++;
            setTimeout(fetchSuggestions, 5000);
          } else {
            setGmailSuggestionsLoading(false);
          }
        })
        .catch(() => { if (!cancelled) setGmailSuggestionsLoading(false); });
    };
    setGmailSuggestionsLoading(true);
    fetchSuggestions();
    return () => { cancelled = true; };
  }, [showGmailPicker, activeSession]);

  // Read URL params on mount
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sessionParam = params.get("session");
    const tabParam = params.get("tab");
    if (sessionParam) {
      setActiveSession(sessionParam);
      activeSessionRef.current = sessionParam;
    }
    if (tabParam === "files" || tabParam === "debug" || tabParam === "terminal") {
      setActiveTab(tabParam);
    }
    if (params.get("gmail_connected") === "true") {
      setShowGmailPicker(true);
      params.delete("gmail_connected");
      const qs = params.toString();
      window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
    }
    urlInitializedRef.current = true;
  }, []);

  // Sync session/tab to URL
  useEffect(() => {
    if (!urlInitializedRef.current) return;
    const params = new URLSearchParams();
    if (activeSession) params.set("session", activeSession);
    if (activeTab !== "chat") params.set("tab", activeTab);
    const qs = params.toString();
    const url = qs ? `?${qs}` : window.location.pathname;
    window.history.replaceState(null, "", url);
  }, [activeSession, activeTab]);

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
      setProgress(null);
      setActiveRunId(null);
      setIsAnswering(false);
      return;
    }

    setIsAnswering(false);
    setGmailSuggestions([]);
    setGmailDownloads([]);
    setShowGmailPicker(false);

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
        const nextProgress = await apiFetchProgress(activeSession);
        if (cancelled) return;

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
        const [nextProgress, run] = await Promise.all([
          apiFetchProgress(activeSession),
          apiFetchRun(activeSession, activeRunId),
        ]);

        if (cancelled) return;

        pollFailureCountRef.current = 0;
        setProgress({ ...nextProgress, run });

        // Always reset isAnswering once we get a poll response back
        setIsAnswering(false);

        // Clear pending message once the JSONL reflects it
        setPendingMessage(null);

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
        if (cancelled) return;

        pollFailureCountRef.current += 1;
        if (pollFailureCountRef.current < POLL_FAILURE_LIMIT) return;

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

  const downloadFile = (path: string) => {
    if (!activeSession) return;
    window.open(getFileUrl(activeSession, path), "_blank");
  };

  const uploadFilesHandler = async (fileList: FileList) => {
    if (!activeSession) {
      alert("Send a message first to create a session, then upload files.");
      return;
    }

    try {
      const uploaded = await apiUploadFiles(activeSession, fileList);
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
    if (activeSessionRef.current) {
      return activeSessionRef.current;
    }

    const sessionId = generateSessionId();
    const response = await apiCreateSession(sessionId, getSessionWorkingDir(sessionId));

    const payload =
      normalizeSessionRecord(response) ?? { id: sessionId, working_dir: getSessionWorkingDir(sessionId) };
    skipConversationLoadForSessionRef.current = payload.id;
    activeSessionRef.current = payload.id;
    setActiveSession(payload.id);
    setSessions((prev) => {
      if (prev.some((session) => session.id === payload.id)) {
        return prev;
      }
      return [payload, ...prev];
    });
    return payload.id;
  };

  const doStartRun = async (sessionId: string, message: string) => {
    const run = await apiStartRun(sessionId, message, DEFAULT_RUN_TIMEOUT_SECONDS);
    handledRunIdsRef.current.delete(run.run_id);
    pollFailureCountRef.current = 0;
    setProgress(createEmptyProgress(run));
    setActiveRunId(run.run_id);
    setIsAnswering(false);
    setIsLoading(true);
  };

  const sendMessage = async (messageText: string) => {
    const message = messageText.trim();
    if (!message || isLoading) return;

    // Slash command handling
    if (message.startsWith("/")) {
      setIsLoading(true);
      try {
        const sessionId = await ensureSession();
        const result = await apiRunSlashCommand(sessionId, message);
        if (result.type === "overlay") {
          // Trigger immediate JSONL refresh so result appears without poll delay
          setJsonlRefreshKey((k) => k + 1);
        } else if (result.type === "response") {
          // It produced a normal response — will appear in JSONL polling
          // Trigger a progress refresh
          const nextProgress = await apiFetchProgress(sessionId);
          setProgress(nextProgress);
          if (nextProgress.run && ["pending", "running", "waiting_for_input"].includes(nextProgress.run.status)) {
            setActiveRunId(nextProgress.run.run_id);
          }
        }
        // "instant" type: nothing to show
      } catch (error: unknown) {
        setSendError(error instanceof Error ? error.message : "Command failed");
      } finally {
        setIsLoading(false);
      }
      return;
    }

    setPendingMessage(message);
    setIsLoading(true);

    try {
      const sessionId = await ensureSession();
      await doStartRun(sessionId, message);
      setActiveTab("chat");
    } catch (error: unknown) {
      setPendingMessage(null);
      setSendError(error instanceof Error ? error.message : "Failed to start run");
      setIsLoading(false);
    }
  };

  const answerQuestion = async (optionIndex: number) => {
    if (!activeSession || isAnswering) return;

    setIsAnswering(true);
    setIsLoading(true);

    try {
      const answerData = await apiAnswerQuestion(activeSession, optionIndex);

      // If the answer produced a new question, reload conversation + progress
      if (answerData.is_question) {
        if (activeSession) void fetchConversation(activeSession);
        const newProgress = await apiFetchProgress(activeSession);
        setProgress(newProgress);
        setIsLoading(false);
        setIsAnswering(false);
        return;
      }

      // Normal completion - refresh conversation
      if (activeSession) void fetchConversation(activeSession);

      setProgress((prev) =>
        prev
          ? {
              ...prev,
              pending_question: null,
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
      // Use ref to avoid stale closure
      const currentProgress = progressRef.current;
      if (currentProgress?.run) {
        setActiveRunId(currentProgress.run.run_id);
      }
    } catch (error: unknown) {
      const content =
        error instanceof Error ? `Error: ${error.message}` : "Error: Failed to answer question.";
      setMessages((prev) => [...prev, { id: makeMessageId("assistant"), role: "assistant", content }]);
      setIsLoading(false);
      setIsAnswering(false);
    }
  };

  const deleteSession = async (sessionId: string) => {
    try {
      await apiDeleteSession(sessionId);
      setSessions((prev) => prev.filter((s) => s.id !== sessionId));
      if (activeSession === sessionId) {
        setActiveSession(null);
        setMessages([]);
        setFiles([]);
        setProgress(null);
        setActiveRunId(null);
      }
    } catch (error) {
      console.warn("Failed to delete session:", error);
    }
  };

  const startNewSessionDraft = () => {
    skipConversationLoadForSessionRef.current = null;
    activeSessionRef.current = null;
    pollFailureCountRef.current = 0;
    setActiveSession(null);
    setMessages([]);
    setFiles([]);
    setProgress(null);
    setActiveRunId(null);
    setActiveTab("chat");
    setIsLoading(false);
    setIsAnswering(false);
  };

  const refreshProgressAfterAnswer = async () => {
    setIsAnswering(false);
    setIsLoading(false);
    if (activeSession) {
      try {
        const res = await apiFetchProgress(activeSession);
        setProgress(res);
      } catch (error) {
        console.warn("Failed to refresh progress after answer:", error);
      }
    }
  };

  const handleGmailSelect = async (selectedThreads: SelectedThread[]) => {
    const sid = activeSessionRef.current || (await ensureSession());

    // Merge new selections into existing downloads (don't replace)
    setGmailDownloads((prev) => {
      const existingIds = new Set(prev.map((d) => d.threadId));
      const newEntries = selectedThreads
        .filter((t) => !existingIds.has(t.id))
        .map((t) => ({ threadId: t.id, threadSubject: t.subject, status: "downloading" as const }));
      return [...prev, ...newEntries];
    });
    setGmailThreadIds(selectedThreads.map((t) => t.id));

    // Download each thread's attachments async
    for (const thread of selectedThreads) {
      try {
        const res = await fetch(
          `${CCHOST_API}/api/sessions/${sid}/gmail/download/${thread.id}`,
          { method: "POST", headers: { "Content-Type": "application/json" } },
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as { files: string[] };

        setGmailDownloads((prev) =>
          prev.map((d) =>
            d.threadId === thread.id
              ? { ...d, status: "downloaded" as const, files: data.files }
              : d,
          ),
        );
        void fetchFiles(sid);
      } catch {
        setGmailDownloads((prev) =>
          prev.map((d) =>
            d.threadId === thread.id
              ? { ...d, status: "error" as const }
              : d,
          ),
        );
      }
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setUploadDrag(false);
    if (e.dataTransfer.files.length) {
      void uploadFilesHandler(e.dataTransfer.files);
    }
  };

  return (
    <div
      className="flex h-screen flex-col bg-th-bg text-th-text"
      onDragOver={(e) => {
        e.preventDefault();
        setUploadDrag(true);
      }}
      onDragLeave={() => setUploadDrag(false)}
      onDrop={handleDrop}
    >
      {uploadDrag && (
        <div className="fixed inset-0 z-50 flex items-center justify-center border-4 border-dashed border-th-accent bg-th-surface">
          <p className="text-2xl font-semibold text-th-accent">Drop files to upload</p>
        </div>
      )}

      {commandResult && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setCommandResult(null)}>
          <div className="max-w-2xl max-h-[80vh] overflow-auto rounded-xl bg-th-bg border border-th-border p-6 shadow-2xl" onClick={e => e.stopPropagation()}>
            <pre className="whitespace-pre-wrap text-sm text-th-text font-mono">{commandResult}</pre>
            <button onClick={() => setCommandResult(null)} className="mt-4 px-4 py-2 rounded-lg bg-th-accent text-white text-sm">Dismiss</button>
          </div>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => e.target.files && void uploadFilesHandler(e.target.files)}
      />

      {/* Header bar */}
      <div className="flex items-center border-b border-th-border px-4">
        <h1 className="mr-6 text-sm font-semibold text-th-accent">cchost</h1>
        <TabBar
          activeTab={activeTab}
          onTabChange={setActiveTab}
          fileCount={files.length}
          hasProgress={Boolean(progress)}
        />

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="rounded-lg border border-th-border px-3 py-1.5 text-xs text-th-text transition-colors hover:border-th-accent hover:text-th-accent"
          >
            Upload
          </button>
          {/* Settings */}
          <div className="relative">
            <button
              onClick={() => setShowSettings((v) => !v)}
              className="rounded-lg border border-th-border px-2 py-1.5 text-xs text-th-text transition-colors hover:border-th-accent hover:text-th-accent cursor-pointer"
              title="Settings"
            >
              ⚙
            </button>
            {showSettings && (
              <div className="absolute right-0 top-full z-40 mt-1 w-48 rounded-lg border border-th-border bg-th-bg shadow-lg p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-th-text-muted mb-2">Theme</p>
                {themes.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => { setThemeId(t.id); applyTheme(t); setShowSettings(false); }}
                    className={`w-full text-left px-2 py-1.5 rounded text-xs cursor-pointer ${
                      themeId === t.id ? "bg-th-surface text-th-accent font-medium" : "text-th-text hover:bg-th-surface"
                    }`}
                  >
                    {t.name}
                  </button>
                ))}
              </div>
            )}
          </div>
          {/* Session dropdown */}
          <SessionSelector
            sessions={sessions}
            activeSession={activeSession}
            onSelectSession={(id) => { setActiveSession(id); setActiveRunId(null); setIsLoading(false); setActiveTab("chat"); }}
            onNewSession={startNewSessionDraft}
            onDeleteSession={deleteSession}
          />
        </div>
      </div>

      <div className="flex flex-1 flex-col overflow-hidden">

        {activeTab === "chat" && (() => {
          const chatColumn = (
            <div className="flex flex-1 flex-col min-h-0">
              <div className="flex-1 overflow-y-auto">
                <JsonlChat
                  sessionId={activeSession}
                  files={files}
                  onViewFile={(path) => { setViewingImages(null); setViewingFile((prev) => prev === path ? null : path); }}
                  onViewImages={(images, index) => { setViewingFile(null); setViewingImages({ images, index }); }}
                  pendingMessage={pendingMessage}
                  isWorking={isLoading}
                  refreshKey={jsonlRefreshKey}
                />

                {progress?.pending_question && (
                  <div className="px-6 pb-4">
                    <PendingQuestionCard
                      question={progress.pending_question}
                      sessionId={activeSession}
                      disabled={isAnswering}
                      onAnswered={refreshProgressAfterAnswer}
                    />
                  </div>
                )}

                {progress?.run?.waiting_for_input && progress.run.current_question && (
                  <div className="px-6 pb-4">
                    <div className="flex justify-start">
                      <div className="max-w-[80%]">
                        <QuestionCard run={progress.run} onAnswer={isAnswering ? undefined : answerQuestion} />
                      </div>
                    </div>
                  </div>
                )}


              </div>
              {sendError && (
                <div className="mx-4 mb-2 flex items-center justify-between rounded-lg border border-th-error-text/30 bg-th-error-bg px-4 py-2 text-sm text-th-error-text">
                  <span>{sendError}</span>
                  <button onClick={() => setSendError(null)} className="ml-3 text-th-error-text hover:opacity-70">✕</button>
                </div>
              )}
              {activeSession && (
                <DraftExportButtons
                  sessionId={activeSession}
                  sessionFiles={files}
                  gmailConnected={gmailConnected}
                />
              )}
              {showGmailPicker && (
                <div className="px-3 pb-2 flex-shrink-0">
                  <GmailPicker
                    sessionId={activeSession}
                    sessionFiles={files}
                    suggestions={gmailSuggestions}
                    suggestionsLoading={gmailSuggestionsLoading}
                    ensureSession={ensureSession}
                    onSelect={(threads) => void handleGmailSelect(threads)}
                    onClose={() => setShowGmailPicker(false)}
                    onGmailConnected={() => setGmailConnected(true)}
                  />
                </div>
              )}
              <ChatInput
                showGmailPicker={showGmailPicker}
                onGmailPickerToggle={setShowGmailPicker}
                gmailDownloads={gmailDownloads}
                onRemoveGmailDownload={(threadId) => {
                  setGmailDownloads((prev) => prev.filter((d) => d.threadId !== threadId));
                  const sid = activeSessionRef.current;
                  if (sid) {
                    void fetch(`${CCHOST_API}/api/sessions/${sid}/gmail/download/${threadId}`, { method: "DELETE" });
                  }
                }}
                onSend={(msg) => {
                  setSendError(null);
                  // Always try to send directly. If a run is active, interrupt first.
                  if (isLoading && activeSession) {
                    void import("@/lib/api").then(({ interruptSession }) =>
                      interruptSession(activeSession).then(() => {
                        setIsLoading(false);
                        setActiveRunId(null);
                        setTimeout(() => sendMessage(msg), 1500);
                      }).catch(() => {
                        // Interrupt failed (maybe no active run) — just send directly
                        setIsLoading(false);
                        setActiveRunId(null);
                        sendMessage(msg);
                      })
                    );
                  } else {
                    sendMessage(msg);
                  }
                }}
                disabled={false}
                sessionId={activeSession}
                ensureSession={ensureSession}
                onFilesUploaded={() => { const sid = activeSessionRef.current; if (sid) void fetchFiles(sid); }}
                sessionFiles={files}
                isWorking={isLoading}
                externalInput={draftInput}
                onInputChange={setDraftInput}
                onInterrupt={() => {
                  if (activeSession) {
                    void import("@/lib/api").then(({ interruptSession }) =>
                      interruptSession(activeSession).then(() => setIsLoading(false))
                    );
                  }
                }}
              />
            </div>
          );

          const rightPane = viewingImages ? (
            <ImageGalleryViewer
              images={viewingImages.images}
              startIndex={viewingImages.index}
              onClose={() => setViewingImages(null)}
            />
          ) : viewingFile && activeSession ? (
            <ArtifactsPane
              sessionId={activeSession}
              files={files}
              selectedFile={viewingFile}
              onSelectFile={setViewingFile}
              onClose={() => setViewingFile(null)}
            />
          ) : null;

          return (
            <ResizablePanel
              left={chatColumn}
              right={rightPane}
              defaultRightWidth={550}
              minRightWidth={300}
              maxRightWidth={900}
            />
          );
        })()}

        {activeTab === "files" && (
          <div className="flex flex-1 min-h-0">
            <div className="w-72 overflow-y-auto border-r border-th-border p-3 flex-shrink-0">
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-th-text-muted">Files</h3>
              {!activeSession ? (
                <p className="text-xs text-th-text-muted">Send a message to create a session</p>
              ) : files.length === 0 ? (
                <p className="text-xs text-th-text-muted">No files yet. Upload or ask Claude to create some.</p>
              ) : (
                files.map((file) => (
                  <div
                    key={file}
                    className={`flex cursor-pointer items-center gap-1 truncate rounded px-2 py-1.5 text-xs font-mono ${
                      viewingFile === file ? "bg-th-surface-hover text-th-accent" : "text-th-text-muted hover:bg-th-surface"
                    }`}
                  >
                    <button
                      onClick={() => setViewingFile((prev) => prev === file ? null : file)}
                      className="flex-1 truncate text-left"
                    >
                      {file}
                    </button>
                    <button
                      onClick={() => downloadFile(file)}
                      title="Download"
                      className="flex-shrink-0 text-th-text-muted hover:text-th-accent"
                    >
                      ↓
                    </button>
                  </div>
                ))
              )}
            </div>
            {viewingFile && activeSession ? (
              <FileViewer
                sessionId={activeSession}
                filePath={viewingFile}
                onClose={() => setViewingFile(null)}
              />
            ) : (
              <div className="flex-1 flex items-center justify-center">
                <p className="text-sm text-th-text-muted">Click a file to view.</p>
              </div>
            )}
          </div>
        )}

        {activeTab === "artifacts" && activeSession && files.length > 0 && (
          <div className="flex flex-1 min-h-0">
            <ArtifactsPane
              sessionId={activeSession}
              files={files}
              selectedFile={viewingFile || files[0]}
              onSelectFile={setViewingFile}
              onClose={() => setActiveTab("chat")}
            />
          </div>
        )}

        {activeTab === "artifacts" && (!activeSession || files.length === 0) && (
          <div className="flex flex-1 items-center justify-center">
            <p className="text-sm text-th-text-muted">No artifacts yet. Start a session and Claude will create files.</p>
          </div>
        )}

        {activeTab === "debug" && (
          <div className="flex flex-1 flex-col overflow-hidden">
            {progress ? (
              <div className="flex-1 overflow-y-auto p-4">
                <ProgressPanel progress={progress} sessionId={activeSession} />
              </div>
            ) : (
              <div className="flex h-full items-center justify-center">
                <p className="text-sm text-th-text-muted">No activity yet. Send a message to start a run.</p>
              </div>
            )}
          </div>
        )}

        {activeTab === "terminal" && (
          <TerminalView sessionId={activeSession} />
        )}
      </div>
    </div>
  );
}
