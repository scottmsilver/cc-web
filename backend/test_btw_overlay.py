"""
Test btw overlay parsing: spinner detection, answer extraction, footer matching.

The /btw command creates an overlay in tmux that goes through phases:
1. Spinner: "· Answering..." with "to dismiss" footer (should be skipped)
2. Answer: actual response text with "↑/↓ to scroll · ... to dismiss" footer

The bug was that phase 1 matched the footer check and captured "· Answering..."
as the answer. The fix skips captures containing "Answering" in the pane text.
"""

from cchost import _has_overlay_footer, _is_overlay_footer_line

# ── Simulated pane captures at different phases ──

SPINNER_PANE = """
❯ /btw What is 2+2?

  /btw What is 2+2?

    · Answering…

  Space, Enter, or Escape to dismiss

""".strip()

SPINNER_PANE_ALT = """
❯ /btw Summarize this session

  /btw Summarize this session

    ✢ Answering…

  Space, Enter, or Escape to dismiss

""".strip()

ANSWER_PANE = """
❯ /btw What is 2+2?

  /btw What is 2+2?

    4

  ↑/↓ to scroll · Space, Enter, or Escape to dismiss

""".strip()

ANSWER_MULTILINE_PANE = """
❯ /btw Summarize this session

  /btw Summarize this session

    This session analyzed a construction draw request.
    21 line items were verified against supporting documents.

  ↑/↓ to scroll · Space, Enter, or Escape to dismiss

""".strip()

JSON_ANSWER_PANE = """
❯ /btw Respond ONLY with JSON, no markd

  /btw Respond ONLY with JSON, no markdown: {"title": "<3-6 word session title>", "status": "<current activity under 10 words>"}

    {"title": "Silver Remodel Draw Audit", "status": "Completed invoice analysis"}

  ↑/↓ to scroll · Space, Enter, or Escape to dismiss

""".strip()

NO_OVERLAY_PANE = """
❯ echo hello
hello
❯
""".strip()


# ── Tests: overlay footer detection ──


def test_footer_detected_during_spinner():
    """Footer is present during spinner phase."""
    assert _has_overlay_footer(SPINNER_PANE)


def test_footer_detected_during_answer():
    """Footer is present during answer phase."""
    assert _has_overlay_footer(ANSWER_PANE)


def test_no_footer_on_normal_pane():
    """No footer on a normal command prompt."""
    assert not _has_overlay_footer(NO_OVERLAY_PANE)


# ── Tests: spinner detection (the fix) ──


def test_spinner_detected_in_spinner_pane():
    """'Answering' is present during spinner phase."""
    assert "Answering" in SPINNER_PANE


def test_spinner_detected_alt_icon():
    """'Answering' detected with alternate spinner icon (✢)."""
    assert "Answering" in SPINNER_PANE_ALT


def test_spinner_not_in_answer_pane():
    """'Answering' is NOT present after the real answer renders."""
    assert "Answering" not in ANSWER_PANE


def test_spinner_not_in_multiline_answer():
    """'Answering' is NOT present in multiline answer."""
    assert "Answering" not in ANSWER_MULTILINE_PANE


def test_spinner_not_in_json_answer():
    """'Answering' is NOT present in JSON answer."""
    assert "Answering" not in JSON_ANSWER_PANE


# ── Tests: the combined skip logic ──


def _should_skip(pane_text: str) -> bool:
    """Reproduces the btw() skip logic: skip if Answering + footer present."""
    return "Answering" in pane_text and _has_overlay_footer(pane_text)


def _should_capture(pane_text: str) -> bool:
    """Reproduces the btw() capture logic: capture if footer present and not skipped."""
    if _should_skip(pane_text):
        return False
    return _has_overlay_footer(pane_text)


def test_skip_spinner():
    """Spinner phase should be skipped."""
    assert _should_skip(SPINNER_PANE)
    assert _should_skip(SPINNER_PANE_ALT)
    assert not _should_capture(SPINNER_PANE)


def test_capture_answer():
    """Answer phase should be captured."""
    assert not _should_skip(ANSWER_PANE)
    assert _should_capture(ANSWER_PANE)


def test_capture_multiline_answer():
    """Multiline answer should be captured."""
    assert _should_capture(ANSWER_MULTILINE_PANE)


def test_capture_json_answer():
    """JSON answer should be captured."""
    assert _should_capture(JSON_ANSWER_PANE)


def test_no_capture_normal_pane():
    """Normal pane (no overlay) should not be captured."""
    assert not _should_capture(NO_OVERLAY_PANE)


# ── Tests: answer extraction ──


def _extract_answer(pane_text: str, question: str) -> str:
    """Reproduces the btw() answer extraction logic."""
    lines = pane_text.split("\n")
    q_prefix = question[:30]
    last_q_idx = -1
    for i, line in enumerate(lines):
        if q_prefix in line and "/btw" in line:
            last_q_idx = i
    if last_q_idx < 0:
        return ""
    response_lines = []
    for line in lines[last_q_idx + 1 :]:
        stripped = line.strip()
        if _is_overlay_footer_line(stripped):
            break
        if stripped:
            response_lines.append(stripped)
    return "\n".join(response_lines)


def test_extract_simple_answer():
    assert _extract_answer(ANSWER_PANE, "What is 2+2?") == "4"


def test_extract_multiline_answer():
    answer = _extract_answer(ANSWER_MULTILINE_PANE, "Summarize this session")
    assert "construction draw request" in answer
    assert "21 line items" in answer


def test_extract_json_answer():
    answer = _extract_answer(
        JSON_ANSWER_PANE, 'Respond ONLY with JSON, no markdown: {"title": "<3-6 word session title>"}'
    )
    assert '"Silver Remodel Draw Audit"' in answer


def test_extract_from_spinner_returns_spinner_text():
    """Extracting from spinner pane would return 'Answering' -- this is why we skip it."""
    answer = _extract_answer(SPINNER_PANE, "What is 2+2?")
    assert "Answering" in answer


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
