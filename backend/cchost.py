"""
cchost — Claude Code as a hosted service.

Runs Claude Code interactively in tmux for persistent sessions (sub-agents,
bash processes, tools all stay alive). Reads responses from Claude's JSONL
conversation log (written in real-time) instead of parsing the TUI.

Usage:
    from cchost import CCHost

    host = CCHost()
    session = host.create("my-audit", working_dir="/data/feb")
    response = session.send("What is 2+2?")
    print(response.text)  # "4"

    # Persistent — sub-agents and tools stay alive between messages
    response2 = session.send("/invoice:analyzer .")
    print(response2.text)

    # Files created by Claude are accessible
    print(session.files())
    email = session.read_file("feb_draw_email.md")
"""

import glob
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import libtmux
from progress import derive_progress_snapshot, normalize_jsonl_entries

logger = logging.getLogger("cchost")


@dataclass
class QuestionOption:
    """An option in an AskUserQuestion prompt."""

    label: str
    index: int
    description: str = ""


@dataclass
class Response:
    """A response from Claude Code."""

    text: str
    role: str = "assistant"
    raw: dict = field(default_factory=dict)
    is_question: bool = False
    questions: list[dict] = field(default_factory=list)  # [{question, options: [QuestionOption]}]


_OVERLAY_FOOTERS = ("to dismiss", "esc to cancel", "esc to close")


def _has_overlay_footer(text: str) -> bool:
    """Check if tmux pane content shows an overlay footer."""
    lower = text.lower()
    return any(f in lower for f in _OVERLAY_FOOTERS)


def _is_overlay_footer_line(line: str) -> bool:
    """Check if a single line is an overlay footer."""
    lower = line.strip().lower()
    return any(f in lower for f in _OVERLAY_FOOTERS)


