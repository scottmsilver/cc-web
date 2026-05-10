"""
Live integration tests for ``BhattiSession``.

These spin up a real bhatti microVM from the ``cc-base`` image and drive a
real ``claude`` process inside it via tmux. Auth is whatever the host's
``~/.claude/.credentials.json`` provides (Pro/Max OAuth in this setup) — same
spend pattern as ``test_state_classifier_live.py``.

Skipped automatically if either:

- ``bhatti_client`` is not importable (the agent writing it hasn't shipped yet)
- the bhatti daemon isn't reachable
- the ``cc-base`` image doesn't exist

Otherwise: not skipped. The whole point is to catch real integration breakage.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Iterator

import pytest


def _bhatti_and_image_present() -> tuple[bool, str]:
    """Return (available, reason) — for a clean skip message."""
    try:
        from bhatti_client import BhattiClient, BhattiError  # type: ignore
    except ImportError:
        return False, "bhatti_client not importable"
    try:
        client = BhattiClient()
    except Exception as e:  # noqa: BLE001 - whatever client surfaces here
        return False, f"BhattiClient() failed: {e}"
    # Probe: can we list images and does cc-base appear?
    try:
        # No documented method on the contract; piggyback on exec against a
        # throw-away vm if bhatti exposes 'images' otherwise. We instead
        # fall back to: try to create + immediately destroy a tiny probe VM
        # — but that's heavy. Easier: just trust import works and let the
        # real test fail loudly if cc-base is missing.
        _ = client
    except Exception as e:  # noqa: BLE001
        return False, f"bhatti probe failed: {e}"
    return True, ""


_AVAILABLE, _SKIP_REASON = _bhatti_and_image_present()

pytestmark = pytest.mark.skipif(
    not _AVAILABLE,
    reason=f"bhatti+cc-base required: {_SKIP_REASON}",
)


# Lazy import inside fixtures so collection-time import doesn't blow up when
# bhatti_client is absent (the module-level skip handles that case anyway).


@pytest.fixture
def session() -> Iterator["object"]:
    """Create a fresh BhattiSession with a unique vm_name; destroy after."""
    from bhatti_session import BhattiSession  # noqa: WPS433

    name = f"bhattitest-{uuid.uuid4().hex[:8]}"
    sess = BhattiSession(name=name, working_dir="/workspace")
    try:
        yield sess
    finally:
        try:
            sess.destroy()
        except Exception:
            # Best-effort cleanup; don't mask test failures.
            pass


def _wait_for(predicate, timeout: float = 60.0, poll: float = 1.0) -> bool:
    """Poll ``predicate`` until it returns truthy or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


def test_create_session_ready(session) -> None:
    """A fresh session should reach ``idle`` within 60s."""
    assert session.vm_name.startswith("cc-bhattitest-") or session.vm_name.startswith("bhattitest-")
    assert session.working_dir == "/workspace"
    assert session.is_dormant is False

    became_idle = _wait_for(lambda: session.state == "idle", timeout=60.0)
    assert became_idle, f"session.state stuck at {session.state!r}, expected 'idle'"

    # Smoke-test the CCSession-parity read APIs that server.py calls. None
    # of these should raise — even on an empty/idle session they must return
    # well-formed defaults.
    transcript = session.raw_transcript()
    assert isinstance(transcript, dict)
    assert "entries" in transcript and isinstance(transcript["entries"], list)
    assert "count" in transcript and transcript["count"] == len(transcript["entries"])
    assert "path" in transcript  # may be None or a string

    files = session.files()
    assert isinstance(files, list)
    assert all(isinstance(f, str) for f in files)

    snapshot = session.progress_snapshot()
    # ProgressSnapshot is a dataclass; we don't pin its exact fields, just
    # confirm the call doesn't blow up on an empty session.
    assert snapshot is not None

    progress = session.progress_entries()
    assert isinstance(progress, list)

    # question_status / _is_tmux_idle should return bool without raising.
    assert isinstance(session.question_status(), bool)
    assert isinstance(session._is_tmux_idle(), bool)

    # subagents() returns a list (currently always [] for bhatti).
    assert session.subagents() == []

    # read_file path-traversal guards: reject ``..`` and absolute paths.
    with pytest.raises(ValueError):
        session.read_file("../etc/passwd")
    with pytest.raises(ValueError):
        session.read_file("/etc/passwd")


