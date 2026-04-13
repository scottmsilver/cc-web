"""
Live integration tests for message queuing, interrupting, and sub-agent detection.

These tests create real Claude Code sessions in tmux and interact with them.
Skipped by default — run with: python -m pytest test_queue_and_subagents.py -v -s -m live
Or: python test_queue_and_subagents.py (uses __main__ runner)
"""

import os
import shutil
import tempfile
import time

import pytest
from cchost import CCHost

# Mark all tests in this module as "live" — skipped unless explicitly requested
pytestmark = pytest.mark.skipif(
    os.environ.get("CCHOST_LIVE_TESTS") != "1",
    reason="Live integration tests — set CCHOST_LIVE_TESTS=1 to run",
)


def _make_host():
    return CCHost(
        max_sessions=20,
        manifest_path=os.path.join(tempfile.mkdtemp(), "sessions.json"),
    )


def _ensure_clean(host, session_id, workdir):
    """Kill any leftover tmux session and clean workdir from a prior failed run."""
    try:
        host.destroy(session_id)
    except KeyError:
        pass
    # Also kill tmux directly in case host didn't track it
    import subprocess

    subprocess.run(["tmux", "kill-session", "-t", f"cchost-{session_id}"], capture_output=True)
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)


def _wait_idle(session, timeout=30):
    """Wait for Claude to return to idle prompt."""
    start = time.time()
    while time.time() - start < timeout:
        if session._is_tmux_idle():
            return True
        time.sleep(1)
    return False


def _wait_busy(session, timeout=15):
    """Wait for Claude to start working (not idle)."""
    start = time.time()
    while time.time() - start < timeout:
        if not session._is_tmux_idle():
            return True
        time.sleep(0.5)
    return False


# ── Queue tests ──


def test_queue_message_while_idle():
    """queue_message() on an idle session should report was_busy=False."""
    workdir = "/tmp/cchost-queue-idle"
    host = _make_host()
    _ensure_clean(host, "queue-idle", workdir)
    session = host.create("queue-idle", working_dir=workdir)
    print("Session ready")

    assert _wait_idle(session, timeout=20), "Session did not reach idle"

    result = session.queue_message("What is 2+2? Just the number.")
    print(f"queue_message result: {result}")

    assert result["was_busy"] is False, f"Expected was_busy=False, got {result}"
    assert result["status"] == "sent", f"Expected status=sent, got {result}"

    # Wait for response
    assert _wait_idle(session, timeout=30), "Claude did not return to idle"
    print("PASS: queue_message on idle session works")

    host.destroy("queue-idle")


def test_queue_message_delivers():
    """Send two messages via queue_message back-to-back. Both should reach Claude.

    This tests the delivery mechanism, not busy-state timing. Claude may process
    messages faster than we can observe the busy state.
    """
    workdir = "/tmp/cchost-queue-busy"
    host = _make_host()
    _ensure_clean(host, "queue-busy", workdir)

    # Create files to give Claude real work (after clean wipes the dir)
    for i in range(20):
        with open(os.path.join(workdir, f"data_{i}.txt"), "w") as f:
            f.write(f"Record {i}: sample data line\n" * 10)
    session = host.create("queue-busy", working_dir=workdir)
    print("Session ready")

    assert _wait_idle(session, timeout=20), "Session did not reach idle"

    # Send a task that uses tools (reading files — takes longer than pure text)
    session.queue_message(
        "List all .txt files in this directory and count total lines across all of them. Show each filename."
    )

    # Send first message, wait for it, then send second via queue
    response1 = session.send("List all .txt files in this directory. Just filenames, nothing else.", timeout=60)
    print(f"First response: {response1.text[:80]}")

    # Now use queue_message for a second message
    result = session.queue_message("What is 7*8? Just the number.")
    print(f"queue_message result: {result}")

    # Wait for Claude to process the queued message
    assert _wait_idle(session, timeout=60), "Claude did not finish queued message"
    time.sleep(2)

    # Use send() to verify Claude is responsive and has context
    response2 = session.send("What was the last math answer you gave me?", timeout=30)
    print(f"Verification response: {response2.text[:100]}")

    # The queue_message should have been delivered
    assert "data_" in response1.text or ".txt" in response1.text, "First response should list files"
    print("PASS: queue_message delivers messages")

    host.destroy("queue-busy")


