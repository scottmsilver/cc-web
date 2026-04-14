"""
Unit tests for TopicManager — persistent project workspaces.

Tests filesystem operations (create, list, delete, slugify) without
starting actual Claude Code sessions.
"""

import json
import os
import shutil
import tempfile

import pytest

# Patch TOPICS_DIR before importing TopicManager
_TEST_TOPICS_DIR = tempfile.mkdtemp(prefix="test-topics-")

import cchost

cchost.TOPICS_DIR = _TEST_TOPICS_DIR

from cchost import CCHost, TopicManager


@pytest.fixture(autouse=True)
def clean_topics_dir():
    """Clean the test topics directory before each test."""
    if os.path.exists(_TEST_TOPICS_DIR):
        shutil.rmtree(_TEST_TOPICS_DIR)
    os.makedirs(_TEST_TOPICS_DIR, exist_ok=True)
    yield
    if os.path.exists(_TEST_TOPICS_DIR):
        shutil.rmtree(_TEST_TOPICS_DIR)
    os.makedirs(_TEST_TOPICS_DIR, exist_ok=True)


@pytest.fixture
def manager():
    host = CCHost(manifest_path=os.path.join(tempfile.mkdtemp(), "test-manifest.json"))
    return TopicManager(host)


# ── create_topic ──


def test_create_topic_happy_path(manager):
    result = manager.create_topic("Silver Remodel")
    assert result["name"] == "Silver Remodel"
    assert result["slug"] == "silver-remodel"
    assert result["conversations"] == []
    assert os.path.isdir(os.path.join(_TEST_TOPICS_DIR, "silver-remodel"))
    # .topic.json exists and is valid
    with open(os.path.join(_TEST_TOPICS_DIR, "silver-remodel", ".topic.json")) as f:
        meta = json.load(f)
    assert meta["name"] == "Silver Remodel"


def test_create_topic_slug_collision(manager):
    manager.create_topic("My Project")
    result = manager.create_topic("My Project")
    assert result["slug"] == "my-project-2"
    assert os.path.isdir(os.path.join(_TEST_TOPICS_DIR, "my-project-2"))


def test_create_topic_triple_collision(manager):
    manager.create_topic("test")
    manager.create_topic("test")
    result = manager.create_topic("test")
    assert result["slug"] == "test-3"


def test_create_topic_special_chars(manager):
    result = manager.create_topic("Hello World! @#$%")
    assert result["slug"] == "hello-world"


def test_create_topic_empty_name(manager):
    result = manager.create_topic("")
    assert result["slug"] == "topic"


# ── list_topics ──


def test_list_topics_empty(manager):
    assert manager.list_topics() == []


def test_list_topics_one(manager):
    manager.create_topic("Alpha")
    topics = manager.list_topics()
    assert len(topics) == 1
    assert topics[0]["name"] == "Alpha"


def test_list_topics_multiple_sorted(manager):
    manager.create_topic("Bravo")
    manager.create_topic("Alpha")
    topics = manager.list_topics()
    assert [t["slug"] for t in topics] == ["alpha", "bravo"]


def test_list_topics_skips_corrupt(manager):
    manager.create_topic("Good")
    # Create a corrupt topic
    bad_dir = os.path.join(_TEST_TOPICS_DIR, "bad-topic")
    os.makedirs(bad_dir)
    with open(os.path.join(bad_dir, ".topic.json"), "w") as f:
        f.write("not json{{{")
    topics = manager.list_topics()
    assert len(topics) == 1
    assert topics[0]["slug"] == "good"


def test_list_topics_skips_non_dirs(manager):
    manager.create_topic("Good")
    # Create a file (not directory) in topics dir
    with open(os.path.join(_TEST_TOPICS_DIR, "not-a-dir"), "w") as f:
        f.write("nope")
    topics = manager.list_topics()
    assert len(topics) == 1


# ── get_topic ──


def test_get_topic_found(manager):
    manager.create_topic("Found Me")
    topic = manager.get_topic("found-me")
    assert topic["name"] == "Found Me"


def test_get_topic_not_found(manager):
    with pytest.raises(KeyError):
        manager.get_topic("nonexistent")


def test_get_topic_corrupt(manager):
    bad_dir = os.path.join(_TEST_TOPICS_DIR, "corrupt")
    os.makedirs(bad_dir)
    with open(os.path.join(bad_dir, ".topic.json"), "w") as f:
        f.write("{invalid")
    with pytest.raises(KeyError, match="corrupt metadata"):
        manager.get_topic("corrupt")


# ── path traversal ──


def test_get_topic_path_traversal(manager):
    with pytest.raises(KeyError, match="Invalid"):
        manager.get_topic("..")


def test_get_topic_path_traversal_nested(manager):
    with pytest.raises(KeyError, match="Invalid"):
        manager.get_topic("../../etc")


def test_get_topic_path_traversal_slash(manager):
    with pytest.raises(KeyError, match="Invalid"):
        manager.get_topic("foo/bar")


def test_delete_topic_path_traversal(manager):
    with pytest.raises(KeyError, match="Invalid"):
        manager.delete_topic("..")


# ── delete_topic ──


def test_delete_topic_happy(manager):
    manager.create_topic("Delete Me")
    assert os.path.isdir(os.path.join(_TEST_TOPICS_DIR, "delete-me"))
    manager.delete_topic("delete-me")
    assert not os.path.isdir(os.path.join(_TEST_TOPICS_DIR, "delete-me"))


def test_delete_topic_not_found(manager):
    with pytest.raises(KeyError):
        manager.delete_topic("nonexistent")


def test_delete_topic_preserves_others(manager):
    manager.create_topic("Keep")
    manager.create_topic("Remove")
    manager.delete_topic("remove")
    assert os.path.isdir(os.path.join(_TEST_TOPICS_DIR, "keep"))
    assert not os.path.isdir(os.path.join(_TEST_TOPICS_DIR, "remove"))


# ── _slugify ──


def test_slugify_basic(manager):
    # Note: _slugify checks for existing dirs, so test with clean state
    assert manager._slugify("Hello World") == "hello-world"


def test_slugify_special_chars(manager):
    assert manager._slugify("Test @#$ Project!") == "test-project"


def test_slugify_unicode(manager):
    slug = manager._slugify("café résumé")
    assert slug == "caf-r-sum"


def test_slugify_long_name(manager):
    long_name = "a" * 100
    slug = manager._slugify(long_name)
    assert len(slug) <= 64


def test_slugify_empty(manager):
    assert manager._slugify("") == "topic"


def test_slugify_only_special_chars(manager):
    assert manager._slugify("@#$%^&") == "topic"


# ── metadata persistence ──


def test_metadata_roundtrip(manager):
    manager.create_topic("Roundtrip")
    topic_dir = os.path.join(_TEST_TOPICS_DIR, "roundtrip")
    # Add a conversation manually
    meta = manager._read_metadata(topic_dir)
    meta["conversations"].append(
        {
            "id": "conv-test",
            "session_id": "test-session",
            "started_at": "2026-04-13T00:00:00Z",
            "title": "Test",
            "status": "completed",
        }
    )
    manager._write_metadata(topic_dir, meta)
    # Re-read
    meta2 = manager._read_metadata(topic_dir)
    assert len(meta2["conversations"]) == 1
    assert meta2["conversations"][0]["id"] == "conv-test"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
