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
import { SessionSelector } from "@/components/session-selector";
import { TabBar, type TabId } from "@/components/tab-bar";
import { isBinaryFile } from "@/lib/config";
import {
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
  const [isLoading, setIsLoading] = useState(false);
  const [isAnswering, setIsAnswering] = useState(false);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabId>("chat");
  const [viewingFile, setViewingFile] = useState<string | null>(null);
  const [viewingImages, setViewingImages] = useState<{ images: string[]; index: number } | null>(null);
  const [pendingMessage, setPendingMessage] = useState<string | null>(null);
  const [themeId, setThemeId] = useState("light");
  const [showSettings, setShowSettings] = useState(false);
  const [uploadDrag, setUploadDrag] = useState(false);
  const [progress, setProgress] = useState<ProgressResponse | null>(null);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
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
      setProgress(null);
      setActiveRunId(null);
      setIsAnswering(false);
      return;
    }

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

    setPendingMessage(message);
    setIsLoading(true);

    try {
      const sessionId = await ensureSession();
      await doStartRun(sessionId, message);
      setActiveTab("chat");
    } catch (error: unknown) {
      setPendingMessage(null);
      const content = error instanceof Error ? `Error: ${error.message}` : "Error: Failed to start run.";
      setMessages((prev) => [...prev, { id: makeMessageId("assistant"), role: "assistant", content }]);
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

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setUploadDrag(false);
    if (e.dataTransfer.files.length) {
      void uploadFilesHandler(e.dataTransfer.files);
    }
  };

  return (
    <div
      className="flex h-screen flex-col bg-white text-gray-900"
      onDragOver={(e) => {
        e.preventDefault();
        setUploadDrag(true);
      }}
      onDragLeave={() => setUploadDrag(false)}
      onDrop={handleDrop}
    >
      {uploadDrag && (
        <div className="fixed inset-0 z-50 flex items-center justify-center border-4 border-dashed border-[var(--th-accent)] bg-orange-50">
          <p className="text-2xl font-semibold text-[var(--th-accent)]">Drop files to upload</p>
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
      <div className="flex items-center border-b border-gray-300 px-4">
        <h1 className="mr-6 text-sm font-semibold text-[var(--th-accent)]">cchost</h1>
        <TabBar
          activeTab={activeTab}
          onTabChange={setActiveTab}
          fileCount={files.length}
          hasProgress={Boolean(progress)}
        />

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="rounded-lg border border-gray-300 px-3 py-1.5 text-xs text-gray-700 transition-colors hover:border-[var(--th-accent)] hover:text-[var(--th-accent)]"
          >
            Upload
          </button>
          {/* Settings */}
          <div className="relative">
            <button
              onClick={() => setShowSettings((v) => !v)}
              className="rounded-lg border border-gray-300 px-2 py-1.5 text-xs text-gray-700 transition-colors hover:border-[var(--th-accent)] hover:text-[var(--th-accent)] cursor-pointer"
              title="Settings"
            >
              ⚙
            </button>
            {showSettings && (
              <div className="absolute right-0 top-full z-40 mt-1 w-48 rounded-lg border border-gray-200 bg-white shadow-lg p-3">
                <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500 mb-2">Theme</p>
                {themes.map((t) => (
                  <button
                    key={t.id}
                    onClick={() => { setThemeId(t.id); applyTheme(t); setShowSettings(false); }}
                    className={`w-full text-left px-2 py-1.5 rounded text-xs cursor-pointer ${
                      themeId === t.id ? "bg-orange-50 text-[var(--th-accent)] font-medium" : "text-gray-700 hover:bg-gray-50"
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
            onSelectSession={(id) => { setActiveSession(id); setActiveTab("chat"); }}
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
                  onViewFile={(path) => { setViewingImages(null); setViewingFile(path); }}
                  onViewImages={(images, index) => { setViewingFile(null); setViewingImages({ images, index }); }}
                  pendingMessage={pendingMessage}
                  isWorking={isLoading}
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

                <div ref={messagesEndRef} />
              </div>
              <ChatInput
                onSend={sendMessage}
                disabled={isLoading}
                sessionId={activeSession}
                ensureSession={ensureSession}
                onFilesUploaded={() => { const sid = activeSessionRef.current; if (sid) void fetchFiles(sid); }}
                sessionFiles={files}
              />
            </div>
          );

          if (viewingImages) {
            return (
              <ResizablePanel
                left={chatColumn}
                right={
                  <ImageGalleryViewer
                    images={viewingImages.images}
                    startIndex={viewingImages.index}
                    onClose={() => setViewingImages(null)}
                  />
                }
                defaultRightWidth={550}
                minRightWidth={300}
                maxRightWidth={900}
              />
            );
          }

          if (viewingFile && activeSession) {
            return (
              <ResizablePanel
                left={chatColumn}
                right={
                  <ArtifactsPane
                    sessionId={activeSession}
                    files={files}
                    selectedFile={viewingFile}
                    onSelectFile={setViewingFile}
                    onClose={() => setViewingFile(null)}
                  />
                }
                defaultRightWidth={550}
                minRightWidth={300}
                maxRightWidth={900}
              />
            );
          }

          return chatColumn;
        })()}

        {activeTab === "files" && (
          <div className="flex flex-1 min-h-0">
            <div className="w-72 overflow-y-auto border-r border-gray-300 p-3 flex-shrink-0">
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-gray-600">Files</h3>
              {!activeSession ? (
                <p className="text-xs text-gray-600">Send a message to create a session</p>
              ) : files.length === 0 ? (
                <p className="text-xs text-gray-600">No files yet. Upload or ask Claude to create some.</p>
              ) : (
                files.map((file) => (
                  <div
                    key={file}
                    className={`flex cursor-pointer items-center gap-1 truncate rounded px-2 py-1.5 text-xs font-mono ${
                      viewingFile === file ? "bg-gray-100 text-[var(--th-accent)]" : "text-gray-600 hover:bg-gray-50"
                    }`}
                  >
                    <button
                      onClick={() => setViewingFile(file)}
                      className="flex-1 truncate text-left"
                    >
                      {file}
                    </button>
                    <button
                      onClick={() => downloadFile(file)}
                      title="Download"
                      className="flex-shrink-0 text-gray-600 hover:text-[var(--th-accent)]"
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
                <p className="text-sm text-gray-600">Click a file to view.</p>
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
            <p className="text-sm text-gray-500">No artifacts yet. Start a session and Claude will create files.</p>
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
                <p className="text-sm text-gray-600">No activity yet. Send a message to start a run.</p>
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
