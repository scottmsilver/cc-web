import threading
import time
from datetime import datetime, timezone

import pytest
import server
from cchost import QuestionOption, Response
from fastapi.testclient import TestClient
from progress import ProgressEvent, ProgressSnapshot


class FakeSession:
    def __init__(self):
        self.created_at = datetime.now(timezone.utc)
        self.id = "session-1"
        self.working_dir = "/tmp/session-1"
        self.send_started = threading.Event()
        self.send_continue = threading.Event()
        self.answer_started = threading.Event()
        self.answer_continue = threading.Event()
        self.phase = "idle"
        self.send_response = Response(text="done")
        self.answer_response = Response(text="answered")
        self.legacy_question = None
        self.send_calls: list[tuple[str, int]] = []
        self.answer_calls: list[int] = []

    def send(self, message: str, timeout: int = 600) -> Response:
        self.send_calls.append((message, timeout))
        self.phase = "running"
        self.send_started.set()
        self.send_continue.wait(timeout=2)
        if self.send_response.is_question:
            self.phase = "waiting_for_input"
            self.legacy_question = self.send_response.questions[0]
        else:
            self.phase = "completed"
            self.legacy_question = None
        return self.send_response

    def answer(self, option_index: int = 1) -> Response:
        self.answer_calls.append(option_index)
        self.phase = "running"
        self.answer_started.set()
        self.answer_continue.wait(timeout=2)
        if self.answer_response.is_question:
            self.phase = "waiting_for_input"
            self.legacy_question = self.answer_response.questions[0]
        else:
            self.phase = "completed"
            self.legacy_question = None
        return self.answer_response

    def current_question(self):
        return self.legacy_question

    def progress_snapshot(self) -> ProgressSnapshot:
        is_question = self.phase == "waiting_for_input"
        is_prompt = self.phase in {"idle", "completed"}
        milestones = ["Started reading files"]
        if is_question:
            milestones.append("Waiting for user input")
        elif is_prompt:
            milestones.append("Ready for input")

        return ProgressSnapshot(
            events=[
                ProgressEvent(
                    kind="assistant.text",
                    label="message",
                    confidence=0.5,
                    label_source="inferred",
                    text=f"phase:{self.phase}",
                )
            ],
            background_count=1 if self.phase == "running" else 0,
            primary_label="inspect",
            primary_confidence=0.75,
            primary_label_source="inferred",
            milestones=milestones,
            is_question=is_question,
            is_prompt=is_prompt,
        )


class FakeHost:
    def __init__(self, session: FakeSession):
        self.session = session

    def get(self, session_id: str) -> FakeSession:
        if session_id != self.session.id:
            raise KeyError(session_id)
        return self.session