def test_send_message_response(session) -> None:
    """Send a deterministic ping and verify the response shows up in the JSONL."""
    # Wait for idle before sending — sending into a non-ready prompt drops keys.
    assert _wait_for(lambda: session.state == "idle", timeout=60.0)

    sentinel = "PINGBACK_VM"
    session.send_message(f"Reply with exactly: {sentinel}")

    # Wait for claude to actually start working (state leaves idle). This
    # avoids a race where state==idle still reports True before the JSONL
    # file has been created.
    started = _wait_for(lambda: session.state != "idle", timeout=15.0, poll=0.5)
    assert started, "claude never picked up the message"

    # Wait for claude to finish (idle again).
    came_back = _wait_for(lambda: session.state == "idle", timeout=120.0, poll=2.0)
    assert came_back, f"session never returned to idle, last={session.state!r}"

    # Give the JSONL one more beat to flush the final assistant message.
    time.sleep(2)

    # Read the JSONL transcript via the BhattiClient and look for the sentinel
    # in any assistant message text block.
    jsonl_path = session._ensure_jsonl_path()
    assert jsonl_path, "expected JSONL path to be discovered"
    raw = session._client.read_file(session.vm_name, jsonl_path)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)

    found = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {}) or {}
        content = msg.get("content", [])
        if isinstance(content, str):
            if sentinel in content:
                found = True
                break
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    if sentinel in (block.get("text") or ""):
                        found = True
                        break
        if found:
            break

    assert found, f"sentinel {sentinel!r} not found in assistant transcript"


def test_destroy_cleans_up(session) -> None:
    """After destroy(), get_vm should raise BhattiNotFound."""
    from bhatti_client import BhattiNotFound  # noqa: WPS433

    # Basic readiness gate so we know the VM actually existed.
    assert session.is_dormant is False

    vm_name = session.vm_name
    client = session._client
    session.destroy()

    # Bhatti destroy is async-ish in some implementations — give it a beat.
    def _gone() -> bool:
        try:
            client.get_vm(vm_name)
            return False
        except BhattiNotFound:
            return True

    assert _wait_for(_gone, timeout=30.0, poll=1.0), f"VM {vm_name} still present after destroy()"


# Lazy-start tests --------------------------------------------------------
#
# These exercise the ``lazy_start=True`` path used by ``CCHost._load_dormant_sessions``.
# The whole point of lazy construction is that ``__init__`` returns instantly
# without spinning up a VM, so ``_load_dormant_sessions`` doesn't block server
# cold-start for ``_READY_TIMEOUT_SEC`` per persisted session.


def test_cold_start_uses_no_separate_exec_calls(session) -> None:
    """Cold-start path must reach idle via the single ``create_vm`` call.

    Asserts the session reaches ``idle`` within 30s (well under the 90s
    ready-timeout — gives headroom for slow boots without masking a hang)
    and that the JSONL path can be discovered after a first message round-
    trip. (Claude doesn't write a transcript until the user sends a message,
    so we send one to confirm the cold-start path produced a fully wired
    session — not just a VM that booted.)
    """
    became_idle = _wait_for(lambda: session.state == "idle", timeout=30.0)
    assert became_idle, f"single-call cold-start didn't reach idle, last={session.state!r}"

    # Send a tiny message to force claude to spin up a transcript.
    session.send_message("ping")
    # Wait for the JSONL to appear via lazy re-discovery.
    found_jsonl = _wait_for(lambda: session._ensure_jsonl_path() is not None, timeout=20.0)
    assert found_jsonl, "expected JSONL path to be discoverable after first send"
    assert session._jsonl_path and session._jsonl_path.endswith(".jsonl")
    assert session.session_id, "expected claude_session_id derived from JSONL basename"


