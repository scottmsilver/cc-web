"""
Tests for ``state_classifier.classify``.

Each test builds a temp dir with a JSONL transcript and a ``.cchost-events.jsonl``
matching the shape ``backend/hooks/progress-hook.sh`` actually writes, then
asserts the state. CPU% and PID liveness are patched out so the tests don't
touch psutil or real processes.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

import state_classifier as sc

# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def patched_psutil(monkeypatch):
    """By default report CPU=0 and PID exists. Tests override the CPU value."""
    state = {"cpu": 0.0, "pid_exists": True}

    def fake_cpu(pid, samples=3, interval=0.1):
        return state["cpu"]

    def fake_pid_exists(pid):
        return state["pid_exists"]

    monkeypatch.setattr(sc, "_cpu_percent", fake_cpu)
    monkeypatch.setattr(sc.psutil, "pid_exists", fake_pid_exists)
    return state


def _now_iso(seconds_ago: float = 0) -> str:
    """ISO 8601 with ms suffix matching progress-hook.sh's format."""
    ts = time.time() - seconds_ago
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int((ts % 1) * 1000):03d}Z"


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def _assistant_entry(stop_reason: str, seconds_ago: float = 0) -> dict:
    return {
        "type": "assistant",
        "timestamp": _now_iso(seconds_ago),
        "message": {"stop_reason": stop_reason, "content": []},
    }


def _notification_event(notification_type: str, seconds_ago: float = 0) -> dict:
    return {
        "_cchost_event": "Notification",
        "hook_event_name": "Notification",
        "notification_type": notification_type,
        "message": "test",
        "_cchost_timestamp": _now_iso(seconds_ago),
    }


def _aq_call_event(seconds_ago: float = 0) -> dict:
    return {
        "_cchost_event": "PreToolUse",
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {"question": "?"},
        "_cchost_timestamp": _now_iso(seconds_ago),
    }


def _aq_done_event(seconds_ago: float = 0) -> dict:
    return {
        "_cchost_event": "PostToolUse",
        "hook_event_name": "PostToolUse",
        "tool_name": "AskUserQuestion",
        "tool_response": {"answer": "yes"},
        "_cchost_timestamp": _now_iso(seconds_ago),
    }


# --------------------------------------------------------------------------- #
# State tests
# --------------------------------------------------------------------------- #


def test_dormant_when_pid_is_none(workdir, patched_psutil):
    assert sc.classify(claude_pid=None, jsonl_path=None, events_path=None) == "dormant"


def test_dormant_when_pid_does_not_exist(workdir, patched_psutil):
    patched_psutil["pid_exists"] = False
    assert sc.classify(claude_pid=99999, jsonl_path=None, events_path=None) == "dormant"


def test_working_when_cpu_above_busy_threshold(workdir, patched_psutil):
    patched_psutil["cpu"] = sc.CPU_BUSY + 1.0
    # Even with end_turn in transcript, high CPU wins (claude is mid-stream).
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("end_turn", seconds_ago=0)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=None) == "working"


def test_idle_after_end_turn(workdir, patched_psutil):
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("end_turn", seconds_ago=10)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=None) == "idle"


def test_idle_after_end_turn_even_when_old(workdir, patched_psutil):
    """Long-idle alive sessions stay idle. ``dormant`` is process-state only."""
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("end_turn", seconds_ago=86_400)])  # 1 day
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=None) == "idle"


def test_idle_when_fresh_session_with_no_jsonl(workdir, patched_psutil):
    """A just-spawned session with no transcript yet is idle (waiting for first message)."""
    assert sc.classify(claude_pid=1, jsonl_path=None, events_path=None) == "idle"


def test_awaiting_permission_on_recent_permission_prompt(workdir, patched_psutil):
    events = workdir / ".cchost-events.jsonl"
    _write_jsonl(events, [_notification_event("permission_prompt", seconds_ago=2)])
    assert sc.classify(claude_pid=1, jsonl_path=None, events_path=str(events)) == "awaiting_permission"


