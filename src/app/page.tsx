"use client";

import { useState, useEffect, useRef, FormEvent } from "react";
import ReactMarkdown from "react-markdown";

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
  const [uploadDrag, setUploadDrag] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Fetch sessions
  useEffect(() => {
    const f = async () => {
      try { const r = await fetch(`${CCHOST_API}/api/sessions`); setSessions(await r.json()); } catch {}
    };
    f(); const i = setInterval(f, 10000); return () => clearInterval(i);
  }, []);

  // Fetch files
  useEffect(() => {
    if (!activeSession) return;
    const f = async () => {
      try { const r = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/files`); setFiles((await r.json()).files || []); } catch {}
    };
    f(); const i = setInterval(f, 5000); return () => clearInterval(i);
  }, [activeSession]);

  const readFile = async (path: string) => {
    if (!activeSession) return;
    try {
      const r = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/files/${encodeURIComponent(path)}`);
      setFileContent((await r.text()).substring(0, 50000));
      setSelectedFile(path);
    } catch (e: any) { setFileContent(`Error: ${e.message}`); }
  };

  const downloadFile = (path: string) => {
    if (!activeSession) return;
    window.open(`${CCHOST_API}/api/sessions/${activeSession}/files/${encodeURIComponent(path)}`, "_blank");
  };

  const uploadFiles = async (fileList: FileList) => {
    if (!activeSession) {
      alert("Send a message first to create a session, then upload files.");
      return;
    }
    const formData = new FormData();
    Array.from(fileList).forEach(f => formData.append(f.name, f));
    try {
      const r = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/upload`, { method: "POST", body: formData });
      const data = await r.json();
      if (data.uploaded?.length) {
        // Add a system message about the upload
        setMessages(prev => [...prev, {
          id: Date.now().toString(),
          role: "assistant",
          content: `Uploaded ${data.uploaded.length} file(s): ${data.uploaded.join(", ")}`,
        }]);
        fetchFiles();
      }
    } catch (e: any) { alert(`Upload failed: ${e.message}`); }
  };

  const fetchFiles = async () => {
    if (!activeSession) return;
    try { const r = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/files`); setFiles((await r.json()).files || []); } catch {}
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
      // If we have an active session, use the REST API directly (keeps files in the right place)
      // Otherwise use the OpenAI endpoint (creates a new session)
      let responseText = "";

      if (activeSession) {
        // Direct REST API — messages go to the existing session
        const res = await fetch(`${CCHOST_API}/api/sessions/${activeSession}/send`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: input, timeout: 900 }),
        });
        const data = await res.json();
        responseText = data.text || data.error || "No response";
      } else {
        // OpenAI endpoint — creates a new session
        const res = await fetch(`${CCHOST_API}/v1/chat/completions`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model: "claude-code",
            messages: allMessages.map(m => ({ role: m.role, content: m.content })),
            stream: true,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const reader = res.body?.getReader();
        const decoder = new TextDecoder();
        while (reader) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const line of decoder.decode(value).split("\n")) {
            if (line.startsWith("data: ") && line !== "data: [DONE]") {
              try {
                const delta = JSON.parse(line.slice(6)).choices?.[0]?.delta?.content;
                if (delta) responseText += delta;
              } catch {}
            }
          }
        }
      }

      const assistantId = (Date.now() + 1).toString();
      setMessages(prev => [...prev, { id: assistantId, role: "assistant", content: responseText }]);

      // After response, refresh sessions and files
      setTimeout(() => { fetchSessions(); fetchFiles(); }, 2000);
    } catch (e: any) {
      setMessages(prev => [...prev, { id: (Date.now() + 1).toString(), role: "assistant", content: `Error: ${e.message}` }]);
    }
    setIsLoading(false);
  };

  const fetchSessions = async () => {
    try { const r = await fetch(`${CCHOST_API}/api/sessions`); const data = await r.json(); setSessions(data); if (data.length && !activeSession) setActiveSession(data[0].id); } catch {}
  };

  const isBinary = (path: string) => /\.(pdf|xlsx|xls|zip|png|jpg|gif)$/i.test(path);

  // Drag and drop handlers
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setUploadDrag(false);
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files);
  };

  return (
    <div className="flex h-screen bg-zinc-950 text-zinc-100"
      onDragOver={e => { e.preventDefault(); setUploadDrag(true); }}
      onDragLeave={() => setUploadDrag(false)}
      onDrop={handleDrop}
    >
      {/* Upload overlay */}
      {uploadDrag && (
        <div className="fixed inset-0 bg-rose-600/20 border-4 border-dashed border-rose-500 z-50 flex items-center justify-center">
          <p className="text-2xl font-semibold text-rose-400">Drop files to upload</p>
        </div>
      )}

      {/* Hidden file input */}
      <input ref={fileInputRef} type="file" multiple className="hidden"
        onChange={e => e.target.files && uploadFiles(e.target.files)} />

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
          ) : sessions.map((s: any) => (
            <button key={s.id} onClick={() => { setActiveSession(s.id); setActiveTab("files"); }}
              className={`w-full text-left px-3 py-2 rounded-lg text-sm mb-1 truncate ${
                activeSession === s.id ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:bg-zinc-900"
              }`}>
              {s.id}
            </button>
          ))}
        </div>
      </div>

      {/* Main */}
      <div className="flex-1 flex flex-col">
        <div className="flex border-b border-zinc-800 items-center">
          <button onClick={() => setActiveTab("chat")}
            className={`px-6 py-3 text-sm font-medium ${activeTab === "chat" ? "text-rose-500 border-b-2 border-rose-500" : "text-zinc-500 hover:text-zinc-300"}`}>
            Chat
          </button>
          <button onClick={() => setActiveTab("files")}
            className={`px-6 py-3 text-sm font-medium ${activeTab === "files" ? "text-rose-500 border-b-2 border-rose-500" : "text-zinc-500 hover:text-zinc-300"}`}>
            Files{files.length > 0 && ` (${files.length})`}
          </button>
          <div className="ml-auto pr-4">
            <button onClick={() => fileInputRef.current?.click()}
              className="text-xs text-zinc-500 hover:text-rose-400 px-3 py-1.5 border border-zinc-700 rounded-lg hover:border-rose-500 transition-colors">
              Upload File
            </button>
          </div>
        </div>

        {activeTab === "chat" && (
          <div className="flex-1 flex flex-col">
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {messages.length === 0 && (
                <div className="flex items-center justify-center h-full">
                  <div className="text-center">
                    <p className="text-2xl font-light text-zinc-400 mb-2">Claude Code</p>
                    <p className="text-sm text-zinc-600 mb-4">Send a message or drop a file to start</p>
                  </div>
                </div>
              )}
              {messages.map(m => (
                <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
                    m.role === "user" ? "bg-rose-600/20 text-zinc-100" : "bg-zinc-800 text-zinc-200"
                  }`}>
                    {m.role === "assistant" ? (
                      <div className="prose prose-invert prose-sm max-w-none
                        prose-pre:bg-zinc-900 prose-pre:text-zinc-300
                        prose-code:text-rose-400 prose-code:bg-zinc-900 prose-code:px-1 prose-code:rounded
                        prose-table:text-zinc-300 prose-th:text-zinc-200
                        prose-a:text-rose-400 prose-strong:text-zinc-100">
                        <ReactMarkdown>{m.content}</ReactMarkdown>
                      </div>
                    ) : (
                      <pre className="whitespace-pre-wrap font-sans">{m.content}</pre>
                    )}
                  </div>
                </div>
              ))}
              {isLoading && messages[messages.length - 1]?.content === "" && (
                <div className="flex justify-start">
                  <div className="bg-zinc-800 rounded-2xl px-4 py-3 text-sm text-zinc-400 animate-pulse">
                    Claude is working...
                  </div>
                </div>
              )}
              <div ref={messagesEndRef} />
            </div>
            <form onSubmit={sendMessage} className="p-4 border-t border-zinc-800">
              <div className="flex gap-2">
                <input value={input} onChange={e => setInput(e.target.value)}
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
            <div className="w-72 border-r border-zinc-800 overflow-y-auto p-3">
              <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">Files</h3>
              {!activeSession ? (
                <p className="text-xs text-zinc-600">Send a message to create a session</p>
              ) : files.length === 0 ? (
                <p className="text-xs text-zinc-600">No files yet. Upload or ask Claude to create some.</p>
              ) : files.map(f => (
                <div key={f} className={`flex items-center gap-1 px-2 py-1.5 rounded text-xs font-mono truncate cursor-pointer ${
                  selectedFile === f ? "bg-zinc-800 text-rose-400" : "text-zinc-400 hover:bg-zinc-900"
                }`}>
                  <button onClick={() => isBinary(f) ? downloadFile(f) : readFile(f)} className="flex-1 text-left truncate">
                    {f}
                  </button>
                  <button onClick={() => downloadFile(f)} title="Download"
                    className="text-zinc-600 hover:text-rose-400 flex-shrink-0">
                    ↓
                  </button>
                </div>
              ))}
            </div>
            <div className="flex-1 overflow-auto p-4">
              {selectedFile ? (
                <>
                  <div className="flex items-center gap-3 mb-3">
                    <h3 className="text-sm font-medium text-zinc-400">{selectedFile}</h3>
                    <button onClick={() => downloadFile(selectedFile)}
                      className="text-xs text-zinc-500 hover:text-rose-400 px-2 py-1 border border-zinc-700 rounded hover:border-rose-500">
                      Download
                    </button>
                  </div>
                  <pre className="text-xs font-mono text-zinc-300 bg-zinc-900 rounded-lg p-4 whitespace-pre-wrap">{fileContent}</pre>
                </>
              ) : (
                <p className="text-sm text-zinc-600">Click a file to view. Binary files (PDF, XLSX) will download directly.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