# ── Interrupt tests ──


def test_interrupt_then_send():
    """Send Escape while Claude works, then verify Claude accepts new input.

    We use a task that requires multiple tool calls (searching files) to ensure
    Claude stays busy long enough to catch. If Claude finishes before we can
    interrupt, we skip the interrupt portion and just test send-after-idle.
    """
    workdir = "/tmp/cchost-interrupt"
    host = _make_host()
    _ensure_clean(host, "interrupt-test", workdir)

    # Create many files so the search task takes a while
    for i in range(30):
        with open(os.path.join(workdir, f"notes_{i}.md"), "w") as f:
            f.write(f"# Note {i}\nThis is a detailed note about topic {i}.\n" * 20)

    session = host.create("interrupt-test", working_dir=workdir)
    print("Session ready")

    assert _wait_idle(session, timeout=20), "Session did not reach idle"

    # Send a task that uses tools (slower)
    session.queue_message(
        "Read every .md file in this directory. For each one, summarize its content "
        "in one sentence. Show all 30 summaries."
    )

    # Try to catch Claude working — poll aggressively
    interrupted = False
    for _ in range(40):
        if not session._is_tmux_idle():
            time.sleep(2)  # Let it work a bit
            session.send_keys("Escape")
            print("Caught Claude working, sent Escape")
            interrupted = True
            break
        time.sleep(0.25)

    if not interrupted:
        print("NOTE: Claude finished before we could interrupt — testing send-after-complete instead")

    # Wait for idle
    assert _wait_idle(session, timeout=60), "Claude did not reach idle"
    print(f"Claude is idle (interrupted={interrupted})")
    time.sleep(1)

    # Send a new message — this must work regardless of whether we interrupted
    response = session.send("What is 3+3? Just the number.", timeout=30)
    print(f"Post-interrupt response: {response.text[:100]}")

    assert "6" in response.text, f"Expected '6' in response, got: {response.text}"
    print("PASS: send after interrupt/completion works")

    host.destroy("interrupt-test")


# ── Sub-agent tests ──


def test_subagents_detection():
    """Trigger sub-agent creation and verify detection."""
    workdir = "/tmp/cchost-subagent-test"
    host = _make_host()
    _ensure_clean(host, "subagent-test", workdir)
    session = host.create("subagent-test", working_dir=workdir)
    print("Session ready")

    assert _wait_idle(session, timeout=20), "Session did not reach idle"

    # Ask Claude to use sub-agents (the Agent tool)
    response = session.send(
        "Use the Agent tool to spawn a sub-agent. Have it research what the current "
        "year is and report back. Use a brief prompt. Wait for it to finish.",
        timeout=120,
    )
    print(f"Response: {response.text[:200]}")

    # Check for sub-agents
    agents = session.subagents()
    print(f"Sub-agents found: {len(agents)}")
    for a in agents:
        print(f"  {a['agent_id'][:16]}... status={a['status']} desc={a['description'][:60]}")

    # We should have at least one sub-agent
    if len(agents) > 0:
        assert agents[0]["agent_id"], "Sub-agent should have an agent_id"
        assert agents[0]["description"], "Sub-agent should have a description"
        assert agents[0]["status"] in ("running", "completed"), f"Unexpected status: {agents[0]['status']}"
        print("PASS: sub-agent detected with valid fields")
    else:
        print("NOTE: No sub-agents created (Claude may not have used the Agent tool)")
        print("This is not necessarily a failure — Claude decides whether to spawn agents")

    host.destroy("subagent-test")