def test_awaiting_permission_on_recent_elicitation_dialog(workdir, patched_psutil):
    events = workdir / ".cchost-events.jsonl"
    _write_jsonl(events, [_notification_event("elicitation_dialog", seconds_ago=2)])
    assert sc.classify(claude_pid=1, jsonl_path=None, events_path=str(events)) == "awaiting_permission"


def test_idle_prompt_is_ignored(workdir, patched_psutil):
    """``idle_prompt`` fires after every turn (claude-code#12048) — it's noise.

    A recent ``idle_prompt`` plus an ``end_turn`` should still be ``idle``,
    NOT ``awaiting_permission``.
    """
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("end_turn", seconds_ago=2)])
    events = workdir / ".cchost-events.jsonl"
    _write_jsonl(events, [_notification_event("idle_prompt", seconds_ago=2)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=str(events)) == "idle"


def test_awaiting_question_when_aq_call_unanswered(workdir, patched_psutil):
    events = workdir / ".cchost-events.jsonl"
    _write_jsonl(events, [_aq_call_event(seconds_ago=5)])
    # JSONL shows tool_use because AskUserQuestion is itself a tool call.
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("tool_use", seconds_ago=5)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=str(events)) == "awaiting_question"


def test_falls_through_when_aq_call_was_answered(workdir, patched_psutil):
    events = workdir / ".cchost-events.jsonl"
    _write_jsonl(
        events,
        [
            _aq_call_event(seconds_ago=10),
            _aq_done_event(seconds_ago=8),
        ],
    )
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("end_turn", seconds_ago=5)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=str(events)) == "idle"


def test_awaiting_permission_when_tool_use_stuck_with_quiet_cpu(workdir, patched_psutil):
    """Bypass-permission mode never fires permission_prompt. Fallback: a tool_use
    stop with quiet CPU for more than TOOL_USE_STUCK_AGE seconds = stuck.
    """
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("tool_use", seconds_ago=sc.TOOL_USE_STUCK_AGE + 1)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=None) == "awaiting_permission"


def test_working_when_tool_use_recent(workdir, patched_psutil):
    """Recent tool_use + quiet CPU = the tool may simply be running. Don't
    cry permission until TOOL_USE_STUCK_AGE has elapsed.
    """
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("tool_use", seconds_ago=1)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=None) == "working"


def test_permission_event_priority_over_question(workdir, patched_psutil):
    """If both a recent permission_prompt and an unanswered AskUserQuestion exist,
    permission wins (more urgent and explicit)."""
    events = workdir / ".cchost-events.jsonl"
    _write_jsonl(
        events,
        [
            _aq_call_event(seconds_ago=10),
            _notification_event("permission_prompt", seconds_ago=2),
        ],
    )
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("tool_use", seconds_ago=10)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=str(events)) == "awaiting_permission"


def test_old_permission_event_does_not_count(workdir, patched_psutil):
    """A permission_prompt from yesterday shouldn't pin us to awaiting_permission."""
    events = workdir / ".cchost-events.jsonl"
    _write_jsonl(events, [_notification_event("permission_prompt", seconds_ago=86_400)])
    jsonl = workdir / "t.jsonl"
    _write_jsonl(jsonl, [_assistant_entry("end_turn", seconds_ago=5)])
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=str(events)) == "idle"


def test_handles_corrupt_jsonl_lines(workdir, patched_psutil):
    """A truncated/corrupt JSONL line in the tail must not crash classify."""
    jsonl = workdir / "t.jsonl"
    with open(jsonl, "w") as f:
        f.write('{"type":"assistant","message":{"stop_reason":"end_turn"}}\n')
        f.write("{not-valid-json\n")
    assert sc.classify(claude_pid=1, jsonl_path=str(jsonl), events_path=None) == "idle"


def test_handles_missing_jsonl_file(workdir, patched_psutil):
    """Pointing at a nonexistent JSONL behaves like jsonl_path=None."""
    assert sc.classify(claude_pid=1, jsonl_path=str(workdir / "nope.jsonl"), events_path=None) == "idle"
