
"""Progress parser primitives for Claude JSONL transcripts."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Iterable


_TASK_NOTIFICATION_RE = re.compile(r"<task-notification>(.*?)</task-notification>", re.IGNORECASE | re.DOTALL)
_TEST_COMMAND_PATTERNS = (
    "pytest",
    "npm test",
    "pnpm test",
    "yarn test",
    "go test",
    "cargo test",
    "bundle exec rspec",
    "make test",
)


@dataclass(frozen=True)
class ProgressEvent:
    kind: str
    label: str = ""
    confidence: float = 0.0
    label_source: str = "inferred"
    text: str = ""
    tool_name: str = ""
    command: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProgressSnapshot:
    events: list[ProgressEvent]
    background_count: int
    primary_label: str | None
    primary_confidence: float
    primary_label_source: str = "inferred"
    milestones: list[str] = field(default_factory=list)
    is_question: bool = False
    is_prompt: bool = False


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _extract_agent_name(summary: str) -> str:
    match = re.search(r'Agent\s+"([^"]+)"\s+completed', summary, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r'Agent\s+(.+?)\s+completed', summary, re.IGNORECASE)
    if match:
        return match.group(1).strip(' "')
    return ""


def _xml_children_to_data(element: ET.Element) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for child in list(element):
        key = _normalize_key(child.tag)
        if len(list(child)):
            value: Any = _xml_children_to_data(child)
            child_text = _coerce_text("".join(child.itertext()).strip())
            if child_text and not value:
                value = child_text
        else:
            value = _coerce_text((child.text or "").strip())
        if key in data:
            existing = data[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                data[key] = [existing, value]
        else:
            data[key] = value
    return data


def _parse_xml_task_notification(payload_text: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(f"<root>{payload_text}</root>")
    except ET.ParseError:
        return None
    if not list(root):
        return None
    data = _xml_children_to_data(root)
    text = _coerce_text("".join(root.itertext()).strip())
    if text and "text" not in data:
        data["text"] = text
    return data


def _derive_task_notification_details(data: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    normalized = dict(data)
    status = _coerce_text(normalized.get("status")).strip().lower()
    summary = _coerce_text(normalized.get("summary") or normalized.get("text") or normalized.get("message")).strip()
    result = _coerce_text(normalized.get("result")).strip()
    agent_name = _coerce_text(normalized.get("agent_name")).strip()

    if not agent_name and summary:
        agent_name = _extract_agent_name(summary)
        if agent_name:
            normalized["agent_name"] = agent_name

    if status == "completed":
        label = "agent.completed"
        if not summary and agent_name:
            summary = f'Agent "{agent_name}" completed'
    elif status in {"started", "running"}:
        label = "agent.started"
        if not summary and agent_name:
            summary = f'Agent "{agent_name}" started'
    else:
        label = "task.notification"

    text = summary or result or status or _coerce_text(normalized.get("task_id")).strip() or "Task notification"
    if summary:
        normalized["summary"] = summary
    if result:
        normalized["result"] = result
    if status:
        normalized["status"] = status
    if text and "text" not in normalized:
        normalized["text"] = text

    return label, text, status, normalized


def _build_task_notification_event(payload_text: str, raw: dict[str, Any]) -> ProgressEvent | None:
    payload_text = payload_text.strip()
    if not payload_text:
        return None

    data: dict[str, Any] = {}
    if payload_text.startswith("{"):
        try:
            parsed = json.loads(payload_text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            data = parsed
    else:
        xml_data = _parse_xml_task_notification(payload_text)
        if isinstance(xml_data, dict):
            data = xml_data

    if not data:
        data = {"text": payload_text}

    label, notification_text, _status, normalized = _derive_task_notification_details(data)
    return ProgressEvent(
        kind="task.notification",
        label=label,
        confidence=0.98,
        label_source="explicit",
        text=notification_text,
        data=normalized,
        raw=raw,
    )


def _normalize_text_with_notifications(text: str, raw: dict[str, Any]) -> list[ProgressEvent]:
    events: list[ProgressEvent] = []
    last_index = 0
    matched = False

    for match in _TASK_NOTIFICATION_RE.finditer(text):
        matched = True
        prefix = text[last_index:match.start()].strip()
        if prefix:
            events.append(
                ProgressEvent(
                    kind="assistant.text",
                    label="message",
                    confidence=0.5,
                    label_source="inferred",
                    text=prefix,
                    raw=raw,
                )
            )
        notification = _build_task_notification_event(match.group(1), raw)
        if notification:
            events.append(notification)
        last_index = match.end()

    if matched:
        suffix = text[last_index:].strip()
        if suffix:
            events.append(
                ProgressEvent(
                    kind="assistant.text",
                    label="message",
                    confidence=0.5,
                    label_source="inferred",
                    text=suffix,
                    raw=raw,
                )
            )
        return events

    return []


def infer_tool_label(tool_name: str, command: str = "") -> tuple[str, float]:
    tool_name_lc = tool_name.lower().strip()
    command_lc = command.lower().strip()

    if any(pattern in command_lc for pattern in _TEST_COMMAND_PATTERNS):
        return "test", 0.95
    if tool_name_lc in {"read", "read file", "file read"}:
        return "read", 0.9
    if tool_name_lc in {"write", "edit", "replace"}:
        return "write", 0.9
    if tool_name_lc in {"search", "grep", "rg"}:
        return "search", 0.85
    if tool_name_lc in {"bash", "shell", "command", "run"}:
        if any(pattern in command_lc for pattern in ("git ", "git-")):
            return "git", 0.9
        if any(pattern in command_lc for pattern in ("cat ", "sed ", "head ", "tail ", "rg ", "grep ")):
            return "inspect", 0.75
        if command_lc:
            return "shell", 0.6
        return "shell", 0.55
    if tool_name_lc:
        return tool_name_lc.replace(" ", "_"), 0.5
    if command_lc:
        return "command", 0.4
    return "unknown", 0.0


def _milestone_from_event(event: ProgressEvent) -> str | None:
    if event.kind == "assistant.text":
        text = event.text.strip().lower()
        if text.startswith("reading ") or ("reading" in text and any(word in text for word in ("file", "files", "docs", "document"))):
            return "Started reading files"
        if text.startswith("starting "):
            phrase = event.text.strip()[len("Starting ") :].strip().rstrip(".")
            return f"Started {phrase}" if phrase else "Started work"
        if text.startswith("working "):
            phrase = event.text.strip()[len("Working ") :].strip().rstrip(".")
            return f"Working on {phrase}" if phrase else "Working"
        return None

    if event.kind == "assistant.tool_use":
        if event.label in {"read", "inspect", "search"}:
            return "Started reading files"
        if event.label == "test":
            return "Running tests"
        if event.label == "git":
            return "Working in git"
        return None

    if event.kind == "queue.enqueue":
        return "Background task started"

    if event.kind == "queue.dequeue":
        return "Background task finished"

    if event.kind == "queue.remove":
        return "Background task removed"

    if event.kind == "task.notification":
        status = _coerce_text(event.data.get("status")).lower()
        summary = _coerce_text(event.data.get("summary") or event.text)
        agent_name = _coerce_text(event.data.get("agent_name")).strip()
        if status == "completed":
            if not agent_name:
                agent_name = _extract_agent_name(summary)
            if agent_name:
                return f"Agent completed: {agent_name}"
            return "Agent completed"
        if status in {"started", "running"}:
            if agent_name:
                return f"Agent started: {agent_name}"
            return "Agent started"
        if summary:
            return summary
        if status:
            return status.title()
        return "Task notification"

    return None


def normalize_jsonl_entry(entry: dict[str, Any]) -> list[ProgressEvent]:
    entry_type = _coerce_text(entry.get("type")).lower()
    if not entry_type:
        return []

    if entry_type == "assistant":
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = message.get("content") if isinstance(message, dict) else entry.get("content")
        if isinstance(content, list):
            events: list[ProgressEvent] = []
            for block in content:
                if isinstance(block, dict):
                    events.extend(_normalize_assistant_block(block, entry))
            return events
        if isinstance(content, str):
            mixed = _normalize_text_with_notifications(content, entry)
            if mixed:
                return mixed
            return [
                ProgressEvent(
                    kind="assistant.text",
                    label="message",
                    confidence=0.5,
                    label_source="inferred",
                    text=content,
                    raw=entry,
                )
            ]
        return []

    if entry_type == "queue-operation":
        operation = _coerce_text(entry.get("operation") or entry.get("subtype") or entry.get("action")).lower()
        if operation not in {"enqueue", "dequeue", "remove"}:
            operation = "operation"
        data = {k: v for k, v in entry.items() if k not in {"type"}}
        return [
            ProgressEvent(
                kind=f"queue.{operation}",
                label="queue",
                confidence=0.7,
                label_source="explicit",
                text=_coerce_text(entry.get("text") or entry.get("message") or ""),
                data=data,
                raw=entry,
            )
        ]

    if entry_type == "task-notification":
        label, notification_text, _status, data = _derive_task_notification_details(
            {k: v for k, v in entry.items() if k != "type"}
        )
        return [
            ProgressEvent(
                kind="task.notification",
                label=label,
                confidence=0.98,
                label_source="explicit",
                text=notification_text,
                data=data,
                raw=entry,
            )
        ]

    return []


def _normalize_assistant_block(block: dict[str, Any], raw: dict[str, Any]) -> list[ProgressEvent]:
    block_type = _coerce_text(block.get("type")).lower()

    if block_type == "text":
        text = _coerce_text(block.get("text"))
        mixed = _normalize_text_with_notifications(text, raw)
        if mixed:
            return mixed
        return [
            ProgressEvent(
                kind="assistant.text",
                label="message",
                confidence=0.5,
                label_source="inferred",
                text=text,
                raw=raw,
            )
        ]

    if block_type == "tool_use":
        input_data = block.get("input")
        command = ""
        if isinstance(input_data, dict):
            command = _coerce_text(
                input_data.get("command") or input_data.get("text") or input_data.get("prompt") or input_data.get("input")
            )
        elif input_data is not None:
            command = _coerce_text(input_data)

        tool_name = _coerce_text(block.get("name") or block.get("tool_name") or block.get("tool") or block.get("id"))
        label, confidence = infer_tool_label(tool_name, command)
        return [
            ProgressEvent(
                kind="assistant.tool_use",
                label=label,
                confidence=confidence,
                label_source="inferred",
                tool_name=tool_name,
                command=command,
                data={"tool_name": tool_name, "command": command},
                raw=raw,
            )
        ]

    if block_type == "thinking":
        text = _coerce_text(block.get("thinking") or block.get("text") or block.get("content"))
        return [
            ProgressEvent(
                kind="assistant.thinking",
                label="thinking",
                confidence=0.35,
                label_source="inferred",
                text=text,
                raw=raw,
            )
        ]

    text = _coerce_text(block.get("text") or block.get("content") or block.get("message"))
    if text:
        mixed = _normalize_text_with_notifications(text, raw)
        if mixed:
            return mixed
        return [
            ProgressEvent(
                kind=f"assistant.{block_type or 'content'}",
                label=block_type or "assistant",
                confidence=0.25,
                label_source="inferred",
                text=text,
                raw=raw,
            )
        ]
    return []


def normalize_jsonl_entries(entries: Iterable[dict[str, Any]]) -> list[ProgressEvent]:
    normalized: list[ProgressEvent] = []
    for entry in entries:
        if isinstance(entry, dict):
            normalized.extend(normalize_jsonl_entry(entry))
    return normalized


def _coerce_events(entries: Iterable[dict[str, Any] | ProgressEvent]) -> list[ProgressEvent]:
    events: list[ProgressEvent] = []
    for entry in entries:
        if isinstance(entry, ProgressEvent):
            events.append(entry)
        elif isinstance(entry, dict):
            events.extend(normalize_jsonl_entry(entry))
    return events


def derive_progress_snapshot(
    entries: Iterable[dict[str, Any] | ProgressEvent],
    *,
    is_question: bool = False,
    is_prompt: bool = False,
) -> ProgressSnapshot:
    events = _coerce_events(entries)
    background_count = 0
    primary_label: str | None = None
    primary_confidence = 0.0
    primary_label_source = "inferred"
    milestones: list[str] = []
    seen_milestones: set[str] = set()

    for event in events:
        if event.kind == "queue.enqueue":
            background_count += 1
        elif event.kind in {"queue.dequeue", "queue.remove"}:
            background_count = max(0, background_count - 1)

        milestone = _milestone_from_event(event)
        if milestone and milestone not in seen_milestones:
            seen_milestones.add(milestone)
            milestones.append(milestone)

        if event.label and (
            event.confidence > primary_confidence
            or (event.confidence == primary_confidence and primary_label_source != "explicit" and event.label_source == "explicit")
        ):
            primary_label = event.label
            primary_confidence = event.confidence
            primary_label_source = event.label_source

    if is_question:
        question_milestone = "Waiting for user input"
        if question_milestone not in seen_milestones:
            milestones.append(question_milestone)
    elif is_prompt:
        prompt_milestone = "Ready for input"
        if prompt_milestone not in seen_milestones:
            milestones.append(prompt_milestone)

    return ProgressSnapshot(
        events=events,
        background_count=background_count,
        primary_label=primary_label,
        primary_confidence=primary_confidence,
        primary_label_source=primary_label_source,
        milestones=milestones,
        is_question=is_question,
        is_prompt=is_prompt,
    )
