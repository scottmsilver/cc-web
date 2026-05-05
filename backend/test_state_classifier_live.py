"""
Live integration tests for ``state_classifier``.

These spawn a real ``claude`` process inside a tmux pane and drive a real
conversation. They go through Claude Code, so they use whatever auth
the local ``claude`` binary uses (Pro/Max OAuth in this setup) — no
extra Anthropic API spend on top of normal usage.

Total runtime ~60s. Skipped automatically if ``claude`` or ``tmux`` is
not on PATH.

Covered (real, not synthesized):

- ``idle`` immediately after session creation.
- ``working`` while Claude is generating a response.
- ``idle`` again after the response completes.
- ``awaiting_question`` after Claude calls AskUserQuestion, plus the
  transition out of that state once we answer.
- ``dormant`` after the tmux pane is killed externally.

NOT covered live (unit-tested only in test_state_classifier.py):

- ``awaiting_permission`` via ``permission_prompt`` Notification —
  requires non-bypass permission mode, which has no automated answer
  channel in tmux.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from cchost import CCHost, CCSession

_HAS_CLAUDE = shutil.which("claude") is not None
_HAS_TMUX = shutil.which("tmux") is not None

pytestmark = pytest.mark.skipif(
    not (_HAS_CLAUDE and _HAS_TMUX),
    reason="requires `claude` and `tmux` on PATH",
)


def _wait_for(predicate, timeout: float = 30.0, poll: float = 0.2) -> bool:
    """Poll ``predicate`` until True or timeout. Returns True if matched."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return False


@pytest.fixture(scope="module")
def host(tmp_path_factory) -> Iterator[CCHost]:
    """One CCHost shared across the module so we only pay session setup once."""
    if not (_HAS_CLAUDE and _HAS_TMUX):
        pytest.skip("requires `claude` and `tmux` on PATH")
    manifest = tmp_path_factory.mktemp("manifest") / "sessions.json"
    h = CCHost(manifest_path=str(manifest))
    yield h
    # Tear down any sessions this test created.
    for sid in list(h._sessions.keys()):
        try:
            h.destroy(sid)
        except Exception:
            pass


@pytest.fixture
def session(host: CCHost, tmp_path: Path) -> Iterator[CCSession]:
    sid = f"live-{uuid.uuid4().hex[:8]}"
    workdir = str(tmp_path / sid)
    s = host.create(sid, working_dir=workdir, owner_email="test@example.com")
    yield s
    try:
        host.destroy(sid)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


def test_freshly_created_session_is_idle(session: CCSession):
    """A just-spawned session sitting at Claude's prompt is idle."""
    # Brief settle time for Claude's startup banner to finish drawing.
    assert _wait_for(lambda: session.state == "idle", timeout=10.0)


def test_state_flips_to_working_during_response_and_back_to_idle(session: CCSession):
    """Send a prompt that takes a few seconds. Classifier should observe `working`
    at least once before settling back to `idle`.
    """
    # Wait for the session to be idle before sending.
    assert _wait_for(lambda: session.state == "idle", timeout=10.0)

    # A prompt that produces a lot of tokens and takes a few seconds.
    session.send(
        "Write 10 short haiku about file systems, one per response paragraph. " "No tools, just plain text.",
        timeout=120,
    )

    # ``send`` returns when the response finishes, so by here we should
    # eventually settle to idle. Confirm.
    assert _wait_for(lambda: session.state == "idle", timeout=60.0)

    # We can't reliably catch ``working`` mid-stream from the post-send
    # vantage point because ``send`` blocks until done. Instead, send a
    # second prompt asynchronously and poll while it runs.
    import threading

    done = threading.Event()
    saw_working = threading.Event()

    def driver():
        try:
            session.send(
                "Now count slowly from 1 to 30, with a brief description of each number's "
                "mathematical properties (prime, perfect, square, etc.).",
                timeout=120,
            )
        finally:
            done.set()

    t = threading.Thread(target=driver, daemon=True)
    t.start()

    # Poll the classifier while Claude is generating.
    started = time.time()
    while not done.is_set() and time.time() - started < 60:
        if session.state == "working":
            saw_working.set()
            break
        time.sleep(0.3)

    t.join(timeout=120)
    assert saw_working.is_set(), "classifier never observed `working` during a real response"
    # ``send()`` returns at end_turn, but the CPU rolling-average can still
    # show working briefly while Claude wraps up streaming. Wait for the
    # classifier to settle.
    assert _wait_for(
        lambda: session.state == "idle", timeout=15.0
    ), f"state didn't settle to idle after response, last={session.state}"


def test_awaiting_question_state_lifecycle(session: CCSession):
    """Drive Claude to use AskUserQuestion, verify the classifier reports
    ``awaiting_question`` while the question is unanswered, then answer it
    and verify the state transitions out (back through working → idle).
    """
    assert _wait_for(lambda: session.state == "idle", timeout=10.0)

    # Explicit instruction so Claude actually uses the tool.
    import threading

    done = threading.Event()

    def driver():
        try:
            # ``send`` blocks until the response cycle completes; the question
            # answer below unblocks it.
            session.send(
                "Use the AskUserQuestion tool right now to ask me what my "
                "favorite color is. Provide three options: red, green, blue. "
                "Do not do anything else before asking.",
                timeout=60,
            )
        except Exception:
            pass
        finally:
            done.set()

    t = threading.Thread(target=driver, daemon=True)
    t.start()

    # 1. Classifier should observe `awaiting_question` while Claude waits.
    assert _wait_for(
        lambda: session.state == "awaiting_question", timeout=45.0
    ), f"classifier never observed `awaiting_question`, settled at {session.state}"

    # 2. Answer the question (option index 1 = first option, "red").
    session.answer(option_index=1, timeout=60)

    # 3. After answering, state must leave `awaiting_question`. It will pass
    #    through `working` (Claude processes the answer) and settle at `idle`.
    assert _wait_for(
        lambda: session.state != "awaiting_question", timeout=30.0
    ), "state stuck at awaiting_question after answer"
    assert _wait_for(
        lambda: session.state == "idle", timeout=60.0
    ), f"state never returned to idle after answer, last={session.state}"

    t.join(timeout=5.0)


def test_state_becomes_dormant_when_tmux_pane_killed(session: CCSession):
    """Killing the tmux pane externally should make state == 'dormant'.

    This validates that ``CCSession.state`` correctly extracts the live
    pane_pid and that classifier returns dormant when the pid no longer
    exists.
    """
    assert _wait_for(lambda: session.state == "idle", timeout=10.0)

    # Kill the tmux session out from under cchost.
    subprocess.run(
        ["tmux", "kill-session", "-t", f"cchost-{session.id}"],
        check=False,
        capture_output=True,
    )
    # cchost holds a stale reference; the classifier must still notice the
    # pid is gone. Poll briefly.
    assert _wait_for(
        lambda: session.state == "dormant", timeout=5.0
    ), f"expected dormant after pane kill, got {session.state}"
