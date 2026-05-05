"""
state_classifier — replace tmux screen-scraping with a deterministic state
machine driven by JSONL stop_reason + claude-PID CPU% + recent
.cchost-events.jsonl entries.

States returned by ``classify``:

- ``working``             — claude is producing tokens or running a tool right now
- ``awaiting_permission`` — claude paused on a permission prompt (bypass off)
- ``awaiting_question``   — AskUserQuestion was logged and not answered yet
- ``idle``                — last turn ended with ``end_turn`` and was recent
- ``dormant``             — no live process, or activity is older than the
                            dormant threshold (caller decides whether to resume)

This is a structured replacement for the four ``_unanswered_question_from_*``
+ ``_question_from_tmux_screen`` heuristics in cchost.py. Inputs are signals
that already exist on disk:

1. The Claude Code JSONL transcript at ``session.jsonl_path``. We read backwards
   to find the last ``type=assistant`` entry and inspect ``message.stop_reason``.
2. ``<working_dir>/.cchost-events.jsonl`` — written by ``backend/hooks/progress-hook.sh``
   for every PreToolUse / PostToolUse / Notification / Stop / Subagent* event.
   Filtered by ``_cchost_event`` and ``notification_type``.
3. CPU% on the live claude PID (3-sample rolling average via psutil).

Caveats / known sharp edges:

- ``idle_prompt`` Notification events are noisy: Anthropic ships them after
  every assistant turn, not only on true 60-second idle (claude-code#12048).
  We treat them as a hint that a turn ended — never as a definitive
  "user-needed" signal. ``end_turn`` from the transcript is authoritative.
- ``AskUserQuestion`` does not fire a Notification hook (claude-code#12605
  / #13830 / #15872 / #28273). cchost records it via PreToolUse instead.
  We look for ``_cchost_event=PreToolUse`` + ``tool_name=AskUserQuestion``.
- ``permission_prompt`` is reliable but only fires when permissions are NOT
  bypassed; under ``--dangerously-skip-permissions`` it never fires. The
  ``tool_use`` + low-CPU + age fallback at the bottom of ``classify`` covers
  any other tool that gets stuck waiting.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Literal, Optional

import psutil

logger = logging.getLogger("cchost.state_classifier")

State = Literal["working", "awaiting_permission", "awaiting_question", "idle", "dormant"]

# Tunable thresholds (claudectl's published defaults work well in practice).
CPU_BUSY: float = 5.0  # > this → working
CPU_QUIET: float = 2.0  # < this → quiescent
PERMISSION_AGE_MAX: float = 30  # seconds — permission_prompt counts as "recent"
TURN_END_AGE_MAX: float = 600  # seconds — older than this, idle → dormant
TOOL_USE_STUCK_AGE: float = 5  # seconds — tool_use + quiet that long → blocked

# How far back to read each file. Bounded so we don't drag the whole
# transcript into memory; the relevant signals are always in the tail.
JSONL_TAIL_BYTES: int = 16_384
EVENTS_TAIL_BYTES: int = 32_768


def _cpu_percent(pid: int, samples: int = 3, interval: float = 0.1) -> float:
    """Rolling-average CPU% for ``pid``. Returns 0.0 if the process is gone.

    A single ``cpu_percent`` reading is between two consecutive calls, so the
    first call primes; we take ``samples`` readings after the prime and
    average to dampen transient spikes (claude often bursts and idles within
    a single second during streaming).
    """
    try:
        proc = psutil.Process(pid)
        proc.cpu_percent(None)  # prime
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0
    readings: list[float] = []
    for _ in range(samples):
        time.sleep(interval)
        try:
            readings.append(proc.cpu_percent(None))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0
    return sum(readings) / len(readings) if readings else 0.0


def _read_tail(path: str, max_bytes: int) -> list[str]:
    """Return the trailing ``max_bytes`` of ``path`` split into lines.

    Truncated first line (if we landed mid-line) is dropped so callers always
    get parseable JSON.
    """
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read()
    except OSError:
        return []
    text = chunk.decode("utf-8", errors="ignore")
    lines = text.splitlines()
    # If we seeked past the start, the first line may be partial — discard.
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def _last_assistant_stop(jsonl_path: str) -> tuple[Optional[str], float]:
    """Return ``(stop_reason, age_seconds)`` for the last assistant message.

    ``age_seconds`` falls back to file mtime if the message has no timestamp.
    """
    lines = _read_tail(jsonl_path, JSONL_TAIL_BYTES)
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message") or {}
        stop = msg.get("stop_reason")
        # Prefer the entry's own timestamp if Claude included one.
        ts = rec.get("timestamp") or rec.get("created_at")
        age = _parse_age(ts) if ts else _file_age(jsonl_path)
        return stop, age
    return None, _file_age(jsonl_path)


def _parse_age(ts: str) -> float:
    """Convert an ISO 8601 timestamp into seconds-since-now (>= 0)."""
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except (ValueError, AttributeError):
        return float("inf")


def _file_age(path: str) -> float:
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return float("inf")


def _last_event(events_path: str, predicate) -> tuple[Optional[dict], float]:
    """Find the most recent record in ``.cchost-events.jsonl`` matching ``predicate``.

    ``predicate`` takes the parsed dict and returns True/False.
    Returns ``(record, age_seconds)`` or ``(None, inf)``.
    """
    lines = _read_tail(events_path, EVENTS_TAIL_BYTES)
    for line in reversed(lines):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if predicate(rec):
            ts = rec.get("_cchost_timestamp")
            return rec, _parse_age(ts) if ts else float("inf")
    return None, float("inf")


def _is_permission_event(rec: dict) -> bool:
    if rec.get("_cchost_event") != "Notification":
        return False
    nt = rec.get("notification_type")
    # Treat both as "user has to act" signals; idle_prompt is intentionally excluded
    # because Claude fires it after every turn (claude-code#12048).
    return nt in {"permission_prompt", "elicitation_dialog"}


def _is_askuserquestion_call(rec: dict) -> bool:
    return rec.get("_cchost_event") == "PreToolUse" and rec.get("tool_name") == "AskUserQuestion"


def _is_askuserquestion_answered(rec: dict) -> bool:
    return rec.get("_cchost_event") == "PostToolUse" and rec.get("tool_name") == "AskUserQuestion"


def classify(
    *,
    claude_pid: Optional[int],
    jsonl_path: Optional[str],
    events_path: Optional[str],
) -> State:
    """Classify the session's state from the inputs available on disk.

    Pass ``claude_pid=None`` if no live tmux pane / process exists; the result
    will be ``dormant`` (caller decides whether to lazy-resume).
    """
    if claude_pid is None or not psutil.pid_exists(claude_pid):
        return "dormant"

    cpu = _cpu_percent(claude_pid)
    if cpu > CPU_BUSY:
        return "working"

    stop, stop_age = (None, float("inf"))
    if jsonl_path:
        stop, stop_age = _last_assistant_stop(jsonl_path)

    perm_event, perm_age = (None, float("inf"))
    aq_call, aq_age = (None, float("inf"))
    aq_done, aq_done_age = (None, float("inf"))
    if events_path:
        perm_event, perm_age = _last_event(events_path, _is_permission_event)
        aq_call, aq_age = _last_event(events_path, _is_askuserquestion_call)
        aq_done, aq_done_age = _last_event(events_path, _is_askuserquestion_answered)

    # 1) Recent permission_prompt (or elicitation) trumps everything else.
    if perm_age < PERMISSION_AGE_MAX:
        return "awaiting_permission"

    # 2) AskUserQuestion logged with no matching PostToolUse since.
    if aq_call and (not aq_done or aq_done_age > aq_age):
        return "awaiting_question"

    # 3) Last turn ended cleanly → idle. Age does not matter here: a session
    #    the user opened a day ago is still idle as long as the process is
    #    alive and no tool is mid-call. ``dormant`` is reserved for "no live
    #    process" (handled at the top). A fresh session that has never sent
    #    a message (stop is None) is also idle if CPU is quiet — Claude is
    #    sitting at a fresh prompt, ready for first input.
    if stop == "end_turn" or (stop is None and cpu < CPU_QUIET):
        return "idle"

    # 4) tool_use stop with quiet CPU for long enough → some tool is blocked.
    #    Permission hooks don't fire under ``--dangerously-skip-permissions``;
    #    this is the safety net so the UI still surfaces "something needs you".
    if stop == "tool_use" and cpu < CPU_QUIET and stop_age > TOOL_USE_STUCK_AGE:
        return "awaiting_permission"

    # 5) Default: producing output or about to.
    return "working"
