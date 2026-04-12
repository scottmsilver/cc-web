"""
Test harness for done-detection signals.

Sends different types of messages to Claude Code via cchost and logs
exactly what JSONL entries appear and when. This tells us what signal
to use for "Claude is done responding."

Scenarios:
1. Simple text response (no tools)
2. Single tool use (file read)
3. Multi-tool response (bash + write)
4. Sub-agent dispatch
5. AskUserQuestion trigger
6. Error case (invalid command)
"""

import glob
import json
import os
import shutil
import time

from cchost import CCHost


def find_jsonl(workdir: str) -> str:
    slug = workdir.replace("/", "-").lstrip("-")
    project_dir = os.path.expanduser(f"~/.claude/projects/-{slug}")
    files = glob.glob(os.path.join(project_dir, "*.jsonl"))
    return max(files, key=os.path.getmtime) if files else ""


def watch_jsonl(jsonl_path: str, start_line: int, timeout: int = 60, poll: float = 0.3) -> list[dict]:
    """Watch JSONL for new entries, recording timestamps."""
    events = []
    start_time = time.time()
    last_count = start_line

    while time.time() - start_time < timeout:
        if not os.path.exists(jsonl_path):
            time.sleep(poll)
            continue

        with open(jsonl_path, "r") as f:
            lines = f.readlines()

        for i in range(last_count, len(lines)):
            line = lines[i].strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                elapsed = time.time() - start_time
                events.append(
                    {
                        "line": i,
                        "elapsed_s": round(elapsed, 2),
                        "type": entry.get("type", "?"),
                        "subtype": entry.get("subtype", ""),
                    }
                )
            except json.JSONDecodeError:
                pass
        last_count = len(lines)

        # Check if we see a done signal
        for e in events:
            if e["type"] == "last-prompt":
                # Found it — wait a tiny bit more for any trailing entries
                time.sleep(1)
                # Re-read
                with open(jsonl_path, "r") as f:
                    final_lines = f.readlines()
                for i in range(last_count, len(final_lines)):
                    line = final_lines[i].strip()
                    if line:
                        try:
                            entry = json.loads(line)
                            elapsed = time.time() - start_time
                            events.append(
                                {
                                    "line": i,
                                    "elapsed_s": round(elapsed, 2),
                                    "type": entry.get("type", "?"),
                                    "subtype": entry.get("subtype", ""),
                                }
                            )
                        except json.JSONDecodeError:
                            pass
                return events

        time.sleep(poll)

    return events  # timeout — return what we have


def get_line_count(jsonl_path: str) -> int:
    if not os.path.exists(jsonl_path):
        return 0
    with open(jsonl_path, "r") as f:
        return len(f.readlines())


def run_test(name: str, session, message: str, timeout: int = 60):
    """Send a message and watch what JSONL entries appear."""
    print(f"\n{'='*60}")
    print(f"TEST: {name}")
    print(f"Message: {message[:60]}...")
    print(f"{'='*60}")

    jsonl = find_jsonl(session.working_dir)
    start_line = get_line_count(jsonl) if jsonl else 0

    # Send message via tmux
    pane = session._tmux_session.active_window.active_pane
    pane.send_keys(message, enter=True)

    # Watch for JSONL events
    # Need to re-find JSONL if it didn't exist before
    time.sleep(2)
    if not jsonl:
        jsonl = find_jsonl(session.working_dir)
        start_line = 0

    events = watch_jsonl(jsonl, start_line, timeout=timeout)

    # Also check tmux screen state
    time.sleep(1)
    captured = "\n".join(pane.capture_pane(start=-10))
    tmux_idle = "❯" in captured and captured.strip().split("\n")[-3].strip() == "❯" if captured else False

    print(f"\nJSONL events ({len(events)}):")
    for e in events:
        print(f"  +{e['elapsed_s']:>6.2f}s  line {e['line']:>3}  {e['type']}/{e['subtype']}")

    has_last_prompt = any(e["type"] == "last-prompt" for e in events)
    last_event = events[-1] if events else None
    done_time = next((e["elapsed_s"] for e in events if e["type"] == "last-prompt"), None)

    print("\nResults:")
    print(f"  last-prompt in JSONL: {'YES at +{:.2f}s'.format(done_time) if done_time else 'NO'}")
    print(f"  tmux shows ❯ idle:   {'YES' if tmux_idle else 'NO'}")
    print(f"  total events:        {len(events)}")
    print(f"  last event type:     {last_event['type'] if last_event else 'none'}")

    return {
        "name": name,
        "has_last_prompt": has_last_prompt,
        "tmux_idle": tmux_idle,
        "done_time": done_time,
        "num_events": len(events),
        "events": events,
    }


def main():
    workdir = "/tmp/cchost-done-test"
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir, exist_ok=True)

    host = CCHost(manifest_path=os.path.join(tempfile.mkdtemp(), "sessions.json"))
    session = host.create("done-test", working_dir=workdir)
    print(f"Session ready: {session.id}")

    results = []

    # Test 1: Simple text (no tools)
    r = run_test(
        "Simple text response",
        session,
        "What is 2+2? Just the number, nothing else.",
        timeout=30,
    )
    results.append(r)

    # Wait for idle
    time.sleep(3)

    # Test 2: Single tool use (read a file)
    r = run_test(
        "Single tool use (Bash)",
        session,
        "Run: echo hello. Show me the output, nothing else.",
        timeout=30,
    )
    results.append(r)

    time.sleep(3)

    # Test 3: Multi-tool (write + read back)
    r = run_test(
        "Multi-tool (write file + read back)",
        session,
        "Write 'test123' to /tmp/cchost-done-test/done-test.txt then read it back. Show just the content.",
        timeout=30,
    )
    results.append(r)

    time.sleep(3)

    # Test 4: Longer response (multi-paragraph)
    r = run_test(
        "Longer text response",
        session,
        "Write a 3-line haiku about tmux sessions. Then explain each line in one sentence.",
        timeout=30,
    )
    results.append(r)

    time.sleep(3)

    # Test 5: Error case
    r = run_test(
        "Invalid tool / error",
        session,
        "Read the file /tmp/this-file-does-not-exist-12345.txt",
        timeout=30,
    )
    results.append(r)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Test':<35} {'last-prompt':<15} {'tmux ❯':<10} {'Done at':<10}")
    print("-" * 70)
    for r in results:
        lp = f"+{r['done_time']:.1f}s" if r["done_time"] else "NO"
        tmux = "YES" if r["tmux_idle"] else "NO"
        print(f"{r['name']:<35} {lp:<15} {tmux:<10}")

    session.destroy()
    print("\nDone!")


if __name__ == "__main__":
    main()
