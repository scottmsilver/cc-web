import { describe, it, expect } from "vitest";
import { findAtRefs } from "./jsonl-chat";

const FILES = [
  "extracted_pages/BedrockQuartz_70130_Summary.pdf",
  "inbox/19d73a5efb67f53d/thread.json",
  "inbox/19d73a5efb67f53d/message_0.eml",
  "inbox/19d73a5efb67f53d/Silver Remodel March 2026 Draw Request.pdf",
  "report.pdf",
  "data.csv",
  "audit_findings.md",
  "extracted.json",
];

function paths(text: string, files = FILES): string[] {
  return findAtRefs(text, files).map((r) => r.path);
}

describe("findAtRefs", () => {
  describe("basic matching", () => {
    it("matches @./path with dot-slash prefix", () => {
      expect(paths("@./report.pdf")).toEqual(["report.pdf"]);
    });

    it("matches @path without dot-slash prefix", () => {
      expect(paths("@report.pdf")).toEqual(["report.pdf"]);
    });

    it("matches nested paths", () => {
      expect(paths("@./extracted_pages/BedrockQuartz_70130_Summary.pdf")).toEqual([
        "extracted_pages/BedrockQuartz_70130_Summary.pdf",
      ]);
    });

    it("matches nested paths without dot-slash", () => {
      expect(paths("@extracted_pages/BedrockQuartz_70130_Summary.pdf")).toEqual([
        "extracted_pages/BedrockQuartz_70130_Summary.pdf",
      ]);
    });

    it("matches directory references", () => {
      expect(paths("@./inbox/19d73a5efb67f53d/")).toEqual(["inbox/19d73a5efb67f53d/"]);
    });

    it("matches filenames with spaces", () => {
      expect(paths("@./inbox/19d73a5efb67f53d/Silver Remodel March 2026 Draw Request.pdf")).toEqual([
        "inbox/19d73a5efb67f53d/Silver Remodel March 2026 Draw Request.pdf",
      ]);
    });
  });

  describe("trailing punctuation", () => {
    it("does not capture trailing period after .pdf", () => {
      expect(paths("@./report.pdf.")).toEqual(["report.pdf"]);
    });

    it("does not capture trailing comma", () => {
      expect(paths("@./report.pdf, and more")).toEqual(["report.pdf"]);
    });

    it("does not capture trailing period on nested path", () => {
      expect(paths("@./extracted_pages/BedrockQuartz_70130_Summary.pdf.")).toEqual([
        "extracted_pages/BedrockQuartz_70130_Summary.pdf",
      ]);
    });
  });

  describe("multiple refs in one message", () => {
    it("finds two refs separated by text", () => {
      expect(paths("look at @report.pdf and @data.csv")).toEqual(["report.pdf", "data.csv"]);
    });

    it("finds refs with dot-slash and without mixed", () => {
      expect(paths("@./report.pdf and @data.csv")).toEqual(["report.pdf", "data.csv"]);
    });
  });

  describe("security and edge cases", () => {
    it("does not match paths outside the file list", () => {
      expect(paths("@/etc/passwd")).toEqual([]);
    });

    it("does not match email addresses", () => {
      expect(paths("email me at scott@example.com")).toEqual([]);
    });

    it("does not match @ in the middle of a word", () => {
      expect(paths("user@host")).toEqual([]);
    });

    it("matches @ at start of string", () => {
      expect(paths("@report.pdf")).toEqual(["report.pdf"]);
    });

    it("matches @ after a quote", () => {
      expect(paths('"@report.pdf"')).toEqual(["report.pdf"]);
    });

    it("matches @ after opening paren", () => {
      expect(paths("(@report.pdf)")).toEqual(["report.pdf"]);
    });

    it("returns empty for no files", () => {
      expect(findAtRefs("@./something.txt", [])).toEqual([]);
    });

    it("returns empty for text with no @", () => {
      expect(paths("just some text")).toEqual([]);
    });
  });

  describe("position tracking", () => {
    it("tracks start and end positions correctly", () => {
      const refs = findAtRefs("see @./report.pdf here", FILES);
      expect(refs).toHaveLength(1);
      expect(refs[0].start).toBe(4);
      expect(refs[0].end).toBe(4 + "@./".length + "report.pdf".length);
      expect(refs[0].path).toBe("report.pdf");
    });

    it("extracted text between refs is correct", () => {
      const text = "check @./report.pdf and @./data.csv please";
      const refs = findAtRefs(text, FILES);
      expect(refs).toHaveLength(2);
      // Text between: " and "
      const between = text.slice(refs[0].end, refs[1].start);
      expect(between).toBe(" and ");
    });
  });

  describe("longest match wins", () => {
    it("prefers longer file path over shorter", () => {
      const files = ["inbox/thread.json", "inbox/19d73a5efb67f53d/thread.json"];
      expect(paths("@./inbox/19d73a5efb67f53d/thread.json", files)).toEqual([
        "inbox/19d73a5efb67f53d/thread.json",
      ]);
    });
  });

  describe("basename matching", () => {
    it("matches basename and resolves to full path", () => {
      const files = ["inbox/deep/report.pdf"];
      expect(paths("@report.pdf", files)).toEqual(["inbox/deep/report.pdf"]);
    });

    it("tracks end position correctly for basename match", () => {
      const files = ["inbox/deep/report.pdf"];
      const refs = findAtRefs("see @report.pdf here", files);
      expect(refs).toHaveLength(1);
      expect(refs[0].path).toBe("inbox/deep/report.pdf");
      // @ + report.pdf = 1 + 10 = 11 chars consumed from text
      expect(refs[0].end).toBe(4 + 1 + "report.pdf".length);
      // Text after should be " here"
      expect("see @report.pdf here".slice(refs[0].end)).toBe(" here");
    });

    it("does not match ambiguous basenames", () => {
      const files = ["a/report.pdf", "b/report.pdf"];
      expect(paths("@report.pdf", files)).toEqual([]);
    });

    it("prefers full path over basename", () => {
      const files = ["report.pdf", "inbox/report.pdf"];
      expect(paths("@report.pdf", files)).toEqual(["report.pdf"]);
    });
  });

  describe("directory matching", () => {
    it("matches a directory prefix from file paths", () => {
      const files = ["inbox/19d73/thread.json", "inbox/19d73/message_0.eml"];
      expect(paths("@inbox/19d73/", files)).toEqual(["inbox/19d73/"]);
    });

    it("tracks end position for directory match", () => {
      const files = ["inbox/abc/file.txt"];
      const refs = findAtRefs("@./inbox/abc/ has files", files);
      expect(refs).toHaveLength(1);
      expect(refs[0].end).toBe("@./inbox/abc/".length);
    });
  });
});
