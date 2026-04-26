"use client";

import { useEffect, useRef, useState } from "react";

import { createGmailDraft, readFile, getSilverOAuthEmail } from "@/lib/api";
import { CCHOST_API } from "@/lib/config";

type DraftExportButtonsProps = {
  sessionId: string;
  sessionFiles: string[];
  gmailConnected: boolean;
};

export function DraftExportButtons({
  sessionId,
  sessionFiles,
  gmailConnected,
}: DraftExportButtonsProps) {
  const [draftStatus, setDraftStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [docStatus, setDocStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [draftError, setDraftError] = useState("");
  const [docError, setDocError] = useState("");
  const prevFilesRef = useRef(sessionFiles);

  // Reset export status when sessionFiles change (email regenerated)
  useEffect(() => {
    const hadEmail = prevFilesRef.current.some(
      (f) => f === "email_to_gc.txt" || f.endsWith("_draw_email.md"),
    );
    const hasEmail = sessionFiles.some(
      (f) => f === "email_to_gc.txt" || f.endsWith("_draw_email.md"),
    );
    if (hasEmail && !hadEmail) {
      setDraftStatus("idle");
      setDocStatus("idle");
    }
    prevFilesRef.current = sessionFiles;
  }, [sessionFiles]);

  const hasEmailFile = sessionFiles.some(
    (f) => f === "email_to_gc.txt" || f.endsWith("_draw_email.md"),
  );

  if (!hasEmailFile || !gmailConnected) {
    return null;
  }

  const handleSendToGmail = async () => {
    setDraftStatus("loading");
    setDraftError("");
    try {
      // Read gmail-source.json to get thread_id for threading
      let threadId = "";
      if (sessionFiles.includes("gmail-source.json")) {
        try {
          const sourceContent = await readFile(sessionId, "gmail-source.json");
          const source = JSON.parse(sourceContent);
          threadId = source.thread_ids?.[0] || "";
        } catch {
          // No gmail-source.json or parse error — will create non-threaded draft
        }
      }

      if (threadId) {
        await createGmailDraft(sessionId, threadId);
      } else {
        // Create non-threaded draft — still call the endpoint with empty thread_id
        // The backend will handle creating a new draft without threading
        await createGmailDraft(sessionId, "");
      }
      setDraftStatus("success");
    } catch (err: unknown) {
      setDraftError(err instanceof Error ? err.message : "Draft creation failed");
      setDraftStatus("error");
    }
  };

  const handleSendToDocs = async () => {
    setDocStatus("loading");
    setDocError("");
    try {
      const oauthEmail = getSilverOAuthEmail();
      const headers: Record<string, string> = { "Content-Type": "application/json" };
      if (oauthEmail) headers["X-Silver-OAuth-Email"] = oauthEmail;
      const res = await fetch(
        `${CCHOST_API}/api/sessions/${sessionId}/drive/doc`,
        { method: "POST", headers },
      );
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`Doc creation failed: ${detail}`);
      }
      const data = (await res.json()) as { doc_url: string };
      setDocStatus("success");
      window.open(data.doc_url, "_blank");
    } catch (err: unknown) {
      setDocError(err instanceof Error ? err.message : "Doc creation failed");
      setDocStatus("error");
    }
  };

  return (
    <div className="flex items-center gap-2 px-6 py-3 border-t border-th-border bg-th-surface/50">
      <span className="text-xs text-th-text-muted mr-2">Export draft:</span>

      <button
        onClick={() => void handleSendToGmail()}
        disabled={draftStatus === "loading" || draftStatus === "success"}
        className="rounded-lg border border-th-border px-3 py-1.5 text-xs font-medium text-th-text transition-colors hover:border-th-accent hover:text-th-accent disabled:opacity-50"
      >
        {draftStatus === "loading"
          ? "Creating draft..."
          : draftStatus === "success"
            ? "Draft created ✓"
            : "Send to Gmail Drafts"}
      </button>

      <button
        onClick={() => void handleSendToDocs()}
        disabled={docStatus === "loading" || docStatus === "success"}
        className="rounded-lg border border-th-border px-3 py-1.5 text-xs font-medium text-th-text transition-colors hover:border-th-accent hover:text-th-accent disabled:opacity-50"
      >
        {docStatus === "loading"
          ? "Creating doc..."
          : docStatus === "success"
            ? "Doc created ✓"
            : "Send to Google Docs"}
      </button>

      {draftStatus === "error" && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-th-error-text">{draftError}</span>
          <button
            onClick={() => { setDraftStatus("idle"); void handleSendToGmail(); }}
            className="text-xs text-th-accent hover:underline"
          >
            Retry
          </button>
        </div>
      )}

      {docStatus === "error" && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-th-error-text">{docError}</span>
          <button
            onClick={() => { setDocStatus("idle"); void handleSendToDocs(); }}
            className="text-xs text-th-accent hover:underline"
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
}