@pytest.fixture
def client(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(server, "host", FakeHost(session))
    monkeypatch.setattr(server, "run_manager", server.RunManager())
    return TestClient(server.app), session


def _wait_for_status(client: TestClient, session_id: str, run_id: str, expected: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/sessions/{session_id}/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] == expected:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Run {run_id} did not reach status {expected!r}")


def test_async_run_lifecycle_and_progress_snapshot(client):
    test_client, session = client

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Inspect the repo", "timeout": 30},
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["session_id"] == session.id
    assert created["status"] in {"pending", "running"}
    assert created["started_at"] is not None
    assert created["finished_at"] is None
    run_id = created["run_id"]

    assert session.send_started.wait(timeout=1)

    running = _wait_for_status(test_client, session.id, run_id, "running")
    assert running["result"] is None
    assert running["error"] is None

    progress = test_client.get(f"/api/sessions/{session.id}/progress")
    assert progress.status_code == 200
    progress_payload = progress.json()
    assert progress_payload["run"]["run_id"] == run_id
    assert progress_payload["run"]["status"] == "running"
    assert progress_payload["snapshot"]["primary_label"] == "inspect"
    assert progress_payload["snapshot"]["events"][0]["text"] == "phase:running"
    assert progress_payload["snapshot"]["milestones"] == ["Started reading files"]

    session.send_continue.set()
    completed = _wait_for_status(test_client, session.id, run_id, "completed")
    assert completed["finished_at"] is not None
    assert completed["result"]["text"] == "done"
    assert completed["result"]["role"] == "assistant"
    assert completed["waiting_for_input"] is False
    assert completed["current_question"] is None

    progress_after = test_client.get(f"/api/sessions/{session.id}/progress")
    assert progress_after.status_code == 200
    after_payload = progress_after.json()
    assert after_payload["run"]["status"] == "completed"
    assert [event["text"] for event in after_payload["snapshot"]["events"]] == [
        "phase:running",
        "phase:completed",
    ]
    assert after_payload["snapshot"]["milestones"] == ["Started reading files", "Ready for input"]


def test_pending_progress_poll_does_not_contaminate_async_run_history(client, monkeypatch):
    test_client, session = client
    original_execute = server.run_manager.execute_send_run
    release_worker = threading.Event()

    def delayed_execute(run_id, session_obj, message, timeout):
        release_worker.wait(timeout=1)
        return original_execute(run_id, session_obj, message, timeout)

    monkeypatch.setattr(server.run_manager, "execute_send_run", delayed_execute)

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Inspect the repo", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]

    pending_progress = test_client.get(f"/api/sessions/{session.id}/progress")
    assert pending_progress.status_code == 200
    pending_payload = pending_progress.json()
    assert pending_payload["run"]["run_id"] == run_id
    assert pending_payload["run"]["status"] == "pending"
    assert [event["text"] for event in pending_payload["snapshot"]["events"]] == ["phase:idle"]

    release_worker.set()
    assert session.send_started.wait(timeout=1)

    running_progress = test_client.get(f"/api/sessions/{session.id}/progress")
    assert running_progress.status_code == 200
    running_payload = running_progress.json()
    assert running_payload["run"]["status"] == "running"
    assert [event["text"] for event in running_payload["snapshot"]["events"]] == ["phase:running"]

    session.send_continue.set()
    _wait_for_status(test_client, session.id, run_id, "completed")

    completed_progress = test_client.get(f"/api/sessions/{session.id}/progress")
    assert completed_progress.status_code == 200
    completed_payload = completed_progress.json()
    assert completed_payload["run"]["status"] == "completed"
    assert [event["text"] for event in completed_payload["snapshot"]["events"]] == [
        "phase:running",
        "phase:completed",
    ]


def test_progress_polls_preserve_timeline_history(client):
    test_client, session = client

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Inspect the repo", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]
    assert session.send_started.wait(timeout=1)

    first_progress = test_client.get(f"/api/sessions/{session.id}/progress")
    assert first_progress.status_code == 200
    first_payload = first_progress.json()
    assert first_payload["run"]["run_id"] == run_id
    assert [event["text"] for event in first_payload["snapshot"]["events"]] == ["phase:running"]
    assert first_payload["snapshot"]["milestones"] == ["Started reading files"]

    session.send_continue.set()
    _wait_for_status(test_client, session.id, run_id, "completed")

    second_progress = test_client.get(f"/api/sessions/{session.id}/progress")
    assert second_progress.status_code == 200
    second_payload = second_progress.json()
    assert second_payload["run"]["status"] == "completed"
    assert [event["text"] for event in second_payload["snapshot"]["events"]] == [
        "phase:running",
        "phase:completed",
    ]
    assert second_payload["snapshot"]["milestones"] == ["Started reading files", "Ready for input"]


def test_progress_hides_completed_async_run_during_legacy_send(client):
    test_client, session = client
    send_response_holder: dict[str, object] = {}

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Async path", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]
    assert session.send_started.wait(timeout=1)
    session.send_continue.set()
    _wait_for_status(test_client, session.id, run_id, "completed")
    session.send_started.clear()
    session.send_continue.clear()

    def submit_legacy_send():
        send_response_holder["response"] = test_client.post(
            f"/api/sessions/{session.id}/send",
            json={"message": "Legacy path", "timeout": 15},
        )

    send_thread = threading.Thread(target=submit_legacy_send, daemon=True)
    send_thread.start()
    assert session.send_started.wait(timeout=1)

    progress = test_client.get(f"/api/sessions/{session.id}/progress")

    assert progress.status_code == 200
    payload = progress.json()
    assert payload["run"] is None
    assert payload["snapshot"]["events"][0]["text"] == "phase:running"

    session.send_continue.set()
    send_thread.join(timeout=2)
    assert "response" in send_response_holder
    assert send_response_holder["response"].status_code == 200


def test_legacy_answer_rejects_while_legacy_send_is_in_flight(client):
    test_client, session = client
    send_response_holder: dict[str, object] = {}

    def submit_legacy_send():
        send_response_holder["response"] = test_client.post(
            f"/api/sessions/{session.id}/send",
            json={"message": "Legacy path", "timeout": 15},
        )

    send_thread = threading.Thread(target=submit_legacy_send, daemon=True)
    send_thread.start()

    assert session.send_started.wait(timeout=1)

    response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A run is already active for this session"
    assert session.answer_calls == []

    session.send_continue.set()
    send_thread.join(timeout=2)
    assert "response" in send_response_holder
    assert send_response_holder["response"].status_code == 200


def test_completed_run_allows_legacy_answer_fallback(client):
    test_client, session = client

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Inspect the repo", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]
    assert session.send_started.wait(timeout=1)
    session.send_continue.set()
    _wait_for_status(test_client, session.id, run_id, "completed")

    session.answer_response = Response(text="legacy answer", role="assistant")
    session.legacy_question = {
        "question": "Legacy pick",
        "options": [QuestionOption(label="Accept", index=1)],
    }
    session.answer_continue.set()

    response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": 1},
    )

    assert response.status_code == 200
    assert response.json()["text"] == "legacy answer"
    assert session.answer_calls == [1]


