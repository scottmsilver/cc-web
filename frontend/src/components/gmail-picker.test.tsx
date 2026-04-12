import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GmailPicker } from "./gmail-picker";

// Mock the api module
vi.mock("@/lib/api", () => ({
  fetchGmailStatus: vi.fn(),
  getGmailAuthUrl: vi.fn(() => "https://accounts.google.com/oauth"),
  scanGmail: vi.fn(),
}));

import { fetchGmailStatus, scanGmail } from "@/lib/api";

const mockFetchGmailStatus = vi.mocked(fetchGmailStatus);
const mockScanGmail = vi.mocked(scanGmail);

const defaultProps = {
  sessionId: "sess-1",
  onSelect: vi.fn(),
  onClose: vi.fn(),
  ensureSession: vi.fn().mockResolvedValue("sess-1"),
};

describe("GmailPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders Connect Gmail button when not connected", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: false });
    render(<GmailPicker {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByText("Connect Gmail")).toBeInTheDocument();
    });
  });

  it("renders search UI when connected", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: true, email: "test@example.com" });
    render(<GmailPicker {...defaultProps} />);
    await waitFor(() => {
      expect(screen.getByPlaceholderText("Gmail search query...")).toBeInTheDocument();
      expect(screen.getByText("test@example.com")).toBeInTheDocument();
    });
  });

  it("calls scanGmail when Scan button is clicked", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: true, email: "test@example.com" });
    mockScanGmail.mockResolvedValue([]);
    render(<GmailPicker {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getByText("Scan")).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText("Gmail search query...");
    fireEvent.change(input, { target: { value: "has:attachment" } });
    fireEvent.click(screen.getByText("Scan"));

    await waitFor(() => {
      expect(mockScanGmail).toHaveBeenCalledWith("has:attachment");
    });
  });

  it("shows suggestion chips when provided", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: true, email: "test@example.com" });
    const suggestions = [
      { label: "GMRS License", query: "subject:GMRS from:fcc.gov" },
      { label: "Radio Orders", query: "subject:radio order" },
    ];
    render(<GmailPicker {...defaultProps} suggestions={suggestions} />);

    await waitFor(() => {
      expect(screen.getByText("GMRS License")).toBeInTheDocument();
      expect(screen.getByText("Radio Orders")).toBeInTheDocument();
    });
  });

  it("toggles thread selection with checkboxes", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: true, email: "test@example.com" });
    mockScanGmail.mockResolvedValue([
      { id: "t1", subject: "Draw Request", sender: "gc@test.com", date: "2026-04-01", message_count: 1, attachment_count: 2, downloaded: false },
    ]);
    render(<GmailPicker {...defaultProps} />);

    await waitFor(() => screen.getByText("Scan"));
    fireEvent.click(screen.getByText("Scan"));

    await waitFor(() => {
      expect(screen.getByText("Draw Request")).toBeInTheDocument();
    });

    const checkbox = screen.getByRole("checkbox");
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();
  });

  it("closes when X button is clicked", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: true, email: "test@example.com" });
    render(<GmailPicker {...defaultProps} />);

    await waitFor(() => {
      expect(screen.getAllByText("✕")[0]).toBeInTheDocument();
    });

    fireEvent.click(screen.getAllByText("✕")[0]);
    expect(defaultProps.onClose).toHaveBeenCalled();
  });

  it("shows error state on scan failure", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: true, email: "test@example.com" });
    mockScanGmail.mockRejectedValue(new Error("Network error"));
    render(<GmailPicker {...defaultProps} />);

    await waitFor(() => screen.getByText("Scan"));
    fireEvent.click(screen.getByText("Scan"));

    await waitFor(() => {
      expect(screen.getByText("Network error")).toBeInTheDocument();
    });
  });

  it("shows downloaded badge for previously downloaded threads", async () => {
    mockFetchGmailStatus.mockResolvedValue({ connected: true, email: "test@example.com" });
    mockScanGmail.mockResolvedValue([
      { id: "t1", subject: "Already Downloaded", sender: "gc@test.com", date: "2026-04-01", message_count: 1, attachment_count: 1, downloaded: true },
    ]);
    render(<GmailPicker {...defaultProps} />);

    await waitFor(() => screen.getByText("Scan"));
    fireEvent.click(screen.getByText("Scan"));

    await waitFor(() => {
      expect(screen.getByText("Already Downloaded")).toBeInTheDocument();
      expect(screen.getByText("✓")).toBeInTheDocument();
    });
  });
});
