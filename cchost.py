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
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import libtmux
from progress import derive_progress_snapshot, normalize_jsonl_entries


@dataclass
class QuestionOption:
    """An option in an AskUserQuestion prompt."""

    label: str
    description: str = ""
    index: int = 0  # 1-based index as shown on screen


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

    def _find_jsonl(self) -> Optional[str]:
        """Find the JSONL conversation log for this session."""
        # Claude stores at ~/.claude/projects/{slug}/{session_id}.jsonl
        slug = self.working_dir.replace("/", "-").lstrip("-")
        project_dir = os.path.expanduser(f"~/.claude/projects/-{slug}")
        if os.path.isdir(project_dir):
            files = glob.glob(os.path.join(project_dir, "*.jsonl"))
            if files:
                # Return the most recently modified one
                return max(files, key=os.path.getmtime)
        return None

    def _read_new_lines(self) -> list[dict]:
        """Read new lines from the JSONL since last check."""
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            self._jsonl_path = self._find_jsonl()
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return []

        with open(self._jsonl_path, "r") as f:
            lines = f.readlines()

        new_lines = lines[self._last_line_count :]
        self._last_line_count = len(lines)

        parsed = []
        for line in new_lines:
            line = line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return parsed

    def _read_all_transcript_entries(self) -> list[dict[str, Any]]:
        """Read the full JSONL transcript without advancing incremental state."""
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            self._jsonl_path = self._find_jsonl()
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return []

        entries: list[dict[str, Any]] = []
        with open(self._jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
        return entries

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
        slug = self.working_dir.replace("/", "-").lstrip("-")
        project_dir = os.path.expanduser(f"~/.claude/projects/-{slug}")
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
                pass
            time.sleep(1)
        raise TimeoutError(f"Claude Code didn't start within {timeout}s")

    def _parse_question_screen(self) -> Optional[dict]:
        """
        Parse the tmux screen for an AskUserQuestion prompt.
        Returns {question: str, options: [QuestionOption]} or None.
        """
        try:
            pane = self._tmux_session.active_window.active_pane
            captured = pane.capture_pane(start=-25)
        except Exception:
            return None

        lines = [line.rstrip() for line in captured]
        screen = "\n".join(lines)

        # AskUserQuestion shows "Enter to select" at the bottom
        if "Enter to select" not in screen:
            return None

        # Extract the question text — appears above the numbered options
        question_text = ""
        options = []
        option_pattern = re.compile(r"^\s*(?:❯\s*)?(\d+)\.\s+(.+)$")

        in_options = False
        for line in lines:
            stripped = line.strip()

            # Detect option lines (1. Label, 2. Label, etc.)
            match = option_pattern.match(stripped)
            if match:
                in_options = True
                idx = int(match.group(1))
                label = match.group(2).strip()
                # Skip meta-options
                if label.lower() in ("type something.", "chat about this"):
                    continue
                options.append(QuestionOption(label=label, index=idx))
            elif not in_options and stripped and not stripped.startswith(("─", "│", "╭", "╰", "←", "☐", "✔", "Enter")):
                # Lines before options that aren't UI chrome = question text
                if stripped and "❯" not in stripped:
                    question_text = stripped

        if options:
            return {
                "question": question_text,
                "options": options,
            }
        return None

    def current_question(self) -> Optional[dict]:
        """Return the active AskUserQuestion prompt, if one is visible."""
        return self._parse_question_screen()

    def question_status(self) -> bool:
        """Return whether Claude is currently showing an AskUserQuestion prompt."""
        return self.current_question() is not None

    def prompt_status(self) -> bool:
        """Return whether the tmux pane shows Claude Code's idle prompt."""
        return self._is_tmux_idle()

    def _is_asking_question(self) -> bool:
        """Check if Claude is showing an AskUserQuestion prompt."""
        return self.question_status()

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

    def progress_entries(self) -> list:
        """Return normalized progress events from the full transcript."""
        return normalize_jsonl_entries(self._read_all_transcript_entries())

    def progress_snapshot(self):
        """Return a derived progress snapshot for the current session."""
        return derive_progress_snapshot(
            self.progress_entries(),
            is_question=self.question_status(),
            is_prompt=self.prompt_status(),
        )

    def send(self, message: str, timeout: int = 600) -> Response:
        """
        Send a message and wait for Claude's response.

        Done detection strategy (from empirical testing):
        1. Watch JSONL for new assistant messages (response content)
        2. After seeing an assistant message, check tmux for ❯ idle prompt
        3. Once both conditions met (have response + tmux idle), return
        4. Also accept last-prompt JSONL entry as a done signal (arrives late but definitive)
        """
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")

        # Always re-find the JSONL (session ID may have changed, or new file created)
        self._jsonl_path = self._find_jsonl()
        if self._jsonl_path:
            with open(self._jsonl_path, "r") as f:
                self._last_line_count = len(f.readlines())
        else:
            self._last_line_count = 0

        # Type the message into Claude Code
        pane = self._tmux_session.active_window.active_pane
        pane.send_keys(message, enter=True)

        # Watch for response + idle signal.
        # Key insight: Claude's multi-step analysis produces intermediate
        # "idle" states between tool calls. We must NOT return during these.
        # Only return when:
        # - We have assistant text
        # - The last assistant entry has NO tool_use blocks (not mid-chain)
        # - tmux shows idle (❯)
        # - No new JSONL content for 2 seconds
        start = time.time()
        last_assistant_text = ""
        last_assistant_raw = {}
        last_new_content_time = time.time()
        has_pending_tool_use = False
        saw_any_tool_use = False  # True if this turn involved any tools

        while time.time() - start < timeout:
            new_lines = self._read_new_lines()

            for entry in new_lines:
                entry_type = entry.get("type", "")
                last_new_content_time = time.time()

                if entry_type == "assistant":
                    text = self._extract_text(entry)
                    content = entry.get("message", {}).get("content", [])
                    entry_has_tools = False

                    if isinstance(content, list):
                        entry_has_tools = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)

                    if entry_has_tools:
                        # This entry dispatched tools — Claude will continue
                        has_pending_tool_use = True
                        saw_any_tool_use = True
                    else:
                        has_pending_tool_use = False

                    # Only update the "final" response text when the entry
                    # has text and does NOT also dispatch tools. Entries with
                    # both text and tool_use are intermediate status messages
                    # ("Let me read the remaining pages" + Read tool).
                    if text and not entry_has_tools:
                        last_assistant_text = text
                        last_assistant_raw = entry

                elif entry_type == "user":
                    # A user entry with tool_result means a tool completed.
                    # Claude will produce another assistant turn — keep waiting.
                    content = entry.get("message", {}).get("content", [])
                    if isinstance(content, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                    ):
                        # Tool resolved, but Claude hasn't spoken yet
                        has_pending_tool_use = False

                elif entry_type == "last-prompt":
                    return Response(
                        text=last_assistant_text,
                        role="assistant",
                        raw=last_assistant_raw,
                    )

            # Check for AskUserQuestion
            question = self._parse_question_screen()
            if question:
                return Response(
                    text=question["question"],
                    role="assistant",
                    is_question=True,
                    questions=[question],
                )

            # Done detection: require STABLE idle.
            # Check tmux idle, then wait, then check again.
            # If Claude is between tool calls, the idle will disappear
            # when the next tool starts. If Claude is truly done, idle
            # persists across both checks.
            if last_assistant_text and not has_pending_tool_use:
                time_since_content = time.time() - last_new_content_time
                settle_time = 5.0 if saw_any_tool_use else 2.0
                if time_since_content >= settle_time and self._is_tmux_idle():
                    # First idle check passed. Wait 3 more seconds and
                    # verify no new JSONL lines appeared and tmux is still idle.
                    time.sleep(3.0)
                    recheck_lines = self._read_new_lines()
                    new_content_during_settle = False
                    for entry in recheck_lines:
                        et = entry.get("type", "")
                        last_new_content_time = time.time()
                        new_content_during_settle = True
                        if et == "assistant":
                            text = self._extract_text(entry)
                            content = entry.get("message", {}).get("content", [])
                            entry_has_tools = isinstance(content, list) and any(
                                isinstance(b, dict) and b.get("type") == "tool_use" for b in content
                            )
                            if entry_has_tools:
                                has_pending_tool_use = True
                            elif text:
                                last_assistant_text = text
                                last_assistant_raw = entry
                        elif et == "user":
                            content = entry.get("message", {}).get("content", [])
                            if isinstance(content, list) and any(
                                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                            ):
                                has_pending_tool_use = False

                    if not new_content_during_settle and self._is_tmux_idle():
                        # Truly done — no new content during settle and still idle
                        return Response(
                            text=last_assistant_text,
                            role="assistant",
                            raw=last_assistant_raw,
                        )
                    # Otherwise, new content arrived or idle disappeared — keep looping

            time.sleep(0.5)

        # Timeout — return whatever we have
        return Response(
            text=last_assistant_text or "(timeout — no response captured)",
            role="assistant",
            raw=last_assistant_raw,
        )

    def answer(self, option_index: int = 1) -> Response:
        """
        Answer an AskUserQuestion by selecting an option.

        Args:
            option_index: 1-based index of the option to select.
                         Default 1 selects the first (usually recommended) option.

        The AskUserQuestion picker uses arrow keys to navigate and Enter to select.
        Option 1 is pre-selected by default, so Enter alone picks option 1.
        For other options, we press Down arrow (option_index - 1) times, then Enter.

        After answering, if there are more questions in the same AskUserQuestion
        batch (tabs at the top like ☐ Markup ☐ Retention), this returns the
        next question. If all questions are answered, it submits and returns
        the final response.
        """
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")

        pane = self._tmux_session.active_window.active_pane

        # Navigate to the desired option
        if option_index > 1:
            for _ in range(option_index - 1):
                pane.send_keys("Down", enter=False)
                time.sleep(0.2)

        # Select it
        pane.send_keys("", enter=True)
        time.sleep(1)

        # Check if there's another question, a submit screen, or we're back to idle
        # Poll for up to 5 seconds
        for _ in range(10):
            # Check for another question
            question = self._parse_question_screen()
            if question:
                return Response(
                    text=question["question"],
                    role="assistant",
                    is_question=True,
                    questions=[question],
                )

            # Check for submit prompt
            captured = pane.capture_pane(start=-10)
            screen = "\n".join(captured)
            if "Submit" in screen and "Ready to submit" in screen:
                # Auto-submit
                pane.send_keys("", enter=True)
                time.sleep(2)
                # Now wait for the actual response
                return self._wait_after_answer()

            # Check if idle (question was the only one, no submit needed)
            if self._is_tmux_idle():
                return self._wait_after_answer()

            time.sleep(0.5)

        # Fell through — try waiting for response
        return self._wait_after_answer()

    def _wait_after_answer(self, timeout: int = 60) -> Response:
        """Wait for Claude's response after answering a question."""
        start = time.time()
        while time.time() - start < timeout:
            new_lines = self._read_new_lines()
            for entry in new_lines:
                if entry.get("type") == "assistant":
                    text = self._extract_text(entry)
                    if text:
                        # Check if idle
                        time.sleep(1)
                        if self._is_tmux_idle():
                            return Response(text=text, role="assistant", raw=entry)

            # Check for another question
            question = self._parse_question_screen()
            if question:
                return Response(
                    text=question["question"],
                    role="assistant",
                    is_question=True,
                    questions=[question],
                )

            time.sleep(0.5)

        return Response(text="(timeout waiting for response after answer)", role="assistant")

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
        """Return the full conversation history from the JSONL."""
        if not self._jsonl_path:
            self._jsonl_path = self._find_jsonl()
        if not self._jsonl_path or not os.path.exists(self._jsonl_path):
            return []
        entries = []
        with open(self._jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    t = entry.get("type", "")
                    if t in ("user", "assistant"):
                        text = self._extract_text(entry)
                        if text:
                            entries.append({"role": t, "text": text})
                except json.JSONDecodeError:
                    pass
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
        """Kill the tmux session and clean up."""
        if self._tmux_session is not None:
            try:
                self._tmux_session.kill()
            except Exception:
                pass
            self._tmux_session = None
        if self._host and self.id in self._host._sessions:
            del self._host._sessions[self.id]


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
        self.get(session_id).destroy()

    def destroy_all(self) -> None:
        for sid in list(self._sessions.keys()):
            self.destroy(sid)
