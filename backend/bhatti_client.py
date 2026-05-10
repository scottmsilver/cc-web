"""Thin synchronous Python REST client for the bhatti microVM platform.

Wraps the bhatti HTTP API (default http://localhost:18080) with a small,
typed surface for the operations we actually use: VM lifecycle, exec,
file IO, and image save. Uses one persistent httpx.Client per BhattiClient
with a Bearer auth header. No async, no streaming exec (single-shot only).
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

import httpx


def _encode_files_for_bhatti(files: list[dict]) -> list[dict]:
    """Return a new list of file dicts with content base64-encoded.

    bhatti's createFileReq expects {guest_path, content (base64), mode?}.
    Accepts content as bytes or str; str is utf-8 encoded first. Files with
    no content (None or missing) are skipped — bhatti requires content.
    Does not mutate the caller's dicts.
    """
    out: list[dict] = []
    for f in files:
        content = f.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        elif isinstance(content, (bytes, bytearray)):
            content_bytes = bytes(content)
        else:
            raise TypeError(f"file content must be bytes or str, got {type(content).__name__}")
        encoded = base64.b64encode(content_bytes).decode("ascii")
        new_f: dict[str, Any] = {
            "guest_path": f["guest_path"],
            "content": encoded,
        }
        if "mode" in f and f["mode"] is not None:
            new_f["mode"] = f["mode"]
        out.append(new_f)
    return out


try:
    import yaml as _yaml  # type: ignore
except ImportError:  # pragma: no cover - PyYAML is a project dep elsewhere
    _yaml = None


class BhattiError(Exception):
    """Base class for all bhatti client errors."""

    def __init__(self, status: int, body: str, message: str | None = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message or f"bhatti HTTP {status}: {body[:200]}")


class BhattiNotFound(BhattiError):
    """Raised on HTTP 404."""


class BhattiConflict(BhattiError):
    """Raised on HTTP 409."""


def _load_config(path: str) -> tuple[str | None, str | None]:
    """Parse api_url and auth_token from a bhatti YAML config file."""
    p = Path(os.path.expanduser(path))
    if not p.is_file():
        return None, None
    text = p.read_text()
    if _yaml is not None:
        try:
            data = _yaml.safe_load(text) or {}
            return data.get("api_url"), data.get("auth_token")
        except Exception:
            pass
    # Regex fallback for simple key: value YAML
    api_url = _scan(text, r"^\s*api_url\s*:\s*(\S+)\s*$")
    auth_token = _scan(text, r"^\s*auth_token\s*:\s*(\S+)\s*$")
    return api_url, auth_token


def _scan(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1) if m else None


def _raise_for_status(resp: httpx.Response) -> None:
    """Map non-2xx responses to BhattiError subclasses."""
    if 200 <= resp.status_code < 300:
        return
    body = resp.text
    if resp.status_code == 404:
        raise BhattiNotFound(404, body)
    if resp.status_code == 409:
        raise BhattiConflict(409, body)
    raise BhattiError(resp.status_code, body)


class BhattiClient:
    """Synchronous client for the bhatti microVM REST API."""

    def __init__(
        self,
        api_url: str | None = None,
        auth_token: str | None = None,
        config_path: str = "~/.bhatti/config.yaml",
        timeout: float = 30.0,
    ) -> None:
        """Build a client. Reads api_url/auth_token from config_path if unset."""
        cfg_url, cfg_token = (None, None)
        if api_url is None or auth_token is None:
            cfg_url, cfg_token = _load_config(config_path)
        self.api_url = (api_url or cfg_url or "http://localhost:18080").rstrip("/")
        self.auth_token = auth_token or cfg_token
        if not self.auth_token:
            raise BhattiError(0, "", "no auth_token found (pass auth_token= or set ~/.bhatti/config.yaml)")
        self._http = httpx.Client(
            base_url=self.api_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {self.auth_token}"},
        )

    # ---- lifecycle / context ---------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._http.close()

    def __enter__(self) -> "BhattiClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- low-level helpers -----------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: Any = None,
        content: bytes | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        resp = self._http.request(method, path, params=params, json=json, content=content, headers=headers)
        return resp

    # ---- health ----------------------------------------------------------

    def health(self) -> dict:
        """GET /health (auth not required, but harmless to send)."""
        # Health is unauthenticated; use a bare client to avoid sending a stale token.
        resp = httpx.get(f"{self.api_url}/health", timeout=self._http.timeout)
        _raise_for_status(resp)
        return resp.json()

    # ---- VMs -------------------------------------------------------------

    def list_vms(self) -> list[dict]:
        """GET /sandboxes — return all visible VMs."""
        resp = self._request("GET", "/sandboxes")
        _raise_for_status(resp)
        data = resp.json()
        return data if isinstance(data, list) else []

    def get_vm(self, name: str) -> dict:
        """GET /sandboxes/{name}; raises BhattiNotFound on 404."""
        resp = self._request("GET", f"/sandboxes/{name}")
        _raise_for_status(resp)
        return resp.json()

    def create_vm(
        self,
        name: str,
        image: str = "minimal",
        cpus: float = 2,
        memory_mb: int = 2048,
        disk_size_mb: int = 4096,
        env: dict | None = None,
        files: list[dict] | None = None,
        init: str | None = None,
        keep_hot: bool = False,
        init_script: str | None = None,
    ) -> dict:
        """POST /sandboxes; idempotent — returns existing VM on 409.

        Files in `files` should each be {guest_path, content, mode?} where
        `content` is bytes or str — this client base64-encodes it on the wire
        (bhatti's createFileReq decodes via base64.RawStdEncoding/StdEncoding).

        `init` is a shell script the guest runs at boot. The deprecated
        `init_script` kwarg is accepted as an alias for backwards compatibility
        and forwarded as `init`.
        """
        if init_script is not None and init is None:
            init = init_script
        body: dict[str, Any] = {
            "name": name,
            "image": image,
            "cpus": cpus,
            "memory_mb": memory_mb,
            "disk_size_mb": disk_size_mb,
            "keep_hot": keep_hot,
        }
        if env is not None:
            body["env"] = env
        if files is not None:
            body["files"] = _encode_files_for_bhatti(files)
        if init is not None:
            body["init"] = init
        resp = self._request("POST", "/sandboxes", json=body)
        if resp.status_code == 409:
            return self.get_vm(name)
        _raise_for_status(resp)
        return resp.json()

    def destroy_vm(self, name: str, force: bool = True) -> None:
        """DELETE /sandboxes/{name}; tolerates 404."""
        resp = self._request("DELETE", f"/sandboxes/{name}", params={"force": "true" if force else "false"})
        if resp.status_code == 404:
            return
        _raise_for_status(resp)

    def start_vm(self, name: str) -> None:
        """POST /sandboxes/{name}/start."""
        resp = self._request("POST", f"/sandboxes/{name}/start")
        _raise_for_status(resp)

    def stop_vm(self, name: str) -> None:
        """POST /sandboxes/{name}/stop."""
        resp = self._request("POST", f"/sandboxes/{name}/stop")
        _raise_for_status(resp)

    # ---- exec ------------------------------------------------------------

    def exec(
        self,
        name: str,
        argv: list[str] | str,
        timeout_sec: int = 300,
        env: dict | None = None,
    ) -> dict:
        """POST /sandboxes/{name}/exec; returns {exit_code, stdout, stderr}.

        If argv is a string it is wrapped as ["/bin/bash","-c", argv].
        Note: bhatti's request field is named "cmd" (not "argv").
        """
        if isinstance(argv, str):
            cmd = ["/bin/bash", "-c", argv]
        else:
            cmd = list(argv)
        body: dict[str, Any] = {"cmd": cmd, "timeout_sec": timeout_sec}
        if env is not None:
            body["env"] = env
        resp = self._http.post(
            f"/sandboxes/{name}/exec", json=body, timeout=max(self._http.timeout.read or 30.0, timeout_sec + 10)
        )
        _raise_for_status(resp)
        data = resp.json()
        # Normalise — server returns these keys directly.
        return {
            "exit_code": data.get("exit_code", data.get("exitCode", 0)),
            "stdout": data.get("stdout", ""),
            "stderr": data.get("stderr", ""),
        }

    # ---- files -----------------------------------------------------------

    def read_file(self, name: str, path: str) -> bytes:
        """GET /sandboxes/{name}/files?path=...; raises BhattiNotFound on missing."""
        # The server returns 500 for missing files on GET, so HEAD first to
        # distinguish "missing" from real failures.
        head = self._request("HEAD", f"/sandboxes/{name}/files", params={"path": path})
        if head.status_code == 404:
            raise BhattiNotFound(404, f"file not found: {path}")
        if head.status_code >= 400 and head.status_code != 405:
            _raise_for_status(head)
        resp = self._request("GET", f"/sandboxes/{name}/files", params={"path": path})
        if resp.status_code == 404:
            raise BhattiNotFound(404, f"file not found: {path}")
        _raise_for_status(resp)
        return resp.content

    def write_file(
        self,
        name: str,
        path: str,
        content: bytes | str,
        mode: str | None = None,
    ) -> None:
        """PUT /sandboxes/{name}/files?path=...; optionally chmod via exec."""
        data = content.encode() if isinstance(content, str) else content
        resp = self._request(
            "PUT",
            f"/sandboxes/{name}/files",
            params={"path": path},
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        _raise_for_status(resp)
        if mode:
            # Best-effort chmod; raises BhattiError if exec fails.
            result = self.exec(name, ["/bin/chmod", mode, path])
            if result["exit_code"] != 0:
                raise BhattiError(0, result.get("stderr", ""), f"chmod {mode} {path} failed: {result.get('stderr','')}")

    def file_exists(self, name: str, path: str) -> bool:
        """HEAD /sandboxes/{name}/files?path=... — True on 2xx, False on 404."""
        resp = self._request("HEAD", f"/sandboxes/{name}/files", params={"path": path})
        if resp.status_code == 404:
            return False
        if 200 <= resp.status_code < 300:
            return True
        _raise_for_status(resp)
        return False  # unreachable

    def stat_file(self, name: str, path: str) -> dict | None:
        """HEAD a file and parse X-File-* headers; None if missing."""
        resp = self._request("HEAD", f"/sandboxes/{name}/files", params={"path": path})
        if resp.status_code == 404:
            return None
        _raise_for_status(resp)
        h = resp.headers
        size_raw = h.get("X-File-Size") or h.get("x-file-size")
        is_dir_raw = (h.get("X-File-IsDir") or h.get("x-file-isdir") or "").lower()
        return {
            "size": int(size_raw) if size_raw and size_raw.isdigit() else None,
            "mode": h.get("X-File-Mode") or h.get("x-file-mode"),
            "is_dir": is_dir_raw in ("true", "1", "yes"),
        }

    def ls(self, name: str, path: str) -> list[dict]:
        """GET /sandboxes/{name}/files?path=...&ls=true."""
        resp = self._request("GET", f"/sandboxes/{name}/files", params={"path": path, "ls": "true"})
        _raise_for_status(resp)
        data = resp.json()
        return data if isinstance(data, list) else []

    # ---- images ----------------------------------------------------------

    def list_images(self) -> list[dict]:
        """GET /images."""
        resp = self._request("GET", "/images")
        _raise_for_status(resp)
        data = resp.json()
        return data if isinstance(data, list) else []

    def save_image(self, vm_name: str, image_name: str) -> dict:
        """POST /sandboxes/{vm_name}/save-image with JSON body {name: image_name}."""
        resp = self._request("POST", f"/sandboxes/{vm_name}/save-image", json={"name": image_name})
        _raise_for_status(resp)
        return resp.json()
