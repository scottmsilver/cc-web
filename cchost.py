"""
cchost — Claude Code as a hosted service.

Layer 1: Transport (tmux session management via libtmux)
Layer 2: Protocol (parse Claude Code output into states)

Usage:
    from cchost import CCHost

    host = CCHost()
    session = host.create("my-audit", working_dir="/data/feb")
    session.send("/invoice:analyzer .")
    session.wait_until_idle(timeout=600)
    print(session.output())
    print(session.status)
    session.destroy()
"""

import enum
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import libtmux
import pyte

# ============================================================
# Layer 2: Protocol — Claude Code terminal state machine
# ============================================================


class SessionState(enum.Enum):
    """What Claude Code is doing right now."""

    STARTING = "starting"  # Session created, claude launching
    IDLE = "idle"  # Waiting for user input (prompt visible)
    THINKING = "thinking"  # Processing (spinner, tool use, etc.)
    WAITING_FOR_INPUT = "waiting"  # AskUserQuestion or similar
    ERROR = "error"  # Claude exited or crashed
    DESTROYED = "destroyed"  # Session killed


# ANSI escape code stripper
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\x1b[^[\]()]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from terminal output."""
    return _ANSI_RE.sub("", text)


def detect_state(raw_output: str) -> SessionState:
    """
    Parse Claude Code terminal output to determine current state.

    This is the core protocol logic. Claude Code's terminal has these patterns:
    - Idle: shows a prompt line (typically ends with > or ❯)
    - Thinking: shows spinner, tool names, progress indicators
    - Waiting: shows AskUserQuestion with lettered options
    - Error: shows error messages or the process has exited
    """
    text = strip_ansi(raw_output)
    lines = text.strip().split("\n")

    if not lines:
        return SessionState.STARTING

    # Work from the bottom up — most recent output is most relevant
    last_lines = lines[-15:]  # Check last 15 lines
    last_text = "\n".join(last_lines)
    last_line = lines[-1].strip()

    # Check for error states
    if any(
        phrase in last_text
        for phrase in [
            "Error: session expired",
            "Error: authentication",
            "claude: command not found",
            "Error: Could not connect",
        ]
    ):
        return SessionState.ERROR

    # Check for Claude Code's trust prompt ("Yes, I trust this folder")
    if "I trust this folder" in last_text or "Enter to confirm" in last_text:
        return SessionState.WAITING_FOR_INPUT

    # Check for AskUserQuestion / waiting for input
    # Claude Code renders these with lettered options like "A)", "B)", "C)"
    option_pattern = re.compile(r"^\s*[A-D]\)")
    option_lines = [l for l in last_lines if option_pattern.match(l.strip())]
    if len(option_lines) >= 2:  # At least 2 options = likely AskUserQuestion
        return SessionState.WAITING_FOR_INPUT

    # Check for idle prompt — Claude Code shows ❯ on its own line between ── borders
    # The prompt area looks like:
    #   ────────────────
    #   ❯
    #   ────────────────
    # So we check ALL lines for ❯, not just the last line
    for line in last_lines:
        stripped = line.strip()
        if stripped == "❯" or stripped == ">":
            return SessionState.IDLE
        # Also check for ❯ with trailing spaces or prompt text
        if stripped.startswith("❯") and len(stripped) < 5:
            return SessionState.IDLE

    # Check for active tool use / thinking indicators
    thinking_indicators = [
        "⠋",
        "⠙",
        "⠹",
        "⠸",
        "⠼",
        "⠴",
        "⠦",
        "⠧",
        "⠇",
        "⠏",  # spinner chars
        "Reading",
        "Writing",
        "Searching",
        "Running",  # tool use
        "Bash(",
        "Read(",
        "Write(",
        "Edit(",
        "Grep(",
        "Glob(",  # tool names
        "Agent(",
        "WebSearch(",
        "█",
        "▓",
        "░",  # progress bars
    ]
    if any(ind in last_text for ind in thinking_indicators):
        return SessionState.THINKING

    # If we can't determine, assume thinking (safer than assuming idle)
    return SessionState.THINKING


# ============================================================
# Layer 1: Transport — tmux session management
# ============================================================


@dataclass
class CCSession:
    """A persistent Claude Code session running in tmux."""

    id: str
    working_dir: str
    log_path: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    _tmux_session: Optional[libtmux.Session] = field(default=None, repr=False)
    _host: Optional["CCHost"] = field(default=None, repr=False)
    _last_output: str = field(default="", repr=False)
    _last_output_time: Optional[float] = field(default=None, repr=False)
    _log_offset: int = field(default=0, repr=False)  # How much of the log we've fed to pyte
    _screen: Optional[pyte.Screen] = field(default=None, repr=False)
    _stream: Optional[pyte.Stream] = field(default=None, repr=False)
    _rendered_history: list = field(default_factory=list, repr=False)  # snapshots of rendered screens
    _turn_snapshot_start: int = field(default=0, repr=False)  # index into _rendered_history where current turn began

    @property
    def status(self) -> SessionState:
        """Current session state, detected from terminal output."""
        if self._tmux_session is None:
            return SessionState.DESTROYED
        try:
            raw = self.capture(lines=30)
            return detect_state(raw)
        except Exception:
            return SessionState.ERROR

    def send(self, message: str) -> None:
        """Send a message to Claude Code. Like typing and hitting Enter."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        # Mark the start of a new turn for response extraction
        self._turn_snapshot_start = len(self._rendered_history)
        pane = self._tmux_session.active_window.active_pane
        pane.send_keys(message, enter=True)

    def send_keys(self, keys: str) -> None:
        """Send raw keys (for Ctrl+C, Enter, etc.)."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        pane = self._tmux_session.active_window.active_pane
        pane.send_keys(keys, enter=False)

    def capture(self, lines: int = 100) -> str:
        """Capture the last N lines of terminal output (raw, with ANSI)."""
        if self._tmux_session is None:
            raise RuntimeError(f"Session {self.id} is destroyed")
        pane = self._tmux_session.active_window.active_pane
        captured = pane.capture_pane(start=-lines)
        raw = "\n".join(captured)
        self._last_output = raw
        self._last_output_time = time.time()
        return raw

    def output(self, lines: int = 100) -> str:
        """Capture terminal output, stripped of ANSI codes."""
        return strip_ansi(self.capture(lines))

    def wait_until_ready(self, timeout: int = 30, poll_interval: float = 1.0) -> SessionState:
        """
        Wait for Claude Code to finish launching and be ready for input.
        Automatically accepts the trust prompt if it appears.
        """
        start = time.time()
        trust_accepted = False

        while time.time() - start < timeout:
            raw = self.capture(lines=30)
            state = detect_state(raw)

            # Auto-accept the trust prompt
            if state == SessionState.WAITING_FOR_INPUT and not trust_accepted:
                text = strip_ansi(raw)
                if "I trust this folder" in text or "Enter to confirm" in text:
                    self.send_keys("Enter")
                    trust_accepted = True
                    time.sleep(2)
                    continue

            if state == SessionState.IDLE:
                return state
            if state == SessionState.ERROR:
                return state

            time.sleep(poll_interval)

        raise TimeoutError(f"Session {self.id} did not become ready within {timeout}s")

    def wait_until_idle(self, timeout: int = 300, poll_interval: float = 1.0) -> SessionState:
        """
        Block until Claude Code is idle or waiting for input.
        Returns the final state. Raises TimeoutError if timeout exceeded.
        """
        start = time.time()
        last_change_time = start
        last_output = ""

        while time.time() - start < timeout:
            raw = self.capture(lines=30)
            state = detect_state(raw)

            # Take a pyte snapshot on each poll so we don't lose transient responses
            self.snapshot()

            if state in (SessionState.IDLE, SessionState.WAITING_FOR_INPUT, SessionState.ERROR):
                return state

            # Track output changes for stall detection
            clean = strip_ansi(raw)
            if clean != last_output:
                last_output = clean
                last_change_time = time.time()

            time.sleep(poll_interval)

        raise TimeoutError(
            f"Session {self.id} did not reach idle state within {timeout}s. " f"Last state: {state.value}"
        )

    def wait_for_output_change(self, timeout: int = 60, poll_interval: float = 0.5) -> str:
        """Wait for the output to change from its current state. Returns new output."""
        current = strip_ansi(self.capture(lines=50))
        start = time.time()
        while time.time() - start < timeout:
            new = strip_ansi(self.capture(lines=50))
            if new != current:
                return new
            time.sleep(poll_interval)
        raise TimeoutError(f"No output change within {timeout}s")

    def _ensure_screen(self) -> None:
        """Initialize pyte screen if needed."""
        if self._screen is None:
            self._screen = pyte.Screen(180, 50)
            self._stream = pyte.Stream(self._screen)
            self._log_offset = 0

    def render(self) -> list[str]:
        """
        Render the current terminal state by feeding pipe-pane output through pyte.
        Returns non-empty lines of the rendered screen.
        """
        self._ensure_screen()
        if not self.log_path or not os.path.exists(self.log_path):
            return []

        # Read new bytes since last render
        with open(self.log_path, "rb") as f:
            f.seek(self._log_offset)
            new_data = f.read()
            self._log_offset = f.tell()

        if new_data:
            self._stream.feed(new_data.decode("utf-8", errors="replace"))

        # Return non-empty lines
        return [line.rstrip() for line in self._screen.display if line.strip()]

    def render_full(self) -> str:
        """Render terminal state as a single string."""
        return "\n".join(self.render())

    def snapshot(self) -> None:
        """Take a snapshot of the current rendered screen for history tracking."""
        rendered = self.render_full()
        if rendered and (not self._rendered_history or rendered != self._rendered_history[-1]):
            self._rendered_history.append(rendered)

    def log(self, tail: int = 0) -> str:
        """
        Read the full terminal output log (pipe-pane capture).
        Unlike capture(), this includes content that was overwritten by TUI redraws.
        If tail > 0, return only the last N lines.
        """
        if not self.log_path or not os.path.exists(self.log_path):
            return ""
        with open(self.log_path, "r", errors="replace") as f:
            content = f.read()
        clean = strip_ansi(content)
        if tail > 0:
            lines = clean.split("\n")
            return "\n".join(lines[-tail:])
        return clean

    def response(self) -> str:
        """
        Extract Claude's latest response text.

        Strategy: scan ALL snapshots (captured during wait_until_idle polling)
        plus the current screen. The response text appears transiently between
        the user's prompt and the idle prompt redraw, so we need the snapshot
        history to catch it.
        """
        # Only scan snapshots from the current turn (since last send())
        screens = list(self._rendered_history[self._turn_snapshot_start :])
        current = self.render_full()
        if current:
            screens.append(current)

        if not screens:
            return ""

        # Parse each screen for conversation turns, collect all responses
        all_responses: list[str] = []

        for screen_text in screens:
            lines = screen_text.split("\n")
            current_response: list[str] = []
            in_response = False

            for line in lines:
                stripped = line.strip()

                # User prompt line (contains ❯ with text after it)
                if "❯" in stripped:
                    after = stripped.split("❯", 1)[1].strip()
                    if after and len(after) > 2:
                        if current_response and in_response:
                            resp = "\n".join(current_response).strip()
                            if resp:
                                all_responses.append(resp)
                        current_response = []
                        in_response = True
                        continue
                    elif stripped.strip() == "❯":
                        if current_response and in_response:
                            resp = "\n".join(current_response).strip()
                            if resp:
                                all_responses.append(resp)
                        current_response = []
                        in_response = False
                        continue

                # Skip UI chrome
                if any(stripped.startswith(c) for c in ["─", "│", "╭", "╰", "⏵"]):
                    continue

                # Skip thinking/spinner lines
                _THINKING_WORDS = [
                    "Cerebrating",
                    "Noodling",
                    "Transmuting",
                    "Pondering",
                    "Cogitating",
                    "Ruminating",
                    "Whirring",
                    "Scampering",
                    "Musing",
                    "Dreaming",
                    "Germinating",
                    "Finagling",
                    "Brainstorming",
                    "Percolating",
                    "Considering",
                    "Unfurling",
                    "Concocting",
                    "Composing",
                    "Crafting",
                    "Assembling",
                    "Formulating",
                    "Synthesizing",
                    "Weaving",
                    "Brewing",
                    "Conjuring",
                    "Manifesting",
                    "Scheming",
                    "Plotting",
                    "Devising",
                    "Hatching",
                    "Imagining",
                    "Calculating",
                    "Computing",
                    "Processing",
                    "Analyzing",
                    "Thinking",
                    "Working",
                    "Generating",
                    "Building",
                    "Creating",
                    "Designing",
                    "Planning",
                    "Preparing",
                ]
                if any(w in stripped for w in _THINKING_WORDS):
                    continue
                # Skip lines that are just spinner chars with a word
                if stripped and stripped[0] in "✻✶✢·*" and len(stripped) < 30:
                    skip = False
                    for w in _THINKING_WORDS:
                        if w in stripped:
                            skip = True
                            break
                    if skip:
                        continue

                # Skip tip lines
                if stripped.startswith("⎿"):
                    continue

                if in_response and stripped:
                    # Strip leading bullet markers Claude uses for responses
                    clean = stripped
                    if clean.startswith("● "):
                        clean = clean[2:]
                    current_response.append(clean)

            if current_response and in_response:
                resp = "\n".join(current_response).strip()
                if resp:
                    all_responses.append(resp)

        # Return the last unique non-empty response
        # Deduplicate since the same response may appear in multiple snapshots
        seen = set()
        unique = []
        for r in all_responses:
            if r not in seen:
                seen.add(r)
                unique.append(r)

        return unique[-1] if unique else ""

    def interrupt(self) -> None:
        """Send Ctrl+C to Claude Code."""
        self.send_keys("C-c")

    def files(self) -> list[str]:
        """List files in the working directory."""
        result = []
        for root, dirs, filenames in os.walk(self.working_dir):
            for f in filenames:
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
        # Remove from host's session dict
        if self._host and self.id in self._host._sessions:
            del self._host._sessions[self.id]


class CCHost:
    """
    Manages Claude Code sessions via tmux.

    Usage:
        host = CCHost()
        session = host.create("my-session", working_dir="/data/project")
        session.send("hello")
        state = session.wait_until_idle()
        print(session.output())
    """

    def __init__(self, max_sessions: int = 5, history_limit: int = 10000):
        self._server = libtmux.Server()
        self._sessions: dict[str, CCSession] = {}
        self._max_sessions = max_sessions
        self._history_limit = history_limit
        # Rediscover any existing cchost sessions from tmux
        self._rediscover()

    def _rediscover(self) -> None:
        """Find existing cchost-* tmux sessions from a prior server run."""
        for tmux_session in self._server.sessions:
            name = tmux_session.name
            if name.startswith("cchost-"):
                session_id = name[len("cchost-") :]
                if session_id not in self._sessions:
                    # Reconstruct the working dir from the pane's current path
                    pane = tmux_session.active_window.active_pane
                    workdir = pane.current_path or "/tmp"
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
        claude_args: str = "--dangerously-skip-permissions",
    ) -> CCSession:
        """
        Create a new Claude Code session in tmux.

        Args:
            session_id: Unique identifier (alphanumeric + hyphens)
            working_dir: Working directory for Claude Code
            claude_args: Additional args for the claude command
        """
        if session_id in self._sessions:
            raise ValueError(f"Session {session_id} already exists")
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(f"Max sessions ({self._max_sessions}) reached. " f"Destroy a session first.")

        # Validate session_id
        if not re.match(r"^[a-zA-Z0-9_-]+$", session_id):
            raise ValueError(f"Invalid session ID: {session_id}. Use alphanumeric, hyphens, underscores.")

        # Ensure working dir exists
        os.makedirs(working_dir, exist_ok=True)

        # Create tmux session with Claude Code
        tmux_name = f"cchost-{session_id}"
        tmux_session = self._server.new_session(
            session_name=tmux_name,
            start_directory=working_dir,
            window_command=f"claude {claude_args}",
        )

        # Set scrollback buffer
        tmux_session.set_option("history-limit", self._history_limit)

        # Pipe all terminal output to a log file
        # This captures everything, including content overwritten by TUI redraws
        log_path = os.path.join(working_dir, ".cchost-output.log")
        pane = tmux_session.active_window.active_pane
        pane.cmd("pipe-pane", "-o", f"cat >> {log_path}")

        session = CCSession(
            id=session_id,
            working_dir=working_dir,
            log_path=log_path,
            _tmux_session=tmux_session,
            _host=self,
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> CCSession:
        """Get a session by ID."""
        if session_id not in self._sessions:
            raise KeyError(f"Session {session_id} not found")
        return self._sessions[session_id]

    def list(self) -> list[CCSession]:
        """List all active sessions."""
        return list(self._sessions.values())

    def destroy(self, session_id: str) -> None:
        """Destroy a session."""
        session = self.get(session_id)
        session.destroy()
        del self._sessions[session_id]

    def destroy_all(self) -> None:
        """Destroy all sessions."""
        for session_id in list(self._sessions.keys()):
            self.destroy(session_id)
