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
import tempfile
import time
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

    @property
    def _project_slug(self) -> str:
        return self.working_dir.replace("/", "-").lstrip("-")

    def _find_jsonl(self) -> Optional[str]:
        """Find the JSONL conversation log for this session."""
        project_dir = os.path.expanduser(f"~/.claude/projects/-{self._project_slug}")
        if os.path.isdir(project_dir):
            files = glob.glob(os.path.join(project_dir, "*.jsonl"))
            if files:
                return max(files, key=os.path.getmtime)
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

    def raw_transcript(self) -> dict:
        """Public API: return the raw JSONL transcript entries, path, and count."""
        path = self._ensure_jsonl_path()
        if not path:
            return {"entries": [], "path": None, "count": 0}
        entries, _ = self._parse_jsonl_file(path)
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

    def _wait_for_ready(self, timeout: int = 30) -> None:
        """Wait for Claude Code to start and accept the trust prompt."""
        # Clean up old JSONL files from previous sessions in the same workdir
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
                    return
            except Exception:
                logger.debug("Exception during _wait_for_ready poll", exc_info=True)
            time.sleep(1)
        raise TimeoutError(f"Claude Code didn't start within {timeout}s")

    def _tmux_shows_question(self) -> bool:
        """Check if the tmux screen is showing an AskUserQuestion (boolean only)."""
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
            return bool(q_text) and q_text[:50] in screen

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

    def _is_tmux_idle(self) -> bool:
        """Check if the tmux pane shows Claude Code's idle prompt (❯)."""
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

    def _send_message_to_tmux(self, message: str) -> None:
        """Send a message to Claude Code via tmux, handling long messages safely."""
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
        pane = self._tmux_session.active_window.active_pane
        self._navigate_to_option(option_index)
        time.sleep(0.1)
        pane.send_keys("Space", enter=False)
        time.sleep(0.2)

    def submit_multiselect(self, timeout: int = 600) -> Response:
        """Submit a multi-select question by pressing Tab to reach Submit, then Enter."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
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
            with open(self._jsonl_path, "r") as f:
                self._last_line_count = len(f.readlines())
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
    """

    def __init__(self, max_sessions: int = 5, history_limit: int = 50000):
        self._server = libtmux.Server()
        self._sessions: dict[str, CCSession] = {}
        self._max_sessions = max_sessions
        self._history_limit = history_limit
        self._rediscover()

    def _rediscover(self) -> None:
        """Find existing cchost-* tmux sessions."""
        for tmux_session in self._server.sessions:
            name = tmux_session.name
            if name.startswith("cchost-"):
                session_id = name[len("cchost-") :]
                if session_id not in self._sessions:
                    pane = tmux_session.active_window.active_pane
                    workdir = pane.pane_current_path or "/tmp"
                    self._sessions[session_id] = CCSession(
                        id=session_id,
                        working_dir=workdir,
                        _tmux_session=tmux_session,
                        _host=self,
                    )

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

        tmux_session = self._server.new_session(
            session_name=tmux_name,
            start_directory=working_dir,
            window_command="claude --dangerously-skip-permissions",
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

        return session

    def get(self, session_id: str) -> CCSession:
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        return self._sessions[session_id]

    def list(self) -> list[CCSession]:
        return list(self._sessions.values())

    def destroy(self, session_id: str) -> None:
        session = self.get(session_id)
        session.destroy()
        self._sessions.pop(session_id, None)

    def destroy_all(self) -> None:
        for sid in list(self._sessions.keys()):
            self.destroy(sid)
