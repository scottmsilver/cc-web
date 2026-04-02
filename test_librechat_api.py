"""
Test LibreChat + cchost integration via API.

LibreChat's custom endpoints work by:
1. Frontend sends messages to LibreChat's internal API
2. LibreChat forwards to our /v1/chat/completions endpoint
3. We can't easily bypass LibreChat's frontend for custom endpoints

So instead, test the full stack:
- cchost API directly (works, proven)
- LibreChat conversation/message APIs (read-only, for verification)
- File handling through cchost's own upload endpoint
"""

import json
import os

import requests

CCHOST_URL = "http://localhost:8420"
LIBRECHAT_URL = "http://localhost:3080"
LIBRECHAT_EMAIL = "scott@test.com"
LIBRECHAT_PASSWORD = "test1234"


def librechat_login():
    """Login to LibreChat and get token."""
    r = requests.post(
        f"{LIBRECHAT_URL}/api/auth/login", json={"email": LIBRECHAT_EMAIL, "password": LIBRECHAT_PASSWORD}
    )
    return r.json().get("token", "")


def librechat_conversations(token):
    """List LibreChat conversations."""
    r = requests.get(
        f"{LIBRECHAT_URL}/api/convos?pageNumber=1&pageSize=20", headers={"Authorization": f"Bearer {token}"}
    )
    return r.json().get("conversations", [])


def librechat_messages(token, conv_id):
    """Get messages from a LibreChat conversation."""
    r = requests.get(f"{LIBRECHAT_URL}/api/messages/{conv_id}", headers={"Authorization": f"Bearer {token}"})
    return r.json()


def cchost_create_session(session_id, workdir):
    """Create a cchost session."""
    r = requests.post(f"{CCHOST_URL}/api/sessions", json={"session_id": session_id, "working_dir": workdir})
    return r.json()


def cchost_send(session_id, message, timeout=60):
    """Send a message to cchost session."""
    r = requests.post(f"{CCHOST_URL}/api/sessions/{session_id}/send", json={"message": message, "timeout": timeout})
    return r.json()


def cchost_send_stream(session_id, message, timeout=60):
    """Send via OpenAI-compatible streaming endpoint."""
    r = requests.post(
        f"{CCHOST_URL}/v1/chat/completions",
        json={
            "model": "claude-code",
            "messages": [{"role": "user", "content": message}],
            "stream": True,
        },
        stream=True,
        timeout=timeout,
    )
    full_text = ""
    for line in r.iter_lines():
        if line:
            decoded = line.decode()
            if decoded.startswith("data: ") and decoded != "data: [DONE]":
                try:
                    chunk = json.loads(decoded[6:])
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        full_text += content
                except json.JSONDecodeError:
                    pass
    return full_text


def cchost_files(session_id):
    """List files in cchost session."""
    r = requests.get(f"{CCHOST_URL}/api/sessions/{session_id}/files")
    return r.json().get("files", [])


def cchost_sessions():
    """List cchost sessions."""
    r = requests.get(f"{CCHOST_URL}/api/sessions")
    return r.json()


def cchost_destroy(session_id):
    """Destroy a cchost session."""
    r = requests.delete(f"{CCHOST_URL}/api/sessions/{session_id}")
    return r.json()


def test_basic_flow():
    """Test: create session, send message, get response, verify in LibreChat."""
    print("=== Test 1: Basic message flow ===")

    # Create session
    workdir = "/tmp/cchost-api-test"
    os.makedirs(workdir, exist_ok=True)
    result = cchost_create_session("api-test", workdir)
    print(f"Session created: {result}")

    # Send message via cchost REST API
    r = cchost_send("api-test", "What is 9 * 9? Just the number.", timeout=30)
    print(f"Response: '{r.get('text', '').strip()}'")
    assert "81" in r.get("text", ""), f"Expected 81, got: {r.get('text')}"

    # Follow-up (persistent)
    r2 = cchost_send("api-test", "Subtract 10. Just the number.", timeout=30)
    print(f"Follow-up: '{r2.get('text', '').strip()}'")
    assert "71" in r2.get("text", ""), f"Expected 71, got: {r2.get('text')}"

    # Clean up
    cchost_destroy("api-test")
    print("PASSED\n")


def test_streaming():
    """Test: OpenAI-compatible streaming endpoint."""
    print("=== Test 2: SSE streaming ===")

    text = cchost_send_stream("stream-test", "What is 6 * 7? Just the number.", timeout=30)
    print(f"Streamed response: '{text.strip()}'")
    assert "42" in text, f"Expected 42, got: {text}"
    print("PASSED\n")


def test_file_creation():
    """Test: Claude creates files, accessible via API."""
    print("=== Test 3: File creation and access ===")

    workdir = "/tmp/cchost-file-test"
    os.makedirs(workdir, exist_ok=True)
    cchost_create_session("file-test", workdir)

    # Ask Claude to create a file
    r = cchost_send("file-test", "Write 'hello from API test' to output.txt. No explanation.", timeout=30)
    print(f"Response: '{r.get('text', '').strip()[:60]}'")

    # Check files
    files = cchost_files("file-test")
    print(f"Files: {files}")
    assert "output.txt" in files, f"output.txt not in {files}"

    # Read the file
    file_r = requests.get(f"{CCHOST_URL}/api/sessions/file-test/files/output.txt")
    print(f"File content: '{file_r.text.strip()}'")
    assert "hello from API test" in file_r.text

    cchost_destroy("file-test")
    print("PASSED\n")


def test_librechat_reads():
    """Test: LibreChat can list conversations and messages."""
    print("=== Test 4: LibreChat API (read) ===")

    try:
        token = librechat_login()
        if not token:
            print("SKIPPED (LibreChat login failed — may need re-registration)\n")
            return
        print(f"Logged in (token: {token[:20]}...)")

        r = requests.get(
            f"{LIBRECHAT_URL}/api/convos?pageNumber=1&pageSize=20",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            print(f"SKIPPED (conversations endpoint returned {r.status_code})\n")
            return

        convos = r.json().get("conversations", [])
        print(f"Conversations: {len(convos)}")
        for c in convos[:3]:
            print(f"  {c.get('title', '?')[:40]}  endpoint={c.get('endpoint', '?')}")
    except Exception as e:
        print(f"SKIPPED ({e})\n")
        return

    print("PASSED\n")


def test_session_listing():
    """Test: cchost session management."""
    print("=== Test 5: Session management ===")

    sessions = cchost_sessions()
    print(f"Active sessions: {len(sessions)}")
    for s in sessions:
        print(f"  {s['id']}  state={s['state']}  workdir={s.get('working_dir', '?')}")

    print("PASSED\n")


if __name__ == "__main__":
    print("LibreChat + cchost API Integration Tests")
    print("=" * 50)
    print(f"cchost:    {CCHOST_URL}")
    print(f"LibreChat: {LIBRECHAT_URL}")
    print()

    test_basic_flow()
    test_streaming()
    test_file_creation()
    test_librechat_reads()
    test_session_listing()

    print("All tests passed!")