def test_send_and_run_creation_are_atomically_exclusive(client, monkeypatch):
    test_client, session = client
    original_check = server.run_manager.require_no_active_run
    checked = threading.Event()
    release_check = threading.Event()
    send_response_holder: dict[str, object] = {}

    def wrapped_check(session_id: str):
        original_check(session_id)
        checked.set()
        release_check.wait(timeout=1)

    monkeypatch.setattr(server.run_manager, "require_no_active_run", wrapped_check)

    def submit_legacy_send():
        send_response_holder["response"] = test_client.post(
            f"/api/sessions/{session.id}/send",
            json={"message": "Legacy path", "timeout": 15},
        )

    send_thread = threading.Thread(target=submit_legacy_send, daemon=True)
    send_thread.start()

    assert checked.wait(timeout=1)

    run_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Async path", "timeout": 30},
    )

    release_check.set()
    session.send_continue.set()
    send_thread.join(timeout=2)

    assert "response" in send_response_holder
    send_response = send_response_holder["response"]
    assert send_response.status_code == 200
    assert run_response.status_code == 409
    assert run_response.json()["detail"] == "A run is already active for this session"
    assert session.send_calls == [("Legacy path", 15)]


def test_answer_rejects_when_active_run_is_not_waiting_for_input(client):
    test_client, session = client

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Inspect the repo", "timeout": 30},
    )

    assert create_response.status_code == 200
    assert session.send_started.wait(timeout=1)

    response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Run is active and not waiting for input"
    assert session.answer_calls == []

    session.send_continue.set()


