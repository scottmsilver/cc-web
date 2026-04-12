import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DraftExportButtons } from "./draft-export-buttons";

vi.mock("@/lib/api", () => ({
  createGmailDraft: vi.fn(),
  readFile: vi.fn(),
}));

import { createGmailDraft, readFile } from "@/lib/api";

const mockCreateGmailDraft = vi.mocked(createGmailDraft);
const mockReadFile = vi.mocked(readFile);

describe("DraftExportButtons", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders nothing when email_to_gc.txt not in sessionFiles", () => {
    const { container } = render(
      <DraftExportButtons sessionId="sess-1" sessionFiles={["extracted.json"]} gmailConnected={true} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders nothing when Gmail not connected", () => {
    const { container } = render(
      <DraftExportButtons sessionId="sess-1" sessionFiles={["email_to_gc.txt"]} gmailConnected={false} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders both buttons when email_to_gc.txt exists and Gmail connected", () => {
    render(
      <DraftExportButtons sessionId="sess-1" sessionFiles={["email_to_gc.txt"]} gmailConnected={true} />,
    );
    expect(screen.getByText("Send to Gmail Drafts")).toBeInTheDocument();
    expect(screen.getByText("Send to Google Docs")).toBeInTheDocument();
  });

  it("calls createGmailDraft with thread_id from gmail-source.json", async () => {
    mockReadFile.mockResolvedValue('{"thread_ids": ["thread-abc"], "sender": "gc@test.com"}');
    mockCreateGmailDraft.mockResolvedValue({ draft_id: "draft-123" });

    render(
      <DraftExportButtons
        sessionId="sess-1"
        sessionFiles={["email_to_gc.txt", "gmail-source.json"]}
        gmailConnected={true}
      />,
    );

    fireEvent.click(screen.getByText("Send to Gmail Drafts"));

    await waitFor(() => {
      expect(mockCreateGmailDraft).toHaveBeenCalledWith("sess-1", "thread-abc");
    });
  });

  it("creates non-threaded draft when gmail-source.json missing", async () => {
    mockCreateGmailDraft.mockResolvedValue({ draft_id: "draft-456" });

    render(
      <DraftExportButtons sessionId="sess-1" sessionFiles={["email_to_gc.txt"]} gmailConnected={true} />,
    );

    fireEvent.click(screen.getByText("Send to Gmail Drafts"));

    await waitFor(() => {
      expect(mockCreateGmailDraft).toHaveBeenCalledWith("sess-1", "");
    });
  });

  it("shows success confirmation after draft created", async () => {
    mockCreateGmailDraft.mockResolvedValue({ draft_id: "draft-789" });

    render(
      <DraftExportButtons sessionId="sess-1" sessionFiles={["email_to_gc.txt"]} gmailConnected={true} />,
    );

    fireEvent.click(screen.getByText("Send to Gmail Drafts"));

    await waitFor(() => {
      expect(screen.getByText(/Draft created/)).toBeInTheDocument();
    });
  });

  it("shows error with retry on failure", async () => {
    mockCreateGmailDraft.mockRejectedValue(new Error("Auth expired"));

    render(
      <DraftExportButtons sessionId="sess-1" sessionFiles={["email_to_gc.txt"]} gmailConnected={true} />,
    );

    fireEvent.click(screen.getByText("Send to Gmail Drafts"));

    await waitFor(() => {
      expect(screen.getByText("Auth expired")).toBeInTheDocument();
      expect(screen.getByText("Retry")).toBeInTheDocument();
    });
  });
});
