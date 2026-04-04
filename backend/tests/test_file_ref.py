"""
Test: upload a zip file, ask Claude about it via @./filename,
verify Claude reads it directly (no find/glob to locate it).
"""

import os
import time

import requests
from conftest import create_test_zip

CCHOST = os.environ.get("CCHOST_URL", "http://localhost:8420")
TIMEOUT = 120  # seconds to wait for run completion


def main():
    # 1. Create session
    sid = f"test-fileref-{int(time.time())}"
    print(f"Creating session: {sid}")
    r = requests.post(
        f"{CCHOST}/api/sessions",
        json={
            "session_id": sid,
            "working_dir": f"/tmp/cchost-test/{sid}",
        },
    )
    r.raise_for_status()
    session = r.json()
    print(f"  Session created: {session.get('id') or session.get('session_id')}")

    # 2. Upload zip
    zip_bytes = create_test_zip()
    print(f"Uploading test.zip ({len(zip_bytes)} bytes, 3 files inside)")
    r = requests.post(
        f"{CCHOST}/api/sessions/{sid}/upload",
        files={"test.zip": ("test.zip", zip_bytes, "application/zip")},
    )
    r.raise_for_status()
    uploaded = r.json()
    print(f"  Uploaded: {uploaded}")

    # 3. Send message with @./test.zip reference
    message = "@./test.zip How many files are in this zip? Just give the count."
    print(f"Sending message: {message!r}")
    r = requests.post(
        f"{CCHOST}/api/sessions/{sid}/runs",
        json={
            "message": message,
            "timeout": TIMEOUT,
        },
    )
    r.raise_for_status()
    run = r.json()
    run_id = run["run_id"]
    print(f"  Run started: {run_id}")

    # 4. Poll for completion
    start = time.time()
    result = None
    while time.time() - start < TIMEOUT:
        time.sleep(2)
        r = requests.get(f"{CCHOST}/api/sessions/{sid}/runs/{run_id}")
        r.raise_for_status()
        run = r.json()
        status = run.get("status")
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] status={status}")
        if status == "completed":
            result = run
            break
        if status == "error":
            print(f"  ERROR: {run.get('error')}")
            return

    if not result:
        print("TIMEOUT waiting for run")
        return

    # 5. Get the response text
    response_text = result.get("result", {}).get("text", "")
    print(f"\nClaude's response:\n{response_text[:500]}")

    # 6. Check progress events for tool usage
    r = requests.get(f"{CCHOST}/api/sessions/{sid}/progress")
    r.raise_for_status()
    progress = r.json()
    events = progress.get("snapshot", {}).get("events", [])

    used_find = False
    used_bash_find = False
    tools_used = []
    for evt in events:
        label = evt.get("label", "")
        detail = evt.get("detail", "")
        tools_used.append(f"{label}: {detail[:80]}")
        if "find" in label.lower() or "find" in detail.lower():
            used_find = True
        if "glob" in label.lower() or "glob" in detail.lower():
            used_find = True
        if "bash" in label.lower() and "find" in detail.lower():
            used_bash_find = True

    print(f"\nTools used ({len(tools_used)}):")
    for t in tools_used:
        print(f"  {t}")

    # 7. Also check conversation for Bash find commands
    r = requests.get(f"{CCHOST}/api/sessions/{sid}/conversation")
    r.raise_for_status()
    convo = r.json().get("conversation", [])

    find_in_convo = False
    for entry in convo:
        text = str(entry.get("text", ""))
        if entry.get("role") == "assistant":
            # Check if assistant used find/locate commands
            if "find " in text or "locate " in text or "which test.zip" in text:
                find_in_convo = True
                print(f"\n  FOUND 'find' in assistant message: {text[:200]}")

    print("\n--- RESULTS ---")
    print(f"Response mentions 3 files: {'3' in response_text}")
    print(f"Used find/glob tool: {used_find}")
    print(f"Used bash find: {used_bash_find}")
    print(f"Find in conversation: {find_in_convo}")

    if not used_find and not used_bash_find and not find_in_convo:
        print("PASS: Claude read the file directly via @ reference")
    else:
        print("FAIL: Claude searched for the file instead of reading it directly")

    # Cleanup
    requests.delete(f"{CCHOST}/api/sessions/{sid}")
    print(f"\nSession {sid} deleted.")


if __name__ == "__main__":
    main()