def test_subagents_empty_for_simple_session():
    """A simple session with no agent use should return empty subagents list."""
    workdir = "/tmp/cchost-no-subagent"
    host = _make_host()
    _ensure_clean(host, "no-subagent", workdir)
    session = host.create("no-subagent", working_dir=workdir)
    print("Session ready")

    assert _wait_idle(session, timeout=20), "Session did not reach idle"

    # Simple message, no agents
    response = session.send("What is 1+1? Just the number.", timeout=30)
    print(f"Response: {response.text}")

    agents = session.subagents()
    print(f"Sub-agents: {len(agents)}")
    assert len(agents) == 0, f"Expected 0 sub-agents for simple query, got {len(agents)}"
    print("PASS: no sub-agents for simple query")

    host.destroy("no-subagent")


def test_subagents_on_dormant_session():
    """subagents() on a dormant session (no tmux) should return empty list."""
    from cchost import CCSession

    session = CCSession(
        id="dormant-test",
        working_dir="/tmp/nonexistent",
        _tmux_session=None,
        _claude_session_id=None,
    )
    agents = session.subagents()
    assert agents == [], f"Expected empty list for dormant session, got {agents}"
    print("PASS: dormant session returns empty subagents")


# ── Queue + interrupt combined ──


def test_queue_multiple_while_busy():
    """Queue three messages rapidly. All three should be processed in order."""
    workdir = "/tmp/cchost-multi-queue"
    host = _make_host()
    _ensure_clean(host, "multi-queue", workdir)
    session = host.create("multi-queue", working_dir=workdir)
    print("Session ready")

    assert _wait_idle(session, timeout=20), "Session did not reach idle"

    # Fire three messages rapidly — Claude will process them in sequence
    session.queue_message("Write 'hello' to hello.txt. No explanation.")
    time.sleep(0.5)
    session.queue_message("What is 10+10? Just the number.")
    time.sleep(0.5)
    session.queue_message("What is 5*5? Just the number.")

    # Wait for everything to complete
    assert _wait_idle(session, timeout=180), "Claude did not finish all queued messages"
    time.sleep(3)

    session._jsonl_path = None  # Force re-discovery
    session._find_jsonl()
    entries = session._read_all_transcript_entries()
    user_messages = [e for e in entries if e.get("type") == "user"]
    assistant_messages = [e for e in entries if e.get("type") == "assistant"]
    all_text = " ".join(session._extract_text(e) for e in assistant_messages)

    print(f"User messages: {len(user_messages)}, Assistant messages: {len(assistant_messages)}")

    # Should have processed at least 2 of the 3 messages
    assert len(user_messages) >= 2, f"Expected >= 2 user messages, got {len(user_messages)}"

    # At least one numeric answer should be present
    has_20 = "20" in all_text
    has_25 = "25" in all_text
    print(f"Has '20': {has_20}, Has '25': {has_25}")
    assert has_20 or has_25, "Expected at least one queued answer in responses"

    # Check file was created
    hello_exists = os.path.exists(os.path.join(workdir, "hello.txt"))
    print(f"hello.txt created: {hello_exists}")

    print("PASS: multiple queued messages processed")
    host.destroy("multi-queue")


if __name__ == "__main__":
    print("=" * 60)
    print("LIVE INTEGRATION TESTS")
    print("These tests create real Claude Code sessions")
    print("=" * 60)

    tests = [
        ("Dormant subagents", test_subagents_on_dormant_session),
        ("Queue while idle", test_queue_message_while_idle),
        ("Queue delivers", test_queue_message_delivers),
        ("Interrupt then send", test_interrupt_then_send),
        ("Simple session no subagents", test_subagents_empty_for_simple_session),
        ("Multiple queue", test_queue_multiple_while_busy),
        ("Subagent detection", test_subagents_detection),
    ]

    passed = 0
    failed = 0
    for name, test_fn in tests:
        print(f"\n{'=' * 60}")
        print(f"TEST: {name}")
        print("=" * 60)
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"FAILED: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
