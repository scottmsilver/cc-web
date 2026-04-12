"""
Test AskUserQuestion detection and programmatic response.

When Claude uses AskUserQuestion, the tmux screen shows an interactive picker.
cchost needs to:
1. Detect that Claude is asking a question (not just thinking)
2. Extract the question text and options
3. Allow the caller to select an option
4. Send the selection back to Claude via tmux

We test this by asking Claude to use AskUserQuestion explicitly,
then programmatically answering it.
"""

import os
import shutil
import tempfile
import time

from cchost import CCHost


def test_detect_question():
    """Test that send() detects AskUserQuestion and returns it as a question response."""
    workdir = "/tmp/cchost-ask-test"
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    host = CCHost(manifest_path=os.path.join(tempfile.mkdtemp(), "sessions.json"))
    session = host.create("ask-test", working_dir=workdir)
    print("Session ready")

    # Ask Claude to use AskUserQuestion
    print("\n=== Test 1: Trigger AskUserQuestion ===")
    r = session.send(
        "Use the AskUserQuestion tool to ask me: 'What color do you prefer?' "
        "with options: A) Blue, B) Red, C) Green. Just call the tool, nothing else.",
        timeout=30,
    )
    print(f"Response type: {type(r).__name__}")
    print(f"Response text: '{r.text[:100]}'")
    print(f"Is question: {getattr(r, 'is_question', 'N/A')}")
    if hasattr(r, "questions") and r.questions:
        print(f"Questions: {r.questions}")

    # Check what the tmux screen looks like when AskUserQuestion is shown
    print("\n=== tmux screen ===")
    pane = session._tmux_session.active_window.active_pane
    captured = pane.capture_pane(start=-20)
    for line in captured:
        stripped = line.strip()
        if stripped:
            print(f"  | {stripped}")

    # Can we detect the question from the screen?
    screen_text = "\n".join(captured)
    has_options = any(
        line.strip().startswith(("1.", "2.", "3.", "❯")) and any(word in line for word in ["Blue", "Red", "Green"])
        for line in captured
    )
    print(f"\nOptions visible on screen: {has_options}")

    # Test 2: Answer the question
    print("\n=== Test 2: Answer the question ===")
    if has_options or "Blue" in screen_text or "Red" in screen_text:
        # Select option 1 (Enter selects the currently highlighted option)
        print("Sending Enter to select first option...")
        session.send_keys("Enter")
        time.sleep(3)

        # Check if we're back at idle
        idle = session._is_tmux_idle()
        print(f"Back to idle: {idle}")

        if idle:
            # Get the response after answering
            # The JSONL should have the answer acknowledgment
            print("Checking for follow-up response...")
            time.sleep(2)
            new_lines = session._read_new_lines()
            for entry in new_lines:
                if entry.get("type") == "assistant":
                    text = session._extract_text(entry)
                    if text:
                        print(f"Claude's response: '{text[:100]}'")

    # Test 3: Full round-trip — question, answer, then continue
    print("\n=== Test 3: Full round-trip ===")
    # Wait for idle
    time.sleep(2)
    if session._is_tmux_idle():
        r2 = session.send(
            "Use AskUserQuestion to ask 'Pick a number' with options: "
            "A) 1 (Recommended), B) 2, C) 3. Only call the tool.",
            timeout=30,
        )
        print(f"Response: '{r2.text[:80]}'")

        # Check screen for the question
        time.sleep(1)
        captured2 = pane.capture_pane(start=-15)
        screen2 = "\n".join(captured2)
        print(f"Screen has options: {'1.' in screen2 or '❯' in screen2}")

        # Answer it
        if "Pick" in screen2 or "1" in screen2:
            session.send_keys("Enter")  # Select first option
            time.sleep(3)

            # Now ask a follow-up that proves the answer was received
            r3 = session.send("What option did I just pick? Reply with just the answer.", timeout=30)
            print(f"Follow-up: '{r3.text.strip()}'")

    session.destroy()
    print("\nDone!")


def test_question_in_workflow():
    """Test AskUserQuestion during a real skill invocation (like the analyzer interview)."""
    workdir = "/tmp/cchost-ask-workflow"
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    # Create a dummy PDF so the analyzer has something to look at
    host = CCHost(manifest_path=os.path.join(tempfile.mkdtemp(), "sessions.json"))
    session = host.create("ask-workflow", working_dir=workdir)
    print("Session ready")

    # Write a tiny test file
    session.send("Write 'test invoice' to invoice.txt. No explanation.", timeout=30)
    print("Test file created")

    # Ask Claude to use AskUserQuestion as it would in a skill
    print("\n=== Triggering multi-question interview ===")
    r = session.send(
        "Use AskUserQuestion with 2 questions: "
        "1) 'What is the markup rate?' with options 15%, 12%, 10%, 18%. "
        "2) 'What is the retention policy?' with options: '10% until 75%', '10% until substantial', '5% until 50%', 'No retention'. "
        "Call the tool with both questions.",
        timeout=30,
    )
    print(f"Response: '{r.text[:80]}'")

    # Check screen
    pane = session._tmux_session.active_window.active_pane
    captured = pane.capture_pane(start=-20)
    for line in captured:
        s = line.strip()
        if s:
            print(f"  | {s}")

    # Answer first question
    screen = "\n".join(captured)
    if "markup" in screen.lower() or "15%" in screen or "Markup" in screen:
        print("\nAnswering markup rate (Enter for first option)...")
        session.send_keys("Enter")
        time.sleep(2)

        # Check if there's a second question
        captured2 = pane.capture_pane(start=-15)
        screen2 = "\n".join(captured2)
        if "retention" in screen2.lower() or "10%" in screen2:
            print("Second question visible, answering...")
            session.send_keys("Enter")
            time.sleep(2)

            # Check for submit
            captured3 = pane.capture_pane(start=-10)
            screen3 = "\n".join(captured3)
            if "Submit" in screen3 or "submit" in screen3:
                print("Submit visible, confirming...")
                session.send_keys("Enter")
                time.sleep(3)

    # Check final state
    idle = session._is_tmux_idle()
    print(f"\nFinal idle state: {idle}")

    session.destroy()
    print("Done!")


if __name__ == "__main__":
    print("=" * 60)
    print("TEST SUITE: AskUserQuestion Detection & Response")
    print("=" * 60)

    test_detect_question()
    print("\n" + "=" * 60)
    test_question_in_workflow()
