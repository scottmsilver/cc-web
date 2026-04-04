import json

from cchost import CCSession
from progress import (
    ProgressSnapshot,
    derive_progress_snapshot,
    infer_tool_label,
    normalize_jsonl_entries,
    normalize_jsonl_entry,
)


def test_normalizes_assistant_content_blocks():
    entry = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "text", "text": "Build complete."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
                {"type": "thinking", "thinking": "Checking the final state."},
            ]
        },
    }

    events = normalize_jsonl_entry(entry)

    assert [event.kind for event in events] == [
        "assistant.text",
        "assistant.tool_use",
        "assistant.thinking",
    ]
    assert events[0].text == "Build complete."
    assert events[1].tool_name == "Bash"
    assert events[1].command == "pytest -q"
    assert events[2].text == "Checking the final state."


def test_infers_confident_test_label_from_bash_command():
    label, confidence = infer_tool_label("Bash", "npm test -- --runInBand")

    assert label == "test"
    assert confidence >= 0.9


def test_extracts_queue_and_task_notification_events():
    events = normalize_jsonl_entries(
        [
            {"type": "queue-operation", "operation": "enqueue", "task_id": "bg-1", "label": "index"},
            {"type": "queue-operation", "operation": "dequeue", "task_id": "bg-1"},
            {
                "type": "assistant",
                "message": {
                    "content": '<task-notification>{"text":"Background task queued","task_id":"bg-2"}</task-notification>'
                },
            },
        ]
    )

    assert [event.kind for event in events] == [
        "queue.enqueue",
        "queue.dequeue",
        "task.notification",
    ]
    assert events[0].data["task_id"] == "bg-1"
    assert events[2].text == "Background task queued"
    assert events[2].data["task_id"] == "bg-2"


def test_preserves_assistant_text_around_embedded_task_notification():
    events = normalize_jsonl_entries(
        [
            {
                "type": "assistant",
                "message": {
                    "content": 'Working now. <task-notification>{"text":"Background task queued","task_id":"bg-2"}</task-notification> Thanks.'
                },
            }
        ]
    )

    assert [event.kind for event in events] == ["assistant.text", "task.notification", "assistant.text"]
    assert events[0].text == "Working now."
    assert events[1].text == "Background task queued"
    assert events[1].data["task_id"] == "bg-2"
    assert events[2].text == "Thanks."


def test_parses_xml_task_notification_payload_into_useful_fields():
    events = normalize_jsonl_entries(
        [
            {
                "type": "assistant",
                "message": {
                    "content": '<task-notification>\n<task-id>a6d5c1e0</task-id>\n<status>completed</status>\n<summary>Agent "Match line items to docs" completed</summary>\n<result>done</result>\n</task-notification>'
                },
            }
        ]
    )

    assert len(events) == 1
    event = events[0]
    assert event.kind == "task.notification"
    assert event.label == "agent.completed"
    assert event.text == 'Agent "Match line items to docs" completed'
    assert event.data["task_id"] == "a6d5c1e0"
    assert event.data["status"] == "completed"
    assert event.data["summary"] == 'Agent "Match line items to docs" completed'
    assert event.data["result"] == "done"
    assert event.label_source == "explicit"


def test_derives_human_meaningful_milestones_and_label_source():
    events = normalize_jsonl_entries(
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Reading the invoice files."},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "rg -n invoice ."}},
                    ]
                },
            },
            {"type": "queue-operation", "operation": "enqueue", "task_id": "bg-1"},
            {
                "type": "assistant",
                "message": {
                    "content": '<task-notification><task-id>bg-1</task-id><status>completed</status><summary>Agent "Match line items to docs" completed</summary></task-notification>'
                },
            },
        ]
    )

    snapshot = derive_progress_snapshot(events, is_question=True, is_prompt=False)

    assert snapshot.milestones == [
        "Started reading files",
        "Background task started",
        "Agent completed: Match line items to docs",
        "Waiting for user input",
    ]
    assert snapshot.primary_label_source in {"explicit", "inferred"}
    assert snapshot.primary_label_source == "explicit"


def test_derives_snapshot_and_session_state(tmp_path):
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {"type": "text", "text": "Starting work."},
                                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}},
                            ]
                        },
                    }
                ),
                json.dumps({"type": "queue-operation", "operation": "enqueue", "task_id": "bg-1"}),
            ]
        )
        + "\n"
    )

    session = CCSession(id="session-1", working_dir=str(tmp_path), _jsonl_path=str(jsonl_path))
    session._tmux_shows_question = lambda: True  # type: ignore[method-assign]
    session._is_tmux_idle = lambda: True  # type: ignore[method-assign]

    entries = session.progress_entries()
    snapshot = session.progress_snapshot()

    assert session._last_line_count == 0
    assert len(entries) == 3
    assert isinstance(snapshot, ProgressSnapshot)
    assert snapshot.background_count == 1
    assert snapshot.primary_label == "test"
    assert snapshot.primary_label_source == "inferred"
    assert snapshot.primary_confidence >= 0.9
    assert snapshot.milestones == ["Started work", "Running tests", "Background task started", "Waiting for user input"]
    assert snapshot.is_question is True
    assert snapshot.is_prompt is True


def test_progress_reads_do_not_advance_existing_cursor(tmp_path):
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "assistant", "message": {"content": "First line"}}),
                json.dumps({"type": "assistant", "message": {"content": "Second line"}}),
                json.dumps({"type": "queue-operation", "operation": "enqueue", "task_id": "bg-1"}),
            ]
        )
        + "\n"
    )

    session = CCSession(id="session-2", working_dir=str(tmp_path), _jsonl_path=str(jsonl_path), _last_line_count=2)

    entries = session.progress_entries()

    assert session._last_line_count == 2
    assert [event.kind for event in entries] == ["assistant.text", "assistant.text", "queue.enqueue"]