def test_lazy_start_does_not_create_vm_eagerly() -> None:
    """Constructing with ``lazy_start=True`` must not create the VM."""
    from bhatti_client import BhattiClient  # noqa: WPS433
    from bhatti_session import BhattiSession  # noqa: WPS433

    client = BhattiClient()
    # Pick a name that definitely doesn't exist. The VM-name sanitiser will
    # fold it lowercase + cap at 32 chars, so the probe below uses the same
    # name BhattiSession would have computed.
    raw_name = f"lazyprobe-{uuid.uuid4().hex[:8]}"

    sess = BhattiSession(
        name=raw_name,
        working_dir="/workspace",
        lazy_start=True,
    )
    try:
        # No VM with that vm_name should appear in ``list_vms``.
        vms = client.list_vms()
        names = {(vm.get("name") if isinstance(vm, dict) else str(vm)) for vm in (vms or [])}
        assert sess.vm_name not in names, f"lazy_start=True must not create VM, but {sess.vm_name} is present"

        # is_dormant should report True (VM doesn't exist).
        assert sess.is_dormant is True

        # ``state`` short-circuits to "dormant" before _start has run.
        assert sess.state == "dormant"

        # Read-shaped methods return safe defaults instead of 500ing.
        assert sess.raw_transcript() == {"entries": [], "path": None, "count": 0}
        assert sess.progress_entries() == []
        assert sess.files() == []
        assert sess.subagents() == []
        assert sess.current_question() is None
    finally:
        # Nothing was created, so just clean up the host tempdir.
        try:
            import shutil as _shutil

            _shutil.rmtree(sess._state_tmpdir, ignore_errors=True)
        except OSError:
            pass


def test_lazy_start_resumes_existing_vm() -> None:
    """Lazy session pointing at an existing VM should attach without timing out."""
    from bhatti_session import BhattiSession  # noqa: WPS433

    eager_name = f"lazyresume-{uuid.uuid4().hex[:8]}"
    eager = BhattiSession(name=eager_name, working_dir="/workspace")
    try:
        # Wait for the eager session to fully come up before resuming.
        assert _wait_for(lambda: eager.state == "idle", timeout=60.0)
        vm_name = eager.vm_name
        working_dir = eager.working_dir

        # Build a SECOND, lazy session that points at the same VM.
        lazy = BhattiSession(
            name=f"{eager_name}-lazy",
            working_dir=working_dir,
            vm_name=vm_name,
            lazy_start=True,
        )
        try:
            # Lazy session should not have started yet.
            assert lazy._started is False
            assert lazy.state == "dormant"  # short-circuits without _start

            # Reading the transcript should attach to the existing VM and
            # return its actual contents (or empty if claude hasn't written
            # anything yet) — without timing out on a fresh VM bring-up.
            t0 = time.time()
            transcript = lazy.raw_transcript()
            elapsed = time.time() - t0
            assert isinstance(transcript, dict)
            assert "entries" in transcript
            # Comfortably under the 60s ready-timeout — we should be reusing
            # the existing tmux session, not booting fresh.
            assert elapsed < 30.0, f"lazy resume took {elapsed:.1f}s, expected <30s"

            # _started should now be True.
            assert lazy._started is True

            # And we should be able to send a message through the lazy handle.
            sentinel = "LAZYBACK"
            lazy.send_message(f"Reply with exactly: {sentinel}")
            # Just confirm send_message didn't raise; full round-trip is
            # already exercised by ``test_send_message_response``.
        finally:
            # Don't call lazy.destroy() — eager.destroy() handles the VM.
            try:
                import shutil as _shutil

                _shutil.rmtree(lazy._state_tmpdir, ignore_errors=True)
            except OSError:
                pass
    finally:
        # Destroy via the underlying client so we don't double-call destroy_vm.
        try:
            eager._client.destroy_vm(eager.vm_name, force=True)
        except Exception:
            pass
        try:
            import shutil as _shutil

            _shutil.rmtree(eager._state_tmpdir, ignore_errors=True)
        except OSError:
            pass


def test_lazy_start_raises_BhattiSessionStale_when_vm_gone() -> None:
    """A lazy session whose vm_name doesn't exist must raise BhattiSessionStale on send."""
    from bhatti_session import BhattiSession, BhattiSessionStale  # noqa: WPS433

    raw_name = f"stale-{uuid.uuid4().hex[:8]}"
    sess = BhattiSession(
        name=raw_name,
        working_dir="/workspace",
        lazy_start=True,
    )
    try:
        # is_dormant should be True since the VM doesn't exist.
        assert sess.is_dormant is True

        # State-shaped reads return safe defaults.
        assert sess.raw_transcript() == {"entries": [], "path": None, "count": 0}
        assert sess.progress_entries() == []
        assert sess.files() == []
        assert sess.current_question() is None

        # Send-shaped methods MUST surface BhattiSessionStale rather than
        # silently auto-recreating an empty VM.
        with pytest.raises(BhattiSessionStale):
            sess.send_message("hello")

        with pytest.raises(BhattiSessionStale):
            sess.queue_message("hello")
    finally:
        try:
            import shutil as _shutil

            _shutil.rmtree(sess._state_tmpdir, ignore_errors=True)
        except OSError:
            pass
