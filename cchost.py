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
from typing import Optional

import libtmux


@dataclass
class Response:
    """A response from Claude Code."""

    text: str
    role: str = "assistant"
    raw: dict = field(default_factory=dict)


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
        if not self._jsonl_path:
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

        # Record where we are in the JSONL
        if not self._jsonl_path:
            self._jsonl_path = self._find_jsonl()
        if self._jsonl_path:
            with open(self._jsonl_path, "r") as f:
                self._last_line_count = len(f.readlines())

        # Type the message into Claude Code
        pane = self._tmux_session.active_window.active_pane
        pane.send_keys(message, enter=True)

        # Watch for response + idle signal
        start = time.time()
        last_assistant_text = ""
        last_assistant_raw = {}
        last_new_content_time = time.time()

        while time.time() - start < timeout:
            new_lines = self._read_new_lines()

            for entry in new_lines:
                entry_type = entry.get("type", "")
                last_new_content_time = time.time()

                if entry_type == "assistant":
                    text = self._extract_text(entry)
                    if text:
                        last_assistant_text = text
                        last_assistant_raw = entry

                elif entry_type == "last-prompt":
                    # Definitive done signal (rare but authoritative)
                    return Response(
                        text=last_assistant_text,
                        role="assistant",
                        raw=last_assistant_raw,
                    )

            # If we have a response AND tmux shows idle AND no new JSONL
            # content for 1 second, we're done
            if last_assistant_text:
                time_since_content = time.time() - last_new_content_time
                if time_since_content >= 1.0 and self._is_tmux_idle():
                    return Response(
                        text=last_assistant_text,
                        role="assistant",
                        raw=last_assistant_raw,
                    )

            time.sleep(0.5)

        # Timeout — return whatever we have
        return Response(
            text=last_assistant_text or "(timeout — no response captured)",
            role="assistant",
            raw=last_assistant_raw,
        )

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
