"""
cchost — Claude Code as a hosted service.

Runs Claude Code as a subprocess with --output-format stream-json,
giving us clean structured JSON for every message, tool use, and response.
Sessions persist via --resume with session IDs.

Usage:
    from cchost import CCHost

    host = CCHost()
    session = host.create("my-audit", working_dir="/data/feb")
    result = session.send("What is 2+2?")
    print(result.text)  # "4"

    # Persistent conversation
    result2 = session.send("Add 6 to that.")
    print(result2.text)  # "10"

    # Long-running tasks
    result3 = session.send("/invoice:analyzer .", timeout=900)
    print(result3.text)
    print(session.files())

    session.destroy()
"""

import enum
import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class SessionState(enum.Enum):
    IDLE = "idle"
    RUNNING = "running"
    ERROR = "error"
    DESTROYED = "destroyed"


@dataclass
class StreamEvent:
    """A single event from Claude Code's stream-json output."""

    type: str  # system, assistant, result, tool_use, tool_result, etc.
    subtype: str = ""
    data: dict = field(default_factory=dict)
    raw: str = ""

    @classmethod
    def from_line(cls, line: str) -> "StreamEvent":
        try:
            d = json.loads(line)
            return cls(
                type=d.get("type", "unknown"),
                subtype=d.get("subtype", ""),
                data=d,
                raw=line,
            )
        except json.JSONDecodeError:
            return cls(type="parse_error", raw=line)


@dataclass
class SendResult:
    """Result of sending a message to Claude Code."""

    text: str  # The assistant's response text
    session_id: str = ""  # Session ID for resumption
    duration_ms: int = 0  # How long it took
    cost_usd: float = 0.0  # Cost of this turn
    num_turns: int = 0  # Number of agent turns
    stop_reason: str = ""  # end_turn, tool_use, etc.
    events: list[StreamEvent] = field(default_factory=list)  # All stream events
    is_error: bool = False
    error_message: str = ""

    @property
    def tool_uses(self) -> list[dict]:
        """Extract tool use events from the stream."""
        return [e.data for e in self.events if e.type == "tool_use"]


@dataclass
class CCSession:
    """A persistent Claude Code session."""

    id: str
    working_dir: str
    session_id: Optional[str] = None  # Claude Code's internal session ID (for --resume)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    state: SessionState = SessionState.IDLE
    history: list[SendResult] = field(default_factory=list)
    _host: Optional["CCHost"] = field(default=None, repr=False)

    def send(
        self,
        message: str,
        timeout: int = 300,
        allowed_tools: Optional[str] = None,
    ) -> SendResult:
        """
        Send a message to Claude Code and wait for the response.

        Args:
            message: The prompt to send
            timeout: Max seconds to wait
            allowed_tools: Comma-separated tool names (default: all tools)
        """
        if self.state == SessionState.DESTROYED:
            raise RuntimeError(f"Session {self.id} is destroyed")

        self.state = SessionState.RUNNING

        # Build the claude command
        cmd = [
            "claude",
            "-p",
            message,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]

        if self.session_id:
            cmd.extend(["--resume", self.session_id])

        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])

        # Run claude as a subprocess
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self.working_dir,
            )
        except subprocess.TimeoutExpired:
            self.state = SessionState.ERROR
            return SendResult(
                text="",
                is_error=True,
                error_message=f"Timeout after {timeout}s",
            )

        # Parse the stream-json output
        events = []
        result_text = ""
        result_session_id = ""
        result_duration = 0
        result_cost = 0.0
        result_turns = 0
        result_stop = ""
        is_error = False
        error_msg = ""

        for line in proc.stdout.strip().split("\n"):
            if not line.strip():
                continue
            event = StreamEvent.from_line(line)
            events.append(event)

            if event.type == "result":
                result_text = event.data.get("result", "")
                result_session_id = event.data.get("session_id", "")
                result_duration = event.data.get("duration_ms", 0)
                result_cost = event.data.get("total_cost_usd", 0.0)
                result_turns = event.data.get("num_turns", 0)
                result_stop = event.data.get("stop_reason", "")
                is_error = event.data.get("is_error", False)
                if is_error:
                    error_msg = result_text

            elif event.type == "assistant":
                # Capture assistant message text (may come in chunks)
                content = event.data.get("message", {}).get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            # This is a partial or full text block
                            pass  # result event has the final text

        # Update session state
        if result_session_id:
            self.session_id = result_session_id
        self.state = SessionState.ERROR if is_error else SessionState.IDLE

        result = SendResult(
            text=result_text,
            session_id=result_session_id,
            duration_ms=result_duration,
            cost_usd=result_cost,
            num_turns=result_turns,
            stop_reason=result_stop,
            events=events,
            is_error=is_error,
            error_message=error_msg,
        )
        self.history.append(result)
        return result

    def files(self) -> list[str]:
        """List files in the working directory."""
        result = []
        for root, dirs, filenames in os.walk(self.working_dir):
            # Skip hidden dirs
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
        """Mark session as destroyed."""
        self.state = SessionState.DESTROYED
        if self._host and self.id in self._host._sessions:
            del self._host._sessions[self.id]


class CCHost:
    """
    Manages Claude Code sessions.

    Each session runs claude -p with --resume for conversation persistence.
    No tmux, no TUI — just structured JSON over subprocess.
    """

    def __init__(self, max_sessions: int = 10):
        self._sessions: dict[str, CCSession] = {}
        self._max_sessions = max_sessions

    def create(
        self,
        session_id: str,
        working_dir: str = "/tmp",
    ) -> CCSession:
        if session_id in self._sessions:
            raise ValueError(f"Session {session_id} already exists")
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(f"Max sessions ({self._max_sessions}) reached")
        if not re.match(r"^[a-zA-Z0-9_-]+$", session_id):
            raise ValueError(f"Invalid session ID: {session_id}")

        os.makedirs(working_dir, exist_ok=True)

        session = CCSession(
            id=session_id,
            working_dir=working_dir,
            _host=self,
        )
        self._sessions[session_id] = session
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

    def destroy_all(self) -> None:
        for session_id in list(self._sessions.keys()):
            self.destroy(session_id)
