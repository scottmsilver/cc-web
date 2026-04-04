"""
Test: upload a zip, ask Claude about it via @./filename,
dump full conversation + JSONL to verify no find/glob was used.
"""

import json
import os
import time

import requests
from conftest import create_test_zip

CCHOST = os.environ.get("CCHOST_URL", "http://localhost:8420")
TIMEOUT = 120


def main():
    sid = f"test-fileref-{int(time.time())}"
    print(f"Creating session: {sid}")
    r = requests.post(
        f"{CCHOST}/api/sessions",
        json={"session_id": sid, "working_dir": f"/tmp/cchost-test/{sid}"},
    )
    r.raise_for_status()

    # Upload
    zip_bytes = create_test_zip()
    r = requests.post(
        f"{CCHOST}/api/sessions/{sid}/upload",
        files={"test.zip": ("test.zip", zip_bytes, "application/zip")},
    )
    r.raise_for_status()

    # Send message
    message = "@./test.zip How many files are in this zip? Just give the count."
    print(f"Message: {message}")
    r = requests.post(
        f"{CCHOST}/api/sessions/{sid}/runs",
        json={"message": message, "timeout": TIMEOUT},
    )
    r.raise_for_status()
    run_id = r.json()["run_id"]

    # Poll
    start = time.time()
    while time.time() - start < TIMEOUT:
        time.sleep(2)
        r = requests.get(f"{CCHOST}/api/sessions/{sid}/runs/{run_id}")
        r.raise_for_status()
        run = r.json()
        if run["status"] in ("completed", "error"):
            break

    print(f"\n{'='*60}")
    print("RESPONSE:", run.get("result", {}).get("text", "")[:500])

    # Full conversation
    print(f"\n{'='*60}")
    print("FULL CONVERSATION:")
    r = requests.get(f"{CCHOST}/api/sessions/{sid}/conversation")
    r.raise_for_status()
    convo = r.json().get("conversation", [])
    for i, entry in enumerate(convo):
        role = entry.get("role", "?")
        text = str(entry.get("text", ""))
        print(f"\n--- [{i}] {role} ---")
        print(text[:1000])

    # Full progress events
    print(f"\n{'='*60}")
    print("PROGRESS EVENTS:")
    r = requests.get(f"{CCHOST}/api/sessions/{sid}/progress")
    r.raise_for_status()
    progress = r.json()
    events = progress.get("snapshot", {}).get("events", [])
    for evt in events:
        print(json.dumps(evt, indent=2)[:300])

    # Try to read the JSONL transcript directly
    print(f"\n{'='*60}")
    print("JSONL TRANSCRIPT (tool_use entries):")
    working_dir = f"/tmp/cchost-test/{sid}"
    # Find JSONL files in Claude's project dir
    claude_dir = os.path.expanduser("~/.claude/projects")
    jsonl_path = None
    for root, dirs, files in os.walk(claude_dir):
        for f in files:
            if f.endswith(".jsonl"):
                fpath = os.path.join(root, f)
                mtime = os.path.getmtime(fpath)
                if time.time() - mtime < 60:  # modified in last 60s
                    jsonl_path = fpath
                    break

    if jsonl_path:
        print(f"Reading: {jsonl_path}")
        with open(jsonl_path) as fh:
            for line in fh:
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                # Look for tool_use entries
                if entry.get("type") == "assistant":
                    msg = entry.get("message", {})
                    for block in msg.get("content", []):
                        if block.get("type") == "tool_use":
                            tool = block.get("name", "")
                            inp = json.dumps(block.get("input", {}))[:200]
                            print(f"  TOOL: {tool} -> {inp}")
                        if block.get("type") == "text":
                            txt = block.get("text", "")[:200]
                            if txt.strip():
                                print(f"  TEXT: {txt}")
    else:
        print("  No recent JSONL found")

    # Cleanup
    requests.delete(f"{CCHOST}/api/sessions/{sid}")
    print(f"\nSession {sid} deleted.")


if __name__ == "__main__":
    main()