def test_blocking_send_rejects_while_async_run_is_active(client):
    test_client, session = client

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Inspect the repo", "timeout": 30},
    )

    assert create_response.status_code == 200
    assert session.send_started.wait(timeout=1)
    assert session.send_calls == [("Inspect the repo", 30)]

    response = test_client.post(
        f"/api/sessions/{session.id}/send",
        json={"message": "Second message", "timeout": 15},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "A run is already active for this session"
    assert session.send_calls == [("Inspect the repo", 30)]

    session.send_continue.set()


def test_tracked_answer_uses_exposed_sparse_option_indexes(client):
    test_client, session = client
    session.send_response = Response(
        text="Choose a mode",
        role="assistant",
        is_question=True,
        questions=[
            {
                "question": "Choose a mode",
                "options": [QuestionOption(label="Fast", index=1), QuestionOption(label="Safe", index=3)],
            }
        ],
    )

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Start the task", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]
    assert session.send_started.wait(timeout=1)
    session.send_continue.set()
    _wait_for_status(test_client, session.id, run_id, "waiting_for_input")

    invalid_response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": 2},
    )

    assert invalid_response.status_code == 400
    assert invalid_response.json()["detail"] == "option_index must be one of [1, 3]"
    assert session.answer_calls == []

    session.answer_response = Response(text="picked sparse option", role="assistant")
    session.answer_continue.set()
    valid_response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": 3},
    )

    assert valid_response.status_code == 200
    assert valid_response.json()["text"] == "picked sparse option"
    assert session.answer_calls == [3]


def test_waiting_run_rejects_concurrent_answer_requests(client, monkeypatch):
    test_client, session = client
    session.send_response = Response(
        text="Choose a mode",
        role="assistant",
        is_question=True,
        questions=[
            {
                "question": "Choose a mode",
                "options": [QuestionOption(label="Fast", index=1), QuestionOption(label="Safe", index=2)],
            }
        ],
    )

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Start the task", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]
    assert session.send_started.wait(timeout=1)
    session.send_continue.set()
    _wait_for_status(test_client, session.id, run_id, "waiting_for_input")

    original_continue = server.run_manager.continue_run_with_answer
    first_claimed = threading.Event()
    release_continue = threading.Event()

    def wrapped_continue(run, session_obj, option_index):
        first_claimed.set()
        release_continue.wait(timeout=1)
        return original_continue(run, session_obj, option_index)

    monkeypatch.setattr(server.run_manager, "continue_run_with_answer", wrapped_continue)

    session.answer_response = Response(text="first answer", role="assistant")
    first_answer_holder: dict[str, object] = {}
    second_answer_holder: dict[str, object] = {}

    def submit_first_answer():
        first_answer_holder["response"] = test_client.post(
            f"/api/sessions/{session.id}/answer",
            json={"option_index": 1},
        )

    def submit_second_answer():
        second_answer_holder["response"] = test_client.post(
            f"/api/sessions/{session.id}/answer",
            json={"option_index": 1},
        )

    first_thread = threading.Thread(target=submit_first_answer, daemon=True)
    first_thread.start()
    assert first_claimed.wait(timeout=1)

    second_thread = threading.Thread(target=submit_second_answer, daemon=True)
    second_thread.start()
    second_thread.join(timeout=0.3)

    assert "response" in second_answer_holder
    second_response = second_answer_holder["response"]
    assert second_response.status_code == 409
    assert second_response.json()["detail"] == "Run is active and not waiting for input"
    assert session.answer_calls == []

    release_continue.set()
    assert session.answer_started.wait(timeout=1)
    session.answer_continue.set()
    first_thread.join(timeout=1)
    assert "response" in first_answer_holder
    assert first_answer_holder["response"].status_code == 200
    assert session.answer_calls == [1]


@pytest.mark.parametrize("option_index", [0, 3])
def test_tracked_answer_rejects_invalid_option_index(client, option_index):
    test_client, session = client
    session.send_response = Response(
        text="Choose a mode",
        role="assistant",
        is_question=True,
        questions=[
            {
                "question": "Choose a mode",
                "options": [QuestionOption(label="Fast", index=1), QuestionOption(label="Safe", index=2)],
            }
        ],
    )

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Start the task", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]
    assert session.send_started.wait(timeout=1)
    session.send_continue.set()
    _wait_for_status(test_client, session.id, run_id, "waiting_for_input")

    response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": option_index},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "option_index must be between 1 and 2"
    assert session.answer_calls == []


