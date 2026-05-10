"""Live integration tests for bhatti_client.BhattiClient.

These tests run against the local bhatti server on http://localhost:18080.
They are skipped only when bhatti is unreachable; if it's running, they
execute (the user explicitly dislikes skip-by-default for live tests).
"""

from __future__ import annotations

import socket
import time
import uuid

import pytest

from bhatti_client import BhattiClient, BhattiError, BhattiNotFound

# Keep the `time` import live for the formatter even before it sees the use site below.
_KEEP_TIME = time.monotonic


def _bhatti_reachable() -> bool:
    """Return True if a TCP connect to localhost:18080 succeeds quickly."""
    try:
        with socket.create_connection(("127.0.0.1", 18080), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _bhatti_reachable(), reason="bhatti not running on :18080")


# ---- fixtures ------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> BhattiClient:
    c = BhattiClient()
    yield c
    c.close()


@pytest.fixture(scope="module")
def vm(client: BhattiClient) -> str:
    """Create a single small VM shared across tests; destroy on teardown."""
    name = f"client-test-{uuid.uuid4().hex[:8]}"
    client.create_vm(
        name,
        image="minimal",
        cpus=1,
        memory_mb=512,
        disk_size_mb=1024,
    )
    yield name
    try:
        client.destroy_vm(name)
    except BhattiError:
        pass


# ---- tests ---------------------------------------------------------------


def test_health(client: BhattiClient):
    h = client.health()
    assert isinstance(h, dict)
    assert h.get("status") == "ok"


def test_create_is_idempotent_and_get_vm(client: BhattiClient, vm: str):
    info = client.get_vm(vm)
    assert info["name"] == vm
    # Calling create again with the same name should return the existing VM,
    # not raise — bhatti currently returns 200 on duplicate; client also
    # handles 409 by GETting.
    again = client.create_vm(vm, image="minimal", cpus=1, memory_mb=512, disk_size_mb=1024)
    assert again["name"] == vm


def test_list_vms_includes_our_vm(client: BhattiClient, vm: str):
    names = [v["name"] for v in client.list_vms()]
    assert vm in names


def test_exec_captures_stdout_stderr_exit_code(client: BhattiClient, vm: str):
    result = client.exec(vm, ["/bin/sh", "-c", "echo out; echo err >&2; exit 3"])
    assert result["exit_code"] == 3
    assert "out" in result["stdout"]
    assert "err" in result["stderr"]


def test_exec_string_form_uses_bash(client: BhattiClient, vm: str):
    # String form is wrapped as ["/bin/bash","-c", argv].
    result = client.exec(vm, "echo $((2+2))")
    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "4"


def test_write_then_read_roundtrip(client: BhattiClient, vm: str):
    payload = b"hello bhatti " + uuid.uuid4().bytes
    path = "/tmp/client_test_roundtrip.bin"
    client.write_file(vm, path, payload)
    assert client.read_file(vm, path) == payload


def test_write_string_then_read(client: BhattiClient, vm: str):
    path = "/tmp/client_test_string.txt"
    client.write_file(vm, path, "hello, world\n")
    assert client.read_file(vm, path) == b"hello, world\n"


def test_file_exists_true_and_false(client: BhattiClient, vm: str):
    path = "/tmp/client_test_exists.txt"
    client.write_file(vm, path, "x")
    assert client.file_exists(vm, path) is True
    assert client.file_exists(vm, "/no/such/path/here") is False


def test_stat_file(client: BhattiClient, vm: str):
    path = "/tmp/client_test_stat.bin"
    client.write_file(vm, path, b"abcdef")
    st = client.stat_file(vm, path)
    assert st is not None
    assert st["size"] == 6
    assert st["is_dir"] is False
    assert client.stat_file(vm, "/no/such/file/anywhere") is None


def test_ls_returns_entries(client: BhattiClient, vm: str):
    entries = client.ls(vm, "/etc")
    assert isinstance(entries, list)
    assert len(entries) > 0
    # Each entry should at least have a "name" key.
    assert all("name" in e for e in entries)


def test_read_missing_raises_not_found(client: BhattiClient, vm: str):
    with pytest.raises(BhattiNotFound):
        client.read_file(vm, "/this/file/does/not/exist/zzz")


def test_get_vm_missing_raises_not_found(client: BhattiClient):
    with pytest.raises(BhattiNotFound):
        client.get_vm(f"definitely-not-a-vm-{uuid.uuid4().hex[:6]}")


def test_destroy_missing_vm_is_tolerated(client: BhattiClient):
    # Destroying a VM that doesn't exist must not raise.
    client.destroy_vm(f"definitely-not-a-vm-{uuid.uuid4().hex[:6]}")


def test_list_images(client: BhattiClient):
    images = client.list_images()
    assert isinstance(images, list)
    names = {i.get("name") for i in images}
    # Built-in tier should always be present.
    assert "minimal" in names


def test_save_image_errors_gracefully_when_vm_missing(client: BhattiClient):
    bogus = f"no-such-vm-{uuid.uuid4().hex[:6]}"
    with pytest.raises(BhattiNotFound):
        client.save_image(bogus, "should-not-exist")


def test_create_vm_with_inline_files_and_init(client: BhattiClient):
    """End-to-end check that inline files (base64) and `init` body field both work.

    Verifies the two latent bugs we just fixed in create_vm:
      - body field must be `init` (not `init_script`)
      - file content must be base64-encoded on the wire
    """
    name = f"inline-test-{uuid.uuid4().hex[:8]}"
    files = [
        {
            "guest_path": "/tmp/inline-marker.txt",
            "content": b"hello-from-inline",
            "mode": "0600",
        }
    ]
    init = "echo started > /tmp/init-ran.txt"
    try:
        client.create_vm(
            name=name,
            image="cc-base",
            cpus=2,
            memory_mb=2048,
            disk_size_mb=4096,
            files=files,
            init=init,
        )
        # init runs at boot; give it a moment to finish.
        time.sleep(3)
        assert client.read_file(name, "/tmp/inline-marker.txt") == b"hello-from-inline"
        init_out = client.read_file(name, "/tmp/init-ran.txt")
        assert b"started" in init_out
    finally:
        try:
            client.destroy_vm(name)
        except BhattiError:
            pass