@dataclass
class CCSession:
    """A persistent Claude Code session running in tmux."""

    id: str
    working_dir: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _tmux_session: Optional[libtmux.Session] = field(default=None, repr=False)
    _host: Optional["CCHost"] = field(default=None, repr=False)
    _jsonl_path: Optional[str] = field(default=None, repr=False)
    _last_line_count: int = field(default=0, repr=False)
    _tmux_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _claude_session_id: Optional[str] = field(default=None, repr=False)

    @property
    def _project_slug(self) -> str:
        return self.working_dir.replace("/", "-").lstrip("-")

    def _find_jsonl(self) -> Optional[str]:
        """Find the JSONL conversation log for this session."""
        project_dir = os.path.expanduser(f"~/.claude/projects/-{self._project_slug}")
        if os.path.isdir(project_dir):
            files = glob.glob(os.path.join(project_dir, "*.jsonl"))
            if files:
                best = max(files, key=os.path.getmtime)
                # Extract Claude session ID from filename (UUID.jsonl)
                basename = os.path.basename(best)
                if basename.endswith(".jsonl"):
                    new_id = basename[:-6]
                    if new_id != self._claude_session_id:
                        self._claude_session_id = new_id
                        # Persist updated session ID to manifest
                        if self._host is not None:
                            self._host._save_manifest()
                return best
        return None

    def _ensure_jsonl_path(self) -> Optional[str]:
        """Resolve and cache the JSONL path, returning None if not found."""
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            self._jsonl_path = self._find_jsonl()
        return self._jsonl_path if self._jsonl_path and os.path.exists(self._jsonl_path) else None

    @staticmethod
    def _parse_jsonl_file(path: str, offset: int = 0) -> tuple[list[dict], int]:
        """Parse a JSONL file, returning (entries from line `offset`, total line count)."""
        with open(path, "r") as f:
            lines = f.readlines()
        parsed = []
        for line in lines[offset:]:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                parsed.append(entry)
        return parsed, len(lines)

    @staticmethod
    def _build_question_result(question_text: str, raw_options: list) -> dict:
        """Build a question dict, filtering out non-actionable options."""
        options = []
        for opt in raw_options:
            label = opt.get("label", "")
            if label.lower() in ("type something.", "chat about this"):
                continue
            options.append(
                QuestionOption(
                    label=label,
                    index=len(options) + 1,
                    description=opt.get("description", ""),
                )
            )
        return {"question": question_text, "options": options}

    def _read_new_lines(self) -> list[dict]:
        """Read new lines from the JSONL since last check."""
        path = self._ensure_jsonl_path()
        if not path:
            return []
        parsed, total = self._parse_jsonl_file(path, offset=self._last_line_count)
        self._last_line_count = total
        return parsed

    def _read_all_transcript_entries(self) -> list[dict[str, Any]]:
        """Read the full JSONL transcript without advancing incremental state."""
        path = self._ensure_jsonl_path()
        if not path:
            return []
        entries, _ = self._parse_jsonl_file(path)
        return entries

    def _chat_sidecar_path(self) -> str:
        """Path to the sidecar chat file for non-JSONL entries (command results, etc.)."""
        return os.path.join(self.working_dir, ".cchost-chat.jsonl")

    def save_chat_entry(self, entry: dict) -> None:
        """Append an entry to the sidecar chat file."""
        import datetime as _dt

        entry.setdefault("_ts", _dt.datetime.now(_dt.timezone.utc).isoformat())
        try:
            with open(self._chat_sidecar_path(), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def raw_transcript(self) -> dict:
        """Public API: return the raw JSONL transcript entries merged with sidecar chat entries."""
        path = self._ensure_jsonl_path()
        if not path:
            entries = []
        else:
            entries, _ = self._parse_jsonl_file(path)

        # Merge sidecar entries (command results, etc.)
        sidecar = self._chat_sidecar_path()
        if os.path.exists(sidecar):
            try:
                with open(sidecar, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
            except OSError:
                pass

        return {"entries": entries, "path": path, "count": len(entries)}

    def _extract_text(self, msg_data: dict) -> str:
        """Extract text content from a JSONL message entry."""
        message = msg_data.get("message", {})
        if not isinstance(message, dict):
            return ""
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
            return "\n".join(parts)
        return ""

    def _wait_for_ready(self, timeout: int = 30, is_resume: bool = False) -> None:
        """Wait for Claude Code to start and accept the trust prompt."""
        # Clean up old JSONL files — but NOT when resuming (--resume needs them)
        if not is_resume:
            project_dir = os.path.expanduser(f"~/.claude/projects/-{self._project_slug}")
            if os.path.isdir(project_dir):
                old_files = glob.glob(os.path.join(project_dir, "*.jsonl"))
                for f in old_files:
                    try:
                        os.remove(f)
                    except OSError:
                        pass

        # Wait for tmux pane to have content
        pane = self._tmux_session.active_window.active_pane
        start = time.time()
        while time.time() - start < timeout:
            try:
                captured = "\n".join(pane.capture_pane(start=-15))
                if "I trust this folder" in captured or "Enter to confirm" in captured:
                    pane.send_keys("", enter=True)  # Accept trust prompt
                    time.sleep(2)
                    continue
                if "❯" in captured:
                    # Drain any existing JSONL lines from startup
                    self._jsonl_path = self._find_jsonl()
                    self._read_new_lines()
                    # Ensure events file exists at session root so hooks find it
                    events_path = os.path.join(self.working_dir, ".cchost-events.jsonl")
                    if not os.path.exists(events_path):
                        open(events_path, "a").close()
                    return
            except Exception:
                logger.debug("Exception during _wait_for_ready poll", exc_info=True)
            time.sleep(1)
        raise TimeoutError(f"Claude Code didn't start within {timeout}s")

    def _tmux_shows_question(self) -> bool:
        """Check if the tmux screen is showing an AskUserQuestion (boolean only)."""
        if self._tmux_session is None:
            return False
        try:
            pane = self._tmux_session.active_window.active_pane
            captured = pane.capture_pane(start=-15)
            screen = "\n".join(captured)
            # Primary: "Enter to select" footer
            if "Enter to select" in screen:
                return True
            # Fallback: cursor on a numbered option (❯ N.)
            if re.search(r"❯\s*\d+\.", screen):
                return True
            return False
        except Exception:
            return False

    def _unanswered_question_from_events(self) -> Optional[dict]:
        """
        Primary source: read structured question data from hook events.
        Returns the last AskUserQuestion that has no matching PostToolUse.
        """
        events_path = os.path.join(self.working_dir, ".cchost-events.jsonl")
        if not os.path.exists(events_path):
            return None

        last_ask = None
        answered_tools: set[str] = set()

        with open(events_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                tool = evt.get("tool_name", "")
                event = evt.get("_cchost_event", "")

                if tool == "AskUserQuestion" and event == "PreToolUse":
                    tool_input = evt.get("tool_input", {})
                    questions = tool_input.get("questions", [])
                    if questions:
                        last_ask = {
                            "tool_use_id": evt.get("tool_use_id", ""),
                            "questions": questions,
                        }
                elif tool == "AskUserQuestion" and event == "PostToolUse":
                    tid = evt.get("tool_use_id", "")
                    if tid:
                        answered_tools.add(tid)

        if last_ask and last_ask.get("tool_use_id") not in answered_tools:
            q = last_ask["questions"][0]
            return self._build_question_result(q.get("question", ""), q.get("options", []))
        return None

    def _unanswered_question_from_jsonl(self) -> Optional[dict]:
        """
        Secondary source: check the JSONL transcript for an AskUserQuestion
        tool_use that has no corresponding tool_result.
        """
        path = self._ensure_jsonl_path()
        if not path:
            return None

        last_ask = None
        answered_tools: set[str] = set()

        entries, _ = self._parse_jsonl_file(path)
        for entry in entries:
            msg = entry.get("message", {})
            content = msg.get("content", []) if isinstance(msg, dict) else []
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                    tool_id = block.get("id", "")
                    inp = block.get("input", {})
                    questions = inp.get("questions", [])
                    if questions:
                        last_ask = {"tool_use_id": tool_id, "questions": questions}
                elif block.get("type") == "tool_result":
                    tid = block.get("tool_use_id", "")
                    if tid:
                        answered_tools.add(tid)

        if last_ask and last_ask.get("tool_use_id") not in answered_tools:
            q = last_ask["questions"][0]
            return self._build_question_result(q.get("question", ""), q.get("options", []))
        return None

    def _question_from_tmux_screen(self) -> Optional[dict]:
        """Last resort: parse question content from tmux screen."""
        try:
            pane = self._tmux_session.active_window.active_pane
            captured = pane.capture_pane(start=-40)
        except Exception:
            return None

        lines = [line.rstrip() for line in captured]
        option_pattern = re.compile(r"^\s*(?:❯\s*)?(\d+)\.\s+(.+)$")

        first_option_idx = -1
        for i, line in enumerate(lines):
            if option_pattern.match(line.strip()):
                first_option_idx = i
                break
        if first_option_idx < 0:
            return None

        sep_before_q = -1
        for i in range(first_option_idx - 1, -1, -1):
            if lines[i].strip().startswith("─") and len(lines[i].strip()) > 10:
                sep_before_q = i
                break

        question_lines = []
        start_idx = sep_before_q + 1 if sep_before_q >= 0 else 0
        for line in lines[start_idx:first_option_idx]:
            stripped = line.strip()
            if stripped and not stripped.startswith(("─", "│", "╭", "╰", "←", "☐", "✔", "Enter")):
                if "❯" not in stripped:
                    question_lines.append(stripped)

        raw_options = []
        for line in lines[first_option_idx:]:
            match = option_pattern.match(line.strip())
            if match:
                raw_options.append({"label": match.group(2).strip()})

        result = self._build_question_result("\n".join(question_lines), raw_options)
        if result["options"]:
            return result
        return None

    def current_question(self) -> Optional[dict]:
        """
        Return the current unanswered question, if any.

        Sources (in priority order):
        1. Hook events — best structured data (from PreToolUse)
        2. JSONL transcript — structured (from tool_use blocks)
        3. tmux screen — last resort, parsed from TUI

        tmux "Enter to select" is used as a boolean gate.
        If the structured source returns a question that doesn't match
        what's on screen, we fall through to the next source.
        """
        if not self._tmux_shows_question():
            return None

        # Get what's visible on screen for validation
        try:
            pane = self._tmux_session.active_window.active_pane
            screen = "\n".join(pane.capture_pane(start=-20))
        except Exception:
            screen = ""

        def _question_matches_screen(q: dict) -> bool:
            """Check if a structured question matches what's on tmux."""
            q_text = q.get("question", "")
            if not q_text:
                return False
            normalized_q = " ".join(q_text[:80].split())
            normalized_screen = " ".join(screen.split())
            return normalized_q[:50] in normalized_screen

        # 1. Hook events (best)
        q = self._unanswered_question_from_events()
        if q and _question_matches_screen(q):
            return q

        # 2. JSONL transcript
        q = self._unanswered_question_from_jsonl()
        if q and _question_matches_screen(q):
            return q

        # 3. tmux screen (last resort)
        return self._question_from_tmux_screen()

    def question_status(self) -> bool:
        """Return whether Claude is currently showing an AskUserQuestion prompt."""
        return self._tmux_shows_question()

    @property
    def is_dormant(self) -> bool:
        """True if session exists in manifest but has no live tmux process."""
        return self._tmux_session is None

    def _is_tmux_idle(self) -> bool:
        """Check if the tmux pane shows Claude Code's idle prompt (❯)."""
        if self._tmux_session is None:
            return False
        try:
            pane = self._tmux_session.active_window.active_pane
            captured = pane.capture_pane(start=-10)
            for line in captured:
                if line.strip() == "❯":
                    return True
            return False
        except Exception:
            return False

    def terminal_capture(self, lines: int = 50) -> str:
        """Capture the current tmux pane output."""
        if self._tmux_session is None:
            return "(session is dormant — not yet resumed)"
        try:
            pane = self._tmux_session.active_window.active_pane
            captured = pane.capture_pane(start=-lines)
            return "\n".join(captured)
        except Exception:
            return ""

    def progress_entries(self) -> list:
        """Return normalized progress events from the full transcript."""
        return normalize_jsonl_entries(self._read_all_transcript_entries())

    def progress_snapshot(self):
        """Return a derived progress snapshot for the current session."""
        return derive_progress_snapshot(
            self.progress_entries(),
            is_question=self.question_status(),
            is_prompt=self._is_tmux_idle(),
        )

    def queue_message(self, message: str) -> dict:
        """Send a message to tmux WITHOUT acquiring _tmux_lock.

        This lets users type follow-up messages while Claude is already working.
        Claude Code CLI natively queues typed input, so the message will be
        processed once the current turn finishes.

        Returns {"status": "queued"|"sent", "was_busy": bool}.
        """
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")

        was_busy = not self._is_tmux_idle()

        pane = self._tmux_session.active_window.active_pane

        if len(message) < 500:
            pane.send_keys(message, enter=True)
        else:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(message)
                tmp_path = f.name
            try:
                server = self._tmux_session.server
                import uuid as _uuid

                buf_name = f"cchost-q-{self.id}-{_uuid.uuid4().hex[:8]}"
                server.cmd("load-buffer", "-b", buf_name, tmp_path)
                pane.cmd("paste-buffer", "-b", buf_name, "-d")
                time.sleep(0.3)
                pane.send_keys("", enter=True)
            finally:
                os.unlink(tmp_path)

        return {
            "status": "queued" if was_busy else "sent",
            "was_busy": was_busy,
        }

    def subagents(self) -> list[dict]:
        """Return a list of sub-agent summaries for this session.

        Scans the subagents directory for JSONL files and extracts a lightweight
        summary from each (reading only the first few and last few lines).
        Returns an empty list if no subagents directory exists or the session
        has no Claude session ID.
        """
        if not self._claude_session_id:
            return []

        subagents_dir = os.path.expanduser(
            f"~/.claude/projects/-{self._project_slug}/{self._claude_session_id}/subagents"
        )
        if not os.path.isdir(subagents_dir):
            return []

        results = []
        try:
            jsonl_files = [f for f in os.listdir(subagents_dir) if f.endswith(".jsonl")]
        except OSError:
            return []

        for filename in jsonl_files:
            filepath = os.path.join(subagents_dir, filename)
            try:
                agent_info = self._parse_subagent_file(filepath)
                if agent_info:
                    results.append(agent_info)
            except Exception:
                logger.debug("Failed to parse subagent file %s", filepath, exc_info=True)

        # Sort by last_activity, most recent first
        results.sort(key=lambda x: x.get("last_activity", ""), reverse=True)
        return results

    @staticmethod
    def _parse_subagent_file(filepath: str) -> Optional[dict]:
        """Parse a subagent JSONL file, reading only the first 10 and last 20 lines."""
        try:
            # Read head (first 10 lines)
            head_lines: list[str] = []
            with open(filepath, "r") as f:
                for _ in range(10):
                    line = f.readline()
                    if not line:
                        break
                    head_lines.append(line)

            # Read tail (last ~32KB) for status detection
            tail_lines: list[str] = []
            file_size = os.path.getsize(filepath)
            with open(filepath, "rb") as f:
                seek_pos = max(0, file_size - 32768)
                f.seek(seek_pos)
                raw = f.read().decode("utf-8", errors="replace")
                tail_lines = raw.splitlines()[-20:]
        except OSError:
            return None

        if not head_lines and not tail_lines:
            return None

        lines = head_lines  # For head parsing below

        # Parse the first few lines to get the agent_id and task description
        agent_id = None
        description = ""
        head_lines = lines[:10]
        for line in head_lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue

            # Extract agent_id from any entry that has it
            if not agent_id and entry.get("agentId"):
                agent_id = entry["agentId"]

            # The first user message is the task description
            if not description and entry.get("type") == "user":
                message = entry.get("message", {})
                if isinstance(message, dict):
                    content = message.get("content", "")
                    if isinstance(content, str):
                        description = content[:200]
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                description = (block.get("text") or "")[:200]
                                break
                elif isinstance(message, str):
                    description = message[:200]

        # Parse the last few lines for status and last_activity
        last_entry = None
        last_timestamp = ""
        for line in reversed(tail_lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if last_entry is None:
                last_entry = entry
            # Extract agent_id if we still don't have it
            if not agent_id and entry.get("agentId"):
                agent_id = entry["agentId"]
            # Extract timestamp
            if not last_timestamp:
                ts = entry.get("timestamp") or entry.get("_timestamp") or entry.get("ts") or ""
                if ts:
                    last_timestamp = str(ts)
            if last_entry is not None and agent_id:
                break

        if not agent_id:
            # Derive from filename as fallback (agent-{id}.jsonl)
            basename = os.path.basename(filepath)
            agent_id = basename.replace(".jsonl", "")

        # Determine status: "completed" if last entry is last-prompt, or an assistant
        # message with stop_reason "end_turn" (sub-agents don't write last-prompt)
        status = "running"
        if last_entry:
            if last_entry.get("type") == "last-prompt":
                status = "completed"
            elif last_entry.get("type") == "assistant":
                msg = last_entry.get("message", {})
                if isinstance(msg, dict) and msg.get("stop_reason") == "end_turn":
                    status = "completed"

        # Use file mtime as fallback for last_activity
        if not last_timestamp:
            try:
                mtime = os.path.getmtime(filepath)
                last_timestamp = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except OSError:
                last_timestamp = ""

        return {
            "agent_id": agent_id,
            "description": description,
            "status": status,
            "last_activity": last_timestamp,
        }

    def _send_message_to_tmux(self, message: str) -> None:
        """Send a message to Claude Code via tmux, handling long messages safely."""
        if not self._tmux_lock.acquire(timeout=5):
            raise RuntimeError("Session is busy — try again in a moment")
        try:
            pane = self._tmux_session.active_window.active_pane

            # For short messages, send_keys works fine
            if len(message) < 500:
                pane.send_keys(message, enter=True)
                return

            # For long messages, use tmux load-buffer to avoid paste corruption

            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(message)
                tmp_path = f.name

            try:
                server = self._tmux_session.server
                buf_name = f"cchost-{self.id}"
                server.cmd("load-buffer", "-b", buf_name, tmp_path)
                pane.cmd("paste-buffer", "-b", buf_name, "-d")
                time.sleep(0.3)
                pane.send_keys("", enter=True)
            finally:
                os.unlink(tmp_path)
        finally:
            self._tmux_lock.release()

    def send(self, message: str, timeout: int = 600) -> Response:
        """Send a message and wait for Claude's response."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")

        self._send_message_to_tmux(message)

        # Verify Claude started processing (JSONL or events activity within 15s)
        start = time.time()
        while time.time() - start < 15:
            # Check if Claude is no longer idle (prompt disappeared)
            if not self._is_tmux_idle():
                break
            time.sleep(1)
        else:
            # Still idle after 15s, message may not have been delivered
            # Try pressing Enter again as a fallback
            pane = self._tmux_session.active_window.active_pane
            pane.send_keys("", enter=True)
            time.sleep(2)

        return self._wait_for_response(timeout)

    def _find_cursor_position(self) -> int:
        """Find which option the cursor (❯) is currently on (1-based)."""
        try:
            pane = self._tmux_session.active_window.active_pane
            captured = pane.capture_pane(start=-30)
            option_pattern = re.compile(r"^\s*(❯)?\s*(\d+)\.\s+")
            for line in captured:
                match = option_pattern.match(line)
                if match and match.group(1):  # has ❯
                    return int(match.group(2))
        except Exception:
            pass
        return 1

    def _navigate_to_option(self, target: int) -> None:
        """Navigate cursor to a specific option number."""
        pane = self._tmux_session.active_window.active_pane
        current = self._find_cursor_position()
        diff = target - current
        key = "Down" if diff > 0 else "Up"
        for _ in range(abs(diff)):
            pane.send_keys(key, enter=False)
            time.sleep(0.1)

    def toggle_option(self, option_index: int) -> None:
        """Toggle a checkbox in a multi-select question (space key)."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        with self._tmux_lock:
            pane = self._tmux_session.active_window.active_pane
            self._navigate_to_option(option_index)
            time.sleep(0.1)
            pane.send_keys("Space", enter=False)
            time.sleep(0.2)

    def submit_multiselect(self, timeout: int = 600) -> Response:
        """Submit a multi-select question by pressing Tab to reach Submit, then Enter."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        with self._tmux_lock:
            pane = self._tmux_session.active_window.active_pane

            # In Claude Code's multi-select, Tab cycles through the tab bar
            # (Markup Rate, Billing Dates, SM Tech, Watchlist, Submit)
            # Press Tab until we land on Submit, then Enter
            # The tab bar shows ✔ Submit when it's focused
            for _ in range(10):
                pane.send_keys("Tab", enter=False)
                time.sleep(0.3)
                try:
                    captured = pane.capture_pane(start=-5)
                    screen = "\n".join(captured)
                    # Check if Submit is now the active tab (indicated by ✔ before Submit)
                    # or if the question UI has disappeared (submit happened)
                    if "Enter to select" not in screen:
                        break
                except Exception:
                    break

            # Press Enter to confirm
            pane.send_keys("", enter=True)
            time.sleep(0.5)
        return self._wait_for_response(timeout)

    def answer(self, option_index: int = 1, timeout: int = 600) -> Response:
        """
        Answer an AskUserQuestion by navigating to the right option and pressing Enter.
        For single-select: navigates and presses Enter.
        For multi-select: this toggles the option (use submit_multiselect to finalize).
        """
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")

        with self._tmux_lock:
            pane = self._tmux_session.active_window.active_pane
            self._navigate_to_option(option_index)
            time.sleep(0.1)

            # Press Enter to select
            pane.send_keys("", enter=True)
            time.sleep(0.5)

        # Now wait for the next response using the same logic as send()
        return self._wait_for_response(timeout)

    def _reset_cursors(self) -> tuple[str, int]:
        """Reset JSONL and events cursors for a new response wait. Returns (events_path, events_offset)."""
        self._jsonl_path = self._find_jsonl()
        if self._jsonl_path:
            with open(self._jsonl_path, "rb") as f:
                self._last_line_count = sum(1 for _ in f)
        else:
            self._last_line_count = 0

        events_path = os.path.join(self.working_dir, ".cchost-events.jsonl")
        events_offset = 0
        if os.path.exists(events_path):
            with open(events_path, "rb") as f:
                events_offset = f.seek(0, 2)
        return events_path, events_offset

    def _poll_once(
        self,
        state: dict,
    ) -> Optional[Response]:
        """
        Single poll iteration. Reads from all sources.
        Returns a Response if done, or None to keep polling.

        `state` is a mutable dict with keys:
          last_assistant_text, last_assistant_raw, saw_any_activity,
          events_path, events_offset, start_time
        """
        # Early detection: if no activity after 30s, message may not have been delivered
        elapsed = time.time() - state["start_time"]
        if not state["saw_any_activity"] and elapsed > 30 and self._is_tmux_idle():
            return Response(
                text="(message may not have been delivered, Claude is still idle)",
                role="assistant",
            )

        # Check JSONL transcript
        new_lines = self._read_new_lines()
        for entry in new_lines:
            state["saw_any_activity"] = True
            entry_type = entry.get("type", "")
            if entry_type == "assistant":
                text = self._extract_text(entry)
                if text:
                    state["last_assistant_text"] = text
                    state["last_assistant_raw"] = entry
            elif entry_type == "last-prompt":
                return Response(
                    text=state["last_assistant_text"],
                    role="assistant",
                    raw=state["last_assistant_raw"],
                )

        # Check for question on screen
        question = self.current_question()
        if question:
            return Response(
                text=question["question"],
                role="assistant",
                is_question=True,
                questions=[question],
            )

        # Check hook events
        events_path = state["events_path"]
        if os.path.exists(events_path):
            with open(events_path, "rb") as f:
                f.seek(state["events_offset"])
                new_data = f.read()
                state["events_offset"] = f.tell()
            new_events = new_data.decode("utf-8", errors="replace").splitlines(True)

            for raw_line in new_events:
                # Skip partial writes (line not terminated with newline)
                if not raw_line.endswith("\n"):
                    continue
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    evt = json.loads(raw_line)
                    if evt.get("_cchost_event") == "Stop":
                        time.sleep(0.5)
                        for entry in self._read_new_lines():
                            if entry.get("type") == "assistant":
                                text = self._extract_text(entry)
                                if text:
                                    state["last_assistant_text"] = text
                                    state["last_assistant_raw"] = entry

                        time.sleep(1.0)
                        question = self.current_question()
                        if question:
                            return Response(
                                text=state["last_assistant_text"],
                                role="assistant",
                                is_question=True,
                                questions=[question],
                            )

                        return Response(
                            text=state["last_assistant_text"],
                            role="assistant",
                            raw=state["last_assistant_raw"],
                        )
                except json.JSONDecodeError:
                    pass

        return None

    def _wait_for_response(self, timeout: int = 600) -> Response:
        """Wait for Claude's response after sending a message or answering a question."""
        events_path, events_offset = self._reset_cursors()

        state = {
            "last_assistant_text": "",
            "last_assistant_raw": {},
            "saw_any_activity": False,
            "events_path": events_path,
            "events_offset": events_offset,
            "start_time": time.time(),
        }

        while time.time() - state["start_time"] < timeout:
            result = self._poll_once(state)
            if result is not None:
                return result
            time.sleep(0.5)

        return Response(text="(timeout waiting for response)", role="assistant")

    def send_keys(self, keys: str) -> None:
        """Send raw keys to tmux (for Ctrl+C, Enter, etc.)."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        pane = self._tmux_session.active_window.active_pane
        pane.send_keys(keys, enter=False)

    def interrupt(self) -> None:
        """Send Ctrl+C (escape) to Claude Code."""
        self.send_keys("C-c")

    def btw(self, question: str, timeout: int = 30, lock_timeout: int = 30) -> str:
        """Ask a /btw side question. Ephemeral — doesn't enter conversation history.

        Sends '/btw <question>' to the tmux pane, waits for the response overlay,
        captures the text, and dismisses it with Escape.
        """
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        if not self._tmux_lock.acquire(timeout=lock_timeout):
            raise RuntimeError("Session is busy (locked)")
        try:
            # Wait for idle (previous btw overlay may still be dismissing)
            for _ in range(15):
                if self._is_tmux_idle():
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Session is busy")

            pane = self._tmux_session.active_window.active_pane
            pane.send_keys(f"/btw {question}", enter=True)

            # Wait for the response overlay (ends with "to dismiss")
            start = time.time()
            response_lines: list[str] = []
            q_prefix = question[:30]
            while time.time() - start < timeout:
                time.sleep(1.0)
                content = pane.cmd("capture-pane", "-p").stdout
                full_text = "\n".join(content) if isinstance(content, list) else str(content)
                # The footer "to dismiss" appears immediately with the spinner.
                # Wait for the answer to actually render (spinner text goes away).
                if "Answering" in full_text and _has_overlay_footer(full_text):
                    continue
                if _has_overlay_footer(full_text):
                    # Capture full pane including scrollback
                    # -S - starts from beginning, -E - ends at bottom
                    full_content = pane.cmd("capture-pane", "-p", "-S", "-", "-E", "-").stdout
                    full_text_all = "\n".join(full_content) if isinstance(full_content, list) else str(full_content)

                    lines = full_text_all.split("\n")
                    # The overlay echoes "/btw <question>" then shows the answer.
                    # Find the LAST occurrence of the question echo, capture after it.
                    last_q_idx = -1
                    for i, line in enumerate(lines):
                        if q_prefix in line and "/btw" in line:
                            last_q_idx = i
                    # Capture from after the last question echo to "to dismiss"
                    if last_q_idx >= 0:
                        for line in lines[last_q_idx + 1 :]:
                            stripped = line.strip()
                            if _is_overlay_footer_line(stripped):
                                break
                            if stripped:
                                response_lines.append(stripped)
                    break

            # Dismiss the overlay and wait for it to clear
            pane.send_keys("Escape", enter=False)
            time.sleep(1.5)

            return "\n".join(response_lines)
        finally:
            self._tmux_lock.release()

    def slash_command(self, command: str, timeout: int = 30) -> dict:
        """Execute a slash command and capture the result.

        Returns {"type": "overlay"|"response"|"instant", "content": str}
        """
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        if not self._tmux_lock.acquire(timeout=5):
            raise RuntimeError("Session is busy (locked)")
        try:
            if not self._is_tmux_idle():
                raise RuntimeError("Session is busy")

            pane = self._tmux_session.active_window.active_pane
            pane.send_keys(command, enter=True)

            # Wait and detect what kind of response we get
            start = time.time()
            while time.time() - start < timeout:
                time.sleep(1.0)
                content = pane.cmd("capture-pane", "-p").stdout
                full_text = "\n".join(content) if isinstance(content, list) else str(content)

                # Check for overlay response (like /help, /status, /cost)
                if _has_overlay_footer(full_text):
                    lines = full_text.split("\n")
                    response_lines = []
                    # Capture all non-empty lines before the footer,
                    # skipping leading separator lines (─────)
                    started = False
                    for line in lines:
                        stripped = line.strip()
                        if _is_overlay_footer_line(stripped):
                            break
                        if not started and (not stripped or all(c in "─━═" for c in stripped)):
                            continue
                        started = True
                        response_lines.append(stripped)
                    # Dismiss the overlay
                    pane.send_keys("Escape", enter=False)
                    time.sleep(0.3)
                    result_text = "\n".join(response_lines)
                    # Persist to sidecar so it shows in chat on refresh
                    self.save_chat_entry(
                        {
                            "type": "command",
                            "command": command,
                            "content": result_text,
                        }
                    )
                    return {"type": "overlay", "content": result_text}

                # Check if Claude started working (no longer idle, JSONL activity)
                if not self._is_tmux_idle() and not _has_overlay_footer(full_text):
                    # It's a regular response — release lock and use _wait_for_response
                    self._tmux_lock.release()
                    try:
                        response = self._wait_for_response(timeout)
                        return {"type": "response", "content": response.text}
                    except Exception as e:
                        return {"type": "response", "content": str(e)}

                # Check if it was instant (prompt came back quickly, nothing happened)
                if time.time() - start > 3 and self._is_tmux_idle():
                    return {"type": "instant", "content": ""}

            return {"type": "instant", "content": ""}
        finally:
            if self._tmux_lock.locked():
                self._tmux_lock.release()

    def summary(self) -> dict:
        """Fast, non-blocking. Reads cache or falls back to raw JSONL."""
        cache_path = os.path.join(self.working_dir, ".cchost-summary.json")

        # Read cache
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    cached = json.load(f)
                return {"title": cached.get("title", ""), "status": cached.get("status", "")}
            except (json.JSONDecodeError, OSError):
                pass

        # Fallback: raw JSONL (fast)
        title = ""
        status = ""
        records = self._read_all_transcript_entries()
        for record in records:
            rtype = record.get("type", "")
            if rtype == "user" and not title:
                text = self._extract_text(record).strip()
                if text and len(text) < 2000:
                    title = text[:80]
            elif rtype == "assistant":
                text = self._extract_text(record).strip()
                if text:
                    status = text[:120]
        return {"title": title, "status": status}

    def generate_summary(self) -> dict:
        """Slow. Calls /btw to generate a smart title. Call from background thread only."""
        cache_path = os.path.join(self.working_dir, ".cchost-summary.json")
        jsonl_path = self._ensure_jsonl_path()
        jsonl_size = os.path.getsize(jsonl_path) if jsonl_path and os.path.exists(jsonl_path) else 0

        # Check if cache is fresh enough
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    cached = json.load(f)
                cached_size = cached.get("_jsonl_size", 0)
                # If has a btw-generated title and JSONL hasn't grown much, skip
                if cached.get("_generated") and jsonl_size - cached_size < max(cached_size * 0.1, 50_000):
                    return {"title": cached.get("title", ""), "status": cached.get("status", "")}
            except (json.JSONDecodeError, OSError):
                pass

        title = ""
        status = ""
        try:
            if self._is_tmux_idle():
                response = self.btw(
                    'Respond ONLY with JSON, no markdown: {"title": "<3-6 word session title>", "status": "<current activity under 10 words>"}'
                )
                flat = " ".join(response.split())
                match = re.search(r'\{[^{}]*"title"[^{}]*\}', flat)
                if match:
                    parsed = json.loads(match.group())
                    title = parsed.get("title", "")[:80]
                    status = parsed.get("status", "")[:120]
        except Exception as e:
            logger.debug("btw summary generation failed: %s", e)

        if title:
            try:
                with open(cache_path, "w") as f:
                    json.dump({"title": title, "status": status, "_jsonl_size": jsonl_size, "_generated": True}, f)
            except OSError:
                pass

        return {"title": title, "status": status}

    def generate_gmail_suggestions(self) -> list[dict]:
        """Generate Gmail search suggestions based on conversation context. Calls /btw."""
        cache_path = os.path.join(self.working_dir, "suggested-searches.json")
        jsonl_path = self._ensure_jsonl_path()
        jsonl_size = os.path.getsize(jsonl_path) if jsonl_path and os.path.exists(jsonl_path) else 0

        # Check if cache is fresh enough
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    cached = json.load(f)
                cached_size = cached.get("_jsonl_size", 0)
                if jsonl_size - cached_size < max(cached_size * 0.1, 50_000):
                    return cached.get("suggestions", [])
            except (json.JSONDecodeError, OSError):
                pass

        suggestions: list[dict] = []
        try:
            if not self._is_tmux_idle():
                return []
            # Short prompt → short response → fits on one screen (no scroll issues)
            response = self.btw(
                "Suggest 3 Gmail searches for this conversation. "
                'JSON only: {"suggestions":[{"label":"2-4 words","query":"gmail query"}]}'
            )
            if response:
                flat = " ".join(response.split())
                # Find last complete JSON object
                candidates = []
                depth = 0
                obj_start = -1
                for i, ch in enumerate(flat):
                    if ch == "{":
                        if depth == 0:
                            obj_start = i
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0 and obj_start >= 0:
                            candidates.append(flat[obj_start : i + 1])
                            obj_start = -1
                for candidate in reversed(candidates):
                    try:
                        parsed = json.loads(candidate)
                        sug = parsed.get("suggestions", [])
                        # Filter out echo-back of our example prompt
                        sug = [s for s in sug if s.get("label") not in ("2-4 words", "2-4 word label")]
                        if sug:
                            suggestions = sug[:5]
                            break
                    except json.JSONDecodeError:
                        pass
            if suggestions:
                with open(cache_path, "w") as f:
                    json.dump({"suggestions": suggestions, "_jsonl_size": jsonl_size}, f, indent=2)
                logger.info("gmail_suggestions %s: %d chips cached", self.id, len(suggestions))
        except Exception as e:
            logger.warning("btw gmail suggestions failed for %s: %s", self.id, e)

        return suggestions

    def conversation(self) -> list[dict]:
        """Reconstruct full conversation from the JSONL transcript.

        The JSONL is the single source of truth. We extract:
        - Assistant text blocks (merged when consecutive)
        - AskUserQuestion tool_use → rendered as question with options
        - AskUserQuestion tool_result → user's selected answer
        - Real user messages (the ones the human typed)

        We skip:
        - Tool results for non-question tools (Bash output, file reads, etc.)
        - Skill/system prompts injected as user messages (>2000 chars)
        - Empty entries, tool_reference entries, permission/system entries
        """
        path = self._ensure_jsonl_path()
        if not path:
            return []

        entries: list[dict] = []
        # Track AskUserQuestion tool IDs so we can match answers
        ask_tool_ids: set[str] = set()

        def append_assistant(text: str, is_question: bool = False) -> None:
            if not text:
                return
            entry: dict = {"role": "assistant", "text": text}
            if is_question:
                entry["is_question"] = True
            entries.append(entry)

        records, _ = self._parse_jsonl_file(path)
        for record in records:

            rtype = record.get("type", "")
            msg = record.get("message", {})
            content = msg.get("content", []) if isinstance(msg, dict) else []
            if not isinstance(content, list):
                content = []

            if rtype == "assistant":
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")

                    if btype == "text":
                        text = block.get("text", "").strip()
                        append_assistant(text)

                    elif btype == "tool_use":
                        tool_id = block.get("id", "")
                        tool_name = block.get("name", "")
                        if tool_name == "AskUserQuestion":
                            if tool_id:
                                ask_tool_ids.add(tool_id)
                            inp = block.get("input", {})
                            questions = inp.get("questions", [])
                            if questions:
                                q = questions[0]
                                q_text = q.get("question", "")
                                options = q.get("options", [])
                                opt_lines = []
                                for opt in options:
                                    label = opt.get("label", "")
                                    desc = opt.get("description", "")
                                    opt_lines.append(f"- **{label}**: {desc}" if desc else f"- **{label}**")
                                full_text = q_text
                                if opt_lines:
                                    full_text += "\n\n" + "\n".join(opt_lines)
                                append_assistant(full_text, is_question=True)

            elif rtype == "user":
                # First pass: check if this is purely tool_results
                has_tool_result = False
                has_real_text = False
                for block in content:
                    if isinstance(block, str):
                        if block.strip():
                            has_real_text = True
                    elif isinstance(block, dict):
                        if block.get("type") == "tool_result":
                            has_tool_result = True
                        elif block.get("type") == "text":
                            if block.get("text", "").strip():
                                has_real_text = True

                if has_tool_result:
                    # Extract AskUserQuestion answers
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_result":
                            continue
                        tool_id = block.get("tool_use_id", "")
                        if tool_id in ask_tool_ids:
                            result_content = block.get("content", [])
                            answer_text = ""
                            if isinstance(result_content, list):
                                for rc in result_content:
                                    if isinstance(rc, dict) and rc.get("type") == "text":
                                        answer_text = rc.get("text", "").strip()
                            elif isinstance(result_content, str):
                                answer_text = result_content.strip()
                            if answer_text:
                                entries.append({"role": "user", "text": answer_text})
                            ask_tool_ids.discard(tool_id)
                    # Skip non-question tool results (Bash output etc.)
                    if not has_real_text:
                        continue

                # Real user message
                text = self._extract_text(record)
                if not text or text.strip() in ("", "."):
                    continue
                # Filter skill/system prompts
                if len(text) > 2000:
                    continue
                entries.append({"role": "user", "text": text})

        return entries

    def files(self) -> list[str]:
        """List files in the working directory (excluding hidden)."""
        result = []
        for root, dirs, filenames in os.walk(self.working_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in filenames:
                if f.startswith("."):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, self.working_dir)
                result.append(rel)
        return sorted(result)

    def read_file(self, relative_path: str) -> bytes:
        """Read a file from the working directory. Path-traversal safe."""
        target = Path(self.working_dir) / relative_path
        resolved = target.resolve()
        workdir_resolved = Path(self.working_dir).resolve()
        if not str(resolved).startswith(str(workdir_resolved)):
            raise ValueError(f"Path traversal blocked: {relative_path}")
        return resolved.read_bytes()

    def destroy(self) -> None:
        """Kill the tmux session."""
        if self._tmux_session is not None:
            try:
                self._tmux_session.kill()
            except Exception:
                pass
            self._tmux_session = None


class CCHost:
    """
    Manages persistent Claude Code sessions.

    Each session runs Claude Code interactively in tmux. Responses are
    read from Claude's JSONL conversation log, not from TUI capture.

    Sessions are persisted to a manifest file (~/.cchost/sessions.json)
    so they survive server restarts. On startup, sessions from the manifest
    are loaded as "dormant" (no tmux process). When accessed, they are
    lazily resumed with claude --resume.
    """

    def __init__(self, max_sessions: int = 5, history_limit: int = 50000, manifest_path: Optional[str] = None):
        self._server = libtmux.Server()
        self._sessions: dict[str, CCSession] = {}
        self._max_sessions = max_sessions
        self._history_limit = history_limit
        self._resume_lock = threading.Lock()
        self._manifest_path_override = manifest_path
        self._rediscover()
        self._load_dormant_sessions()
        # Persist any sessions found via tmux rediscovery
        if self._sessions:
            self._save_manifest()

    def _rediscover(self) -> None:
        """Find existing cchost-* tmux sessions."""
        for tmux_session in self._server.sessions:
            name = tmux_session.name
            if name.startswith("cchost-"):
                session_id = name[len("cchost-") :]
                if session_id not in self._sessions:
                    pane = tmux_session.active_window.active_pane
                    workdir = pane.pane_current_path or "/tmp"
                    session = CCSession(
                        id=session_id,
                        working_dir=workdir,
                        _tmux_session=tmux_session,
                        _host=self,
                    )
                    session._find_jsonl()  # populate _claude_session_id
                    self._sessions[session_id] = session

    # ------------------------------------------------------------------
    # Manifest persistence
    # ------------------------------------------------------------------

    def _manifest_path(self) -> str:
        if self._manifest_path_override:
            return self._manifest_path_override
        return os.path.expanduser("~/.cchost/sessions.json")

    def _load_manifest(self) -> dict:
        path = self._manifest_path()
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_manifest(self) -> None:
        path = self._manifest_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        manifest = {}
        for sid, session in self._sessions.items():
            manifest[sid] = {
                "working_dir": session.working_dir,
                "claude_session_id": session._claude_session_id,
                "created_at": session.created_at.isoformat(),
            }
        # Atomic write: write to temp file then rename
        tmp_path = path + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(manifest, f, indent=2)
        os.replace(tmp_path, path)

    def _load_dormant_sessions(self) -> None:
        """Load sessions from manifest that don't have a live tmux process."""
        manifest = self._load_manifest()
        for session_id, meta in manifest.items():
            if session_id in self._sessions:
                continue  # already rediscovered from tmux
            working_dir = meta.get("working_dir", "/tmp")
            if not os.path.isdir(working_dir):
                logger.info("Skipping dormant session %s — working_dir %s gone", session_id, working_dir)
                continue
            created_str = meta.get("created_at")
            try:
                created_at = datetime.fromisoformat(created_str) if created_str else datetime.now(timezone.utc)
            except (ValueError, TypeError):
                created_at = datetime.now(timezone.utc)
            self._sessions[session_id] = CCSession(
                id=session_id,
                working_dir=working_dir,
                created_at=created_at,
                _tmux_session=None,  # dormant — no tmux process
                _host=self,
                _claude_session_id=meta.get("claude_session_id"),
            )
        logger.info(
            "Loaded %d dormant sessions from manifest",
            sum(1 for s in self._sessions.values() if s._tmux_session is None),
        )

    # ------------------------------------------------------------------
    # Resume
    # ------------------------------------------------------------------

    def _resume_session(self, session: CCSession) -> None:
        """Spin up a tmux process for a dormant session using claude --resume."""
        with self._resume_lock:
            if session._tmux_session is not None:
                return  # already resumed (another thread got here first)

            tmux_name = f"cchost-{session.id}"
            cchost_url = os.environ.get("CCHOST_API_URL", "http://localhost:8420")

            resume_flag = ""
            if session._claude_session_id:
                resume_flag = f"--resume {session._claude_session_id} "

            tmux_session = self._server.new_session(
                session_name=tmux_name,
                start_directory=session.working_dir,
                window_command=f"CCHOST_URL={cchost_url} claude {resume_flag}--dangerously-skip-permissions",
            )
            tmux_session.set_option("history-limit", self._history_limit)
            session._tmux_session = tmux_session
            session._wait_for_ready(is_resume=bool(session._claude_session_id))
            # Re-resolve JSONL path after resume (may be a new file)
            session._jsonl_path = None
            session._last_line_count = 0
            session._find_jsonl()
            logger.info("Resumed session %s (claude_session_id=%s)", session.id, session._claude_session_id)

    def create(
        self,
        session_id: str,
        working_dir: str = "/tmp",
        wait_ready: bool = True,
    ) -> CCSession:
        """Create a new Claude Code session."""
        if session_id in self._sessions:
            raise ValueError(f"Session {session_id} already exists")
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(f"Max sessions ({self._max_sessions}) reached")
        if not re.match(r"^[a-zA-Z0-9_-]+$", session_id):
            raise ValueError(f"Invalid session ID: {session_id}")

        os.makedirs(working_dir, exist_ok=True)
        tmux_name = f"cchost-{session_id}"

        # Set CCHOST_URL so skills can call back into the API
        cchost_url = os.environ.get("CCHOST_API_URL", "http://localhost:8420")

        tmux_session = self._server.new_session(
            session_name=tmux_name,
            start_directory=working_dir,
            window_command=f"CCHOST_URL={cchost_url} claude --dangerously-skip-permissions",
        )
        tmux_session.set_option("history-limit", self._history_limit)

        session = CCSession(
            id=session_id,
            working_dir=working_dir,
            _tmux_session=tmux_session,
            _host=self,
        )
        self._sessions[session_id] = session

        if wait_ready:
            session._wait_for_ready()

        # Persist to manifest so session survives restarts
        session._find_jsonl()  # populate _claude_session_id
        self._save_manifest()

        return session

    def get(self, session_id: str) -> CCSession:
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        session = self._sessions[session_id]
        # Lazy resume: spin up tmux if dormant
        if session._tmux_session is None:
            self._resume_session(session)
            self._save_manifest()  # update claude_session_id after resume
        return session

    def list(self) -> list[CCSession]:
        return list(self._sessions.values())

    def destroy(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found")
        session.destroy()
        self._sessions.pop(session_id, None)
        self._save_manifest()

    def destroy_all(self) -> None:
        for sid in list(self._sessions.keys()):
            self.destroy(sid)
        self._save_manifest()


# ============================================================
# Topic Manager
# ============================================================

TOPICS_DIR = os.path.expanduser("~/cchost-topics")


class TopicManager:
    """Manages persistent project workspaces (topics) that group conversations with Claude Code.

    Each topic is a directory under ~/cchost-topics/ containing a .topic.json
    metadata file and any files created during conversations.
    """

    def __init__(self, host: CCHost):
        self.host = host
        os.makedirs(TOPICS_DIR, exist_ok=True)

    def _validate_slug(self, slug: str) -> str:
        """Validate slug is safe and return the resolved topic_dir. Prevents path traversal."""
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?", slug):
            raise KeyError(f"Invalid topic slug: {slug}")
        topic_dir = os.path.join(TOPICS_DIR, slug)
        resolved = os.path.realpath(topic_dir)
        if not resolved.startswith(os.path.realpath(TOPICS_DIR) + os.sep):
            raise KeyError(f"Invalid topic slug: {slug}")
        return resolved

    def create_topic(self, name: str) -> dict:
        """Create a new topic directory with .topic.json metadata."""
        slug = self._slugify(name)
        topic_dir = os.path.join(TOPICS_DIR, slug)
        os.makedirs(topic_dir, exist_ok=True)
        metadata = {
            "name": name,
            "slug": slug,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "conversations": [],
        }
        self._write_metadata(topic_dir, metadata)
        return metadata

    def list_topics(self) -> list[dict]:
        """List all topics with their metadata."""
        topics: list[dict] = []
        if not os.path.isdir(TOPICS_DIR):
            return topics
        for entry in sorted(os.listdir(TOPICS_DIR)):
            topic_dir = os.path.join(TOPICS_DIR, entry)
            if not os.path.isdir(topic_dir):
                continue
            try:
                metadata = self._read_metadata(topic_dir)
                topics.append(metadata)
            except (json.JSONDecodeError, OSError, KeyError):
                logger.debug("Skipping corrupt topic dir: %s", topic_dir)
                continue
        return topics

    def get_topic(self, slug: str) -> dict:
        """Get topic metadata. Raises KeyError if not found."""
        topic_dir = self._validate_slug(slug)
        if not os.path.isdir(topic_dir):
            raise KeyError(f"Topic {slug} not found")
        try:
            return self._read_metadata(topic_dir)
        except (json.JSONDecodeError, OSError) as exc:
            raise KeyError(f"Topic {slug} has corrupt metadata") from exc

    def delete_topic(self, slug: str) -> None:
        """Delete a topic directory. Raises RuntimeError if an active conversation exists."""
        topic = self.get_topic(slug)  # validates slug
        topic_dir = self._validate_slug(slug)

        # Check if any conversation in the topic has an active session in self.host
        for conv in topic.get("conversations", []):
            if conv.get("status") == "active":
                session_id = conv.get("session_id")
                if session_id:
                    try:
                        session = self.host.get(session_id)
                        if session._tmux_session is not None:
                            raise RuntimeError("Cannot delete topic with active conversation")
                    except KeyError:
                        pass  # session not in host, safe to delete

        shutil.rmtree(topic_dir)

    def start_conversation(self, slug: str) -> CCSession:
        """Start a new conversation in the topic. Stops any active conversation first."""
        topic = self.get_topic(slug)  # validates slug
        topic_dir = self._validate_slug(slug)

        # Stop any active conversation
        for conv in topic.get("conversations", []):
            if conv.get("status") == "active":
                try:
                    session = self.host.get(conv["session_id"])
                    session.destroy()
                except KeyError:
                    pass
                conv["status"] = "completed"

        # Persist stopped-conversation status before creating new session
        self._write_metadata(topic_dir, topic)

        # Create new session with topic dir as working_dir
        conv_id = f"conv-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        session = self.host.create(conv_id, working_dir=topic_dir)

        # Record conversation in metadata
        topic["conversations"].append(
            {
                "id": conv_id,
                "session_id": session.id,
                "claude_session_id": session._claude_session_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "title": "",
                "status": "active",
            }
        )
        self._write_metadata(topic_dir, topic)
        return session

    def resume_conversation(self, slug: str, conv_id: str) -> CCSession:
        """Resume a previous conversation. The session must exist in CCHost."""
        topic = self.get_topic(slug)
        conv = next((c for c in topic["conversations"] if c["id"] == conv_id), None)
        if not conv:
            raise KeyError(f"Conversation {conv_id} not found in topic {slug}")

        # Try to get existing session (may be dormant — host.get triggers lazy resume)
        session = self.host.get(conv["session_id"])

        # Update status
        conv["status"] = "active"
        topic_dir = self._validate_slug(slug)
        self._write_metadata(topic_dir, topic)
        return session

    def generate_context(self, slug: str) -> str:
        """Generate CLAUDE.md for a topic using /btw. Returns the generated content."""
        topic = self.get_topic(slug)
        topic_dir = self._validate_slug(slug)

        # Find an idle session in this topic to run /btw
        for conv in reversed(topic.get("conversations", [])):
            try:
                session = self.host.get(conv["session_id"])
                if session._is_tmux_idle():
                    prompt = (
                        "Look at the files in this directory and the conversation history. "
                        "Write a CLAUDE.md project context file. Include: project overview "
                        "(1-2 sentences), key decisions made, important files and what they "
                        "contain, current status. Keep it under 500 words. "
                        "Output ONLY the markdown content, no explanation."
                    )
                    content = session.btw(prompt, timeout=90)
                    if content and len(content) > 20:
                        claude_md_path = os.path.join(topic_dir, "CLAUDE.md")
                        with open(claude_md_path, "w") as f:
                            f.write(content)
                        return content
            except Exception as e:
                logger.debug("generate_context failed for %s: %s", slug, e)
        return ""

    def _slugify(self, name: str) -> str:
        """Convert name to a filesystem-safe slug. Handle collisions with -2, -3, etc."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
        if not slug:
            slug = "topic"
        base = slug
        counter = 2
        while os.path.exists(os.path.join(TOPICS_DIR, slug)):
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    def _write_metadata(self, topic_dir: str, metadata: dict) -> None:
        """Write .topic.json metadata to a topic directory."""
        path = os.path.join(topic_dir, ".topic.json")
        with open(path, "w") as f:
            json.dump(metadata, f, indent=2)

    def _read_metadata(self, topic_dir: str) -> dict:
        """Read .topic.json metadata from a topic directory."""
        path = os.path.join(topic_dir, ".topic.json")
        with open(path, "r") as f:
            return json.load(f)
