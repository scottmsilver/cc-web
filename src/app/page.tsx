"use client";

import { useState, useEffect, useRef, FormEvent } from "react";

const CCHOST_API = "http://localhost:8420";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export default function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessions, setSessions] = useState<any[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [selectedFile, setSelectedFile] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"chat" | "files">("chat");
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Fetch sessions
  useEffect(() => {
    const fetch_ = async () => {
      try {
        const res = await fetch(`${CCHOST_API}/api/sessions`);
        setSessions(await res.json());
      } catch {}
    };
    fetch_();
    const i = setInterval(fetch_, 10000);
    return () => clearInterval(i);
  }, []);

  // Fetch files
  useEffect(() => {
    if (!activeSession) return;
    const fetch_ = async () => {
      try {
        const res = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/files`);
        const data = await res.json();
        setFiles(data.files || []);
      } catch {}
    };
    fetch_();
    const i = setInterval(fetch_, 5000);
    return () => clearInterval(i);
  }, [activeSession]);

  const readFile = async (path: string) => {
    if (!activeSession) return;
    try {
      const res = await fetch(
        `${CCHOST_API}/api/sessions/${activeSession}/files/${encodeURIComponent(path)}`
      );
      setFileContent((await res.text()).substring(0, 50000));
      setSelectedFile(path);
    } catch (e: any) {
      setFileContent(`Error: ${e.message}`);
    }
  };

  const sendMessage = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMsg: Message = { id: Date.now().toString(), role: "user", content: input };
    const allMessages = [...messages, userMsg];
    setMessages(allMessages);
    setInput("");
    setIsLoading(true);

    try {
      // Send via cchost's OpenAI-compatible SSE endpoint
      const res = await fetch(`${CCHOST_API}/v1/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "claude-code",
          messages: allMessages.map((m) => ({ role: m.role, content: m.content })),
          stream: true,
        }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      // Read SSE stream
      const reader = res.body?.getReader();
      const decoder = new TextDecoder();
      let assistantContent = "";
      const assistantId = (Date.now() + 1).toString();

      // Add placeholder
      setMessages((prev) => [...prev, { id: assistantId, role: "assistant", content: "" }]);

      while (reader) {
        const { done, value } = await reader.read();
        if (done) break;

        const text = decoder.decode(value);
        for (const line of text.split("\n")) {
          if (line.startsWith("data: ") && line !== "data: [DONE]") {
            try {
              const chunk = JSON.parse(line.slice(6));
              const delta = chunk.choices?.[0]?.delta?.content;
              if (delta) {
                assistantContent += delta;
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId ? { ...m, content: assistantContent } : m
                  )
                );
              }
            } catch {}
          }
        }
      }
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        { id: (Date.now() + 1).toString(), role: "assistant", content: `Error: ${e.message}` },
      ]);
    }
    setIsLoading(false);
  };

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100">
      {/* Sidebar */}
      <div className="w-64 border-r border-zinc-800 flex flex-col">
        <div className="p-4 border-b border-zinc-800">
          <h1 className="text-lg font-semibold text-rose-500">cchost</h1>
          <p className="text-xs text-zinc-500">Claude Code Chat</p>
        </div>
        <div className="p-3 flex-1 overflow-y-auto">
          <h2 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">Sessions</h2>
          {sessions.length === 0 ? (
            <p className="text-xs text-zinc-600">No active sessions</p>
          ) : (
            sessions.map((s: any) => (
              <button
                key={s.id}
                onClick={() => { setActiveSession(s.id); setActiveTab("files"); }}
                className={`w-full text-left px-3 py-2 rounded-lg text-sm mb-1 truncate ${
                  activeSession === s.id ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:bg-zinc-900"
                }`}
              >
                {s.id}
              </button>
            ))
          )}
        </div>
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col">
        <div className="flex border-b border-zinc-800">
          <button onClick={() => setActiveTab("chat")}
            className={`px-6 py-3 text-sm font-medium ${activeTab === "chat" ? "text-rose-500 border-b-2 border-rose-500" : "text-zinc-500 hover:text-zinc-300"}`}>
            Chat
          </button>
          <button onClick={() => setActiveTab("files")}
            className={`px-6 py-3 text-sm font-medium ${activeTab === "files" ? "text-rose-500 border-b-2 border-rose-500" : "text-zinc-500 hover:text-zinc-300"}`}>
            Files{files.length > 0 && ` (${files.length})`}
          </button>
        </div>

        {activeTab === "chat" && (
          <div className="flex-1 flex flex-col">
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {messages.length === 0 && (
                <div className="flex items-center justify-center h-full">
                  <div className="text-center">
                    <p className="text-2xl font-light text-zinc-400 mb-2">Claude Code</p>
                    <p className="text-sm text-zinc-600">Send a message to start</p>
                  </div>
                </div>
              )}
              {messages.map((m) => (
                <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
                    m.role === "user" ? "bg-rose-600/20 text-zinc-100" : "bg-zinc-800 text-zinc-200"
                  }`}>
                    <pre className="whitespace-pre-wrap font-sans">{m.content}</pre>
                  </div>
                </div>
              ))}
              {isLoading && messages[messages.length - 1]?.role !== "assistant" && (
                <div className="flex justify-start">
                  <div className="bg-zinc-800 rounded-2xl px-4 py-3 text-sm text-zinc-400 animate-pulse">
                    Thinking...
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
            <form onSubmit={sendMessage} className="p-4 border-t border-zinc-800">
              <div className="flex gap-2">
                <input value={input} onChange={(e) => setInput(e.target.value)}
                  placeholder="Message Claude Code..." disabled={isLoading}
                  className="flex-1 bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-rose-500 placeholder-zinc-600" />
                <button type="submit" disabled={isLoading || !input.trim()}
                  className="bg-rose-600 hover:bg-rose-500 disabled:bg-zinc-700 disabled:cursor-not-allowed rounded-xl px-6 py-3 text-sm font-medium transition-colors">
                  Send
                </button>
              </div>
            </form>
          </div>
        )}

        {activeTab === "files" && (
          <div className="flex-1 flex">
            <div className="w-64 border-r border-zinc-800 overflow-y-auto p-3">
              <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">Files</h3>
              {!activeSession ? (
                <p className="text-xs text-zinc-600">Select a session</p>
              ) : files.length === 0 ? (
                <p className="text-xs text-zinc-600">No files yet</p>
              ) : (
                files.map((f) => (
                  <button key={f} onClick={() => readFile(f)}
                    className={`w-full text-left px-2 py-1.5 rounded text-xs font-mono truncate ${
                      selectedFile === f ? "bg-zinc-800 text-rose-400" : "text-zinc-400 hover:bg-zinc-900"
                    }`}>
                    {f}
                  </button>
                ))
              )}
            </div>
            <div className="flex-1 overflow-auto p-4">
              {selectedFile ? (
                <>
                  <h3 className="text-sm font-medium text-zinc-400 mb-2">{selectedFile}</h3>
                  <pre className="text-xs font-mono text-zinc-300 bg-zinc-900 rounded-lg p-4 whitespace-pre-wrap">{fileContent}</pre>
                </>
              ) : (
                <p className="text-sm text-zinc-600">Click a file to view</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