def test_legacy_answer_requires_active_question_prompt(client):
    test_client, session = client

    response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": 1},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Session is not waiting for an answer"
    assert session.answer_calls == []


def test_legacy_answer_rejects_invalid_option_index(client):
    test_client, session = client
    session.legacy_question = {
        "question": "Legacy pick",
        "options": [QuestionOption(label="Only choice", index=1)],
    }

    response = test_client.post(
        f"/api/sessions/{session.id}/answer",
        json={"option_index": 2},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "option_index must be between 1 and 1"
    assert session.answer_calls == []


def test_waiting_for_input_run_can_continue_after_answer(client):
    test_client, session = client
    session.send_response = Response(
        text="Choose a mode",
        role="assistant",
        is_question=True,
        questions=[
            {
                "question": "Choose a mode",
                "options": [QuestionOption(label="Fast", index=1), QuestionOption(label="Safe", index=2)],
            }
        ],
    )

    create_response = test_client.post(
        f"/api/sessions/{session.id}/runs",
        json={"message": "Start the task", "timeout": 30},
    )

    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]
    assert session.send_started.wait(timeout=1)
    session.send_continue.set()

    waiting = _wait_for_status(test_client, session.id, run_id, "waiting_for_input")
    assert waiting["waiting_for_input"] is True
    assert waiting["current_question"]["question"] == "Choose a mode"
    assert waiting["current_question"]["options"] == [
        {"label": "Fast", "index": 1},
        {"label": "Safe", "index": 2},
    ]

    progress = test_client.get(f"/api/sessions/{session.id}/progress")
    assert progress.status_code == 200
    progress_payload = progress.json()
    assert progress_payload["run"]["status"] == "waiting_for_input"
    assert progress_payload["snapshot"]["is_question"] is True
    assert progress_payload["snapshot"]["milestones"][-1] == "Waiting for user input"

    session.answer_response = Response(text="Final answer", role="assistant")

    answer_response_holder: dict[str, object] = {}

    def submit_answer():
        answer_response_holder["response"] = test_client.post(
            f"/api/sessions/{session.id}/answer",
            json={"option_index": 2},
        )

    answer_thread = threading.Thread(target=submit_answer, daemon=True)
    answer_thread.start()

    assert session.answer_started.wait(timeout=1)
    resumed = _wait_for_status(test_client, session.id, run_id, "running")
    assert resumed["waiting_for_input"] is False
    assert resumed["current_question"] is None

    session.answer_continue.set()
    answer_thread.join(timeout=1)
    assert "response" in answer_response_holder
    answer_response = answer_response_holder["response"]
    assert answer_response.status_code == 200

    completed = _wait_for_status(test_client, session.id, run_id, "completed")
    assert completed["result"]["text"] == "Final answer"
    assert session.answer_calls == [2]


def test_blocking_send_endpoint_remains_compatible(client):
    test_client, session = client
    session.send_response = Response(
        text="Need approval",
        role="assistant",
        is_question=True,
        questions=[
            {
                "question": "Need approval",
                "options": [QuestionOption(label="Yes", index=1)],
            }
        ],
    )

    def release_send():
        assert session.send_started.wait(timeout=1)
        session.send_continue.set()

    releaser = threading.Thread(target=release_send, daemon=True)
    releaser.start()
    response = test_client.post(
        f"/api/sessions/{session.id}/send",
        json={"message": "Proceed?", "timeout": 15},
    )
    releaser.join(timeout=1)

    assert response.status_code == 200
    assert response.json() == {
        "text": "Need approval",
        "is_question": True,
        "questions": [{"question": "Need approval", "options": [{"label": "Yes", "index": 1}]}],
        "role": "assistant",
    }
