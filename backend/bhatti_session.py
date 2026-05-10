"""
bhatti_session — claude-code session that runs inside a bhatti microVM.

Mirrors the external surface of ``cchost.CCSession`` so it can drop into the
``CCHost`` dispatch path. The big architectural difference: instead of running
``tmux`` on the host and shelling out via ``libtmux``, we run ``tmux`` *inside*
a VM and route every tmux call through ``BhattiClient.exec``. Claude Code runs
inside the VM as user ``lohar`` (uid 1000, NOPASSWD sudo). The base image
``cc-base`` already has tmux 3.4, ``claude``, and these baked-in paths::

    /opt/cchost/hooks/progress-hook.sh
    /opt/cchost/settings-template.json

The host's Anthropic credentials are passed in as ``files=`` on the
single ``create_vm`` call that bootstraps the session (see ``_start``);
the host's ``settings.json`` is intentionally NOT copied because it
points at host-only hook scripts. Inside the VM we write a hardcoded
``_SETTINGS_JSON`` blob whose hooks point at the cc-base-baked
``/opt/cchost/hooks/progress-hook.sh``.

State classification: we cannot ``psutil.Process(claude_pid)`` against a
process inside the VM, so we always pass ``claude_pid=None`` to
``state_classifier.classify``. The classifier's "no live process" branch maps
to ``dormant`` — but we want a richer answer for a healthy VM, so we
short-circuit: if the VM is running, we synthesise a fake healthy PID by
materialising the in-VM JSONL + events files to a host tempdir and calling
``classify(claude_pid=os.getpid(), ...)``. Using our own PID is a deliberate
hack — we just need *some* live PID for the classifier's CPU check, which will
read ~0% (this Python process is mostly waiting on I/O during a state poll)
and let the JSONL/events signals dominate. Caveat: we lose the ``working``
detection that depends on >5% CPU on the claude PID, but ``end_turn`` /
``tool_use`` from the JSONL still classify correctly, which is what matters
for the UI.

Only the methods actually used by ``CCHost`` dispatch are mirrored:
``__init__`` / ``_ensure_jsonl_path`` / ``send_message`` / ``answer`` /
``current_question`` / ``state`` / ``is_dormant`` / ``working_dir`` /
``session_id`` / ``destroy``. Anything fancier (``btw``, ``slash_command``,
``progress_snapshot``) can be added later.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Optional

# Reuse the Response dataclass (shape consumed by server._serialize_send_response)
# and the progress-snapshot helpers from the tmux backend, so BhattiSession's
# read APIs return identical shapes.

# bhatti_client is provided by another agent; importing here would fail at
# module load time today, so we defer the import to construction. Live tests
# skip cleanly if it's missing.
try:  # pragma: no cover - import-time guard
    from bhatti_client import BhattiClient, BhattiError, BhattiNotFound  # type: ignore
except ImportError:  # pragma: no cover
    BhattiClient = None  # type: ignore[assignment,misc]
    BhattiError = Exception  # type: ignore[assignment,misc]
    BhattiNotFound = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger("cchost.bhatti_session")


class BhattiSessionStale(BhattiError):  # type: ignore[misc,valid-type]
    """Raised when a persisted session's VM no longer exists.

    Surfaced during lazy materialisation of a session that was loaded from
    the manifest but whose backing VM was destroyed out-of-band (e.g. via
    ``bhatti destroy`` on the host). We deliberately do NOT auto-recreate
    the VM, since that would silently produce a fresh empty VM with no
    claude_session_id continuity — better to tell the caller the session
    is gone so they can prune it from the manifest.
    """

    def __init__(self, message: str) -> None:
        # ``BhattiError`` (when bhatti_client is installed) requires
        # ``(status, body)``; satisfy that signature with a synthetic 410
        # ("Gone") and the human message as both body and message. When
        # bhatti_client is missing, ``BhattiError`` is plain ``Exception``
        # and accepts the message directly — handle both cases.
        try:
            super().__init__(410, message, message)  # type: ignore[call-arg]
        except TypeError:
            super().__init__(message)


# Constants -------------------------------------------------------------------

_VM_IMAGE = "cc-base"
_VM_USER = "lohar"
_VM_HOME = f"/home/{_VM_USER}"
_VM_CLAUDE_DIR = f"{_VM_HOME}/.claude"
_VM_TMUX_SESSION = "claude"
_DEFAULT_WORKING_DIR = "/workspace"
_DEFAULT_VM_CPUS = 2
_DEFAULT_VM_MEMORY_MB = 2048
_DEFAULT_VM_DISK_MB = 4096

_READY_TIMEOUT_SEC = 90
_READY_POLL_INTERVAL = 1.5

# Hardcoded settings.json contents written into the VM at create time.
# Six hooks all point at the progress-hook script baked into cc-base.
# ``skipDangerousModePermissionPrompt`` suppresses the bypass-permissions
# interstitial that ``--dangerously-skip-permissions`` would otherwise draw.
_SETTINGS_JSON: bytes = json.dumps(
    {
        "skipDangerousModePermissionPrompt": True,
        "hooks": {
            event: [{"matcher": "", "hooks": [{"type": "command", "command": "/opt/cchost/hooks/progress-hook.sh"}]}]
            for event in (
                "PreToolUse",
                "PostToolUse",
                "Stop",
                "Notification",
                "SubagentStart",
                "SubagentStop",
            )
        },
    },
    indent=2,
).encode()

# State materialisation: we copy the VM's jsonl/events files to host tempdir
# every time .state is read. This is fine for poll cadence (UI polls every
# few seconds) but we cap freshness so back-to-back reads share one fetch.
_STATE_CACHE_TTL = 0.5  # seconds

# If the JSONL or events mirror was touched within this window, treat the
# session as ``working``. Compensates for the missing CPU% signal — see
# ``BhattiSession.state``. Tuned for the materialisation cadence: 0.5s TTL
# means even quiet polls update mtime, so the window has to comfortably
# exceed that. 4s gives a good "claude is generating tokens" signal.
_ACTIVITY_WINDOW_SEC = 4.0


# Helpers ---------------------------------------------------------------------


def _sanitize_vm_name(name: str) -> str:
    """Bhatti VM names: lowercase, alphanumeric + hyphens, ≤32 chars."""
    cleaned = re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")
    cleaned = re.sub(r"-+", "-", cleaned)
    if not cleaned:
        cleaned = "session"
    return cleaned[:32].rstrip("-") or "session"


def _read_host_file(path: str) -> Optional[bytes]:
    """Read a host file as bytes; return None if missing/unreadable."""
    expanded = os.path.expanduser(path)
    if not os.path.isfile(expanded):
        return None
    try:
        with open(expanded, "rb") as f:
            return f.read()
    except OSError:
        return None


# Conservative absolute-path matcher. We embed working_dir into shell
# commands (mkdir/touch/chown/cd) inside the VM, and into a single-quoted
# tmux command argument; any metacharacter or single quote could break out.
# Restricting to [A-Za-z0-9._/-] lets us pass it raw without shell-quoting.
_SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]*$")


def _is_safe_path(path: str) -> bool:
    """True iff ``path`` is an absolute path with no shell metacharacters
    and no ``..`` traversal segments."""
    if not isinstance(path, str) or not path:
        return False
    if not _SAFE_PATH_RE.match(path):
        return False
    # Reject any ".." segment so a regex-passing path can't escape its dir
    # via /workspace/../etc/shadow once a future call site forwards it raw.
    return ".." not in path.split("/")


# BhattiSession ---------------------------------------------------------------


class BhattiSession:
    """A persistent Claude Code session running in a bhatti microVM.

    Public surface mirrors ``cchost.CCSession`` for the methods that
    ``CCHost`` dispatches to:

    - Properties: ``working_dir``, ``session_id``, ``is_dormant``, ``state``,
      ``name``, ``vm_name``.
    - Methods: ``send_message``, ``answer``, ``current_question``,
      ``destroy``, ``_ensure_jsonl_path``.
    """

    def __init__(
        self,
        name: str,
        working_dir: str = _DEFAULT_WORKING_DIR,
        *,
        client: Optional[Any] = None,
        vm_name: Optional[str] = None,
        host_credentials_dir: str = "~/.claude",
        lazy_start: bool = False,
    ) -> None:
        if BhattiClient is None and client is None:
            raise RuntimeError("bhatti_client is not installed; cannot create BhattiSession")

        # Validate working_dir BEFORE any side effects. We embed it in shell
        # commands inside the VM (mkdir, touch, chown, cd), so we restrict
        # to a conservative absolute-path charset to head off shell injection
        # via metacharacters (`;`, `$`, backticks, quotes, newlines).
        if not _is_safe_path(working_dir):
            raise ValueError(f"working_dir must be an absolute path with no shell metacharacters: " f"{working_dir!r}")

        self.name = name
        # CCSession-parity attributes used by CCHost._save_manifest and
        # server.SessionInfo serialization. ``id`` is the cchost-level session
        # id (the dict key in CCHost._sessions); ``backend`` distinguishes us
        # from a tmux session in serialized output; ``owner_email`` is stamped
        # by CCHost.create after construction.
        self.id = name
        self.created_at = datetime.now(timezone.utc)
        self.backend = "bhatti"
        self.owner_email = ""
        self._working_dir = working_dir
        self._client = client if client is not None else BhattiClient()  # type: ignore[misc]
        self._vm_name = _sanitize_vm_name(vm_name or f"cc-{name}")
        # Track whether the caller named a specific VM (rehydrate path) vs
        # asked us to mint one (fresh-create path). Only the rehydrate path
        # treats a missing VM as ``BhattiSessionStale`` — for fresh creates
        # we expect ``_start`` to create the VM from scratch via create_vm.
        self._vm_name_provided = vm_name is not None
        self._host_credentials_dir = host_credentials_dir

        # In-VM paths discovered/cached after start.
        self._jsonl_path: Optional[str] = None  # path *inside* the VM
        self._claude_session_id: Optional[str] = None

        # Host-side tempdir used to mirror the VM's jsonl + events files for
        # state_classifier (which only knows how to read local files).
        self._state_tmpdir = tempfile.mkdtemp(prefix=f"bhatti-{self._vm_name}-")
        self._host_jsonl_mirror = os.path.join(self._state_tmpdir, "transcript.jsonl")
        self._host_events_mirror = os.path.join(self._state_tmpdir, "events.jsonl")
        self._last_state_fetch: float = 0.0

        # Lazy-start gate. When True, ``_start()`` runs the first time a
        # public method that touches the VM is called. ``_started`` flips
        # to True only on a successful materialisation so failed starts
        # don't poison subsequent retries. ``_starting`` is a re-entrance
        # guard for methods called *from inside* ``_start`` (e.g.
        # ``_capture_pane`` during the tmux readiness probe) — without it,
        # those guarded internals would loop back into ``_ensure_started``
        # and recurse infinitely.
        self._started = False
        self._starting = False

        if lazy_start:
            return

        try:
            self._ensure_started()
        except Exception:
            # Clean up tempdir if VM bring-up fails. The VM itself may or may
            # not have been created — destroy_vm is idempotent enough that
            # we attempt it best-effort.
            try:
                import shutil as _shutil

                _shutil.rmtree(self._state_tmpdir, ignore_errors=True)
            except OSError:
                pass
            try:
                self._client.destroy_vm(self._vm_name, force=True)
            except Exception:  # noqa: BLE001
                pass
            raise

    # Lifecycle ---------------------------------------------------------------

    def _start(self) -> None:
        """Single-call VM bring-up using ``create_vm(files=..., init=...)``.

        Replaces the old ~15-exec-call cold-start (mkdir + chowns + 4 PUTs
        + tmux has-session check + tmux new-session + many capture-pane
        ready-polls + jsonl find) with:

        - 1 ``create_vm`` call carrying creds/settings/.claude.json/CLAUDE.md
          as ``files=`` and an ``init`` script that prepares the workspace,
          launches claude in tmux as ``lohar``, and exits.
        - A handful of ``capture-pane`` exec polls (typically 4–8) to detect
          the input box drawing — readiness signal.
        - 0 ``write_file`` calls (PUTs all moved into the create_vm files=).

        Net: cold-start exec-bucket cost ~15 → ~5–8. Worth noting: bhatti
        kills any backgrounded process from init the moment init exits, so
        an in-VM watcher that forks and writes a marker file does NOT
        survive — verified empirically. Hence we keep the readiness probe
        on the Python side. We still skip the trust-folder + bypass-
        permissions overlays via the pre-injected ``.claude.json`` (with
        ``projects[<workdir>].hasTrustDialogAccepted = true``) and
        ``settings.json`` (``skipDangerousModePermissionPrompt: true``),
        so claude reaches the input box without keypresses.

        On rehydrate (caller passed an existing ``vm_name``), ``create_vm``
        returns 409 and the client transparently maps that to ``get_vm`` —
        so an existing VM is re-attached. The ``init`` script does NOT run
        again on a 409 attach, but tmux+claude were already launched on
        the original boot, so the readiness probe still passes.
        """
        # 1. Read host-side state. .credentials.json is required; the rest
        # is best-effort.
        creds_dir = os.path.expanduser(self._host_credentials_dir)
        creds = _read_host_file(os.path.join(creds_dir, ".credentials.json"))
        if creds is None:
            raise RuntimeError(f"Host credentials missing at {creds_dir}/.credentials.json; cannot start session")
        claude_md = _read_host_file(os.path.join(creds_dir, "CLAUDE.md"))

        # 2. Mutate ~/.claude.json to pre-trust the workspace. If the host
        # file is missing, build a minimal stub with just the trust entry.
        host_claude_json = _read_host_file("~/.claude.json")
        if host_claude_json is not None:
            try:
                claude_obj = json.loads(host_claude_json)
                if not isinstance(claude_obj, dict):
                    claude_obj = {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                claude_obj = {}
        else:
            claude_obj = {}
        projects = claude_obj.setdefault("projects", {})
        if not isinstance(projects, dict):
            projects = {}
            claude_obj["projects"] = projects
        existing = projects.get(self._working_dir)
        if not isinstance(existing, dict):
            existing = {}
        existing["hasTrustDialogAccepted"] = True
        projects[self._working_dir] = existing
        claude_json_bytes = json.dumps(claude_obj).encode()

        # 3. Build the files list for create_vm. The bhatti client base64-
        # encodes content for us; we just pass raw bytes.
        files: list[dict] = [
            {"guest_path": f"{_VM_CLAUDE_DIR}/.credentials.json", "content": creds, "mode": "0600"},
            {"guest_path": f"{_VM_HOME}/.claude.json", "content": claude_json_bytes, "mode": "0600"},
            {"guest_path": f"{_VM_CLAUDE_DIR}/settings.json", "content": _SETTINGS_JSON, "mode": "0644"},
        ]
        if claude_md is not None:
            files.append({"guest_path": f"{_VM_CLAUDE_DIR}/CLAUDE.md", "content": claude_md, "mode": "0644"})

        # 4. Build the boot init script. Runs as root at VM boot. Bhatti's
        # supervisor kills any backgrounded process from init when init
        # exits, so we deliberately do NOT fork a watcher — readiness is
        # probed from Python instead.
        wd = self._working_dir
        init_script = f"""set -e
mkdir -p {_VM_CLAUDE_DIR} {wd}
touch {wd}/.cchost-events.jsonl
chown -R {_VM_USER}:{_VM_USER} {_VM_HOME} {wd} 2>/dev/null || true

sudo -u {_VM_USER} tmux new-session -d -s {_VM_TMUX_SESSION} -x 200 -y 50 "cd {wd} && exec claude --dangerously-skip-permissions"
"""

        # 5. Single create_vm call. Idempotent: 409 maps to get_vm.
        logger.info("create_vm %s (single-call cold-start)", self._vm_name)
        self._client.create_vm(
            self._vm_name,
            image=_VM_IMAGE,
            cpus=_DEFAULT_VM_CPUS,
            memory_mb=_DEFAULT_VM_MEMORY_MB,
            disk_size_mb=_DEFAULT_VM_DISK_MB,
            files=files,
            init=init_script,
        )

        # 6. Poll the tmux pane until claude draws its input box. With the
        # trust-folder + bypass-permissions overlays pre-suppressed, claude
        # should reach the prompt without needing keypresses.
        deadline = time.time() + _READY_TIMEOUT_SEC
        ready = False
        while time.time() < deadline:
            try:
                pane = self._capture_pane()
            except (BhattiError, BhattiNotFound) as e:
                logger.debug("capture-pane during ready poll: %s", e)
                time.sleep(_READY_POLL_INTERVAL)
                continue
            # Input box framed with rounded corners means claude is at the
            # prompt. The "Try" placeholder text is a backup signal.
            if ("╭─" in pane and "╰─" in pane) or ">  Try " in pane:
                ready = True
                break
            time.sleep(_READY_POLL_INTERVAL)
        if not ready:
            raise TimeoutError(f"Claude inside VM {self._vm_name} did not become ready within {_READY_TIMEOUT_SEC}s")

        # 7. Discover the JSONL path. Claude doesn't write a transcript
        # until the user sends a message, so projects/<slug>/<id>.jsonl
        # may not exist yet — _ensure_jsonl_path will retry lazily.
        self._discover_jsonl_path()

    def _ensure_started(self) -> None:
        """Lazy materialisation hook for public methods that touch the VM.

        Cheap on the hot path: if ``self._started`` is already True, this is
        a single bool check (no client roundtrip). On first call it verifies
        the VM exists, warm-wakes it if stopped, then runs the existing
        ``_start()`` body. If the persisted ``vm_name`` no longer exists we
        raise ``BhattiSessionStale`` rather than silently auto-recreating
        the VM (which would surface a fresh empty session with no claude
        continuity).
        """
        if self._started or self._starting:
            return
        # Verify the VM exists IFF the caller passed an explicit vm_name —
        # i.e. we're rehydrating a persisted session and the VM is supposed
        # to already exist. On the fresh-create path (no vm_name), missing
        # is the expected state and ``_start`` will create the VM.
        if self._vm_name_provided:
            try:
                info = self._client.get_vm(self._vm_name)
            except BhattiNotFound:
                raise BhattiSessionStale(
                    f"VM {self._vm_name} no longer exists; " "the session has been destroyed out-of-band"
                )
            # Best-effort warm-wake: if the VM is stopped, try to start it.
            # We tolerate failures here — if the warm-wake fails, the
            # subsequent exec calls in ``_start()`` will surface the real
            # error.
            status = (info.get("status") or info.get("state") or "").lower()
            if status and status != "running":
                try:
                    self._client.start_vm(self._vm_name)
                except (BhattiError, BhattiNotFound, AttributeError) as e:
                    logger.debug("warm-wake start_vm(%s) failed: %s", self._vm_name, e)
        # Run the bring-up. ``_start`` is idempotent on rehydrate: the
        # client's create_vm maps 409 to get_vm, so an existing VM is
        # re-attached without re-running init. We set ``_starting`` so
        # guarded internals (``_capture_pane`` etc.) don't re-enter
        # ``_ensure_started`` while ``_start`` runs.
        self._starting = True
        try:
            self._start()
            self._started = True
        finally:
            self._starting = False

    def ensure_started(self) -> None:
        """Public alias for :py:meth:`_ensure_started` — mostly for tests."""
        self._ensure_started()

    def _discover_jsonl_path(self) -> None:
        """Find the JSONL transcript file inside the VM, set self._jsonl_path.

        Claude Code writes to ``~/.claude/projects/<slug>/<session-id>.jsonl``.
        We don't care about the slug — just take the most recently modified
        ``*.jsonl`` under ``~/.claude/projects/``.
        """
        result = self._exec(
            [
                "sudo",
                "-u",
                _VM_USER,
                "bash",
                "-lc",
                (
                    f"find {_VM_CLAUDE_DIR}/projects -type f -name '*.jsonl' "
                    "-printf '%T@ %p\\n' 2>/dev/null | sort -nr | head -1 | "
                    "awk '{print $2}'"
                ),
            ],
            timeout_sec=10,
            check=False,
        )
        path = (result.get("stdout", "") or "").strip()
        if path:
            self._jsonl_path = path
            basename = os.path.basename(path)
            if basename.endswith(".jsonl"):
                self._claude_session_id = basename[:-6]
            logger.info("Discovered JSONL: %s (session_id=%s)", path, self._claude_session_id)
        else:
            logger.warning("Could not discover JSONL inside VM %s yet", self._vm_name)

    # tmux helpers ------------------------------------------------------------

    def _exec(
        self,
        argv: list[str],
        *,
        timeout_sec: int = 30,
        check: bool = True,
    ) -> dict:
        """Wrapper around ``client.exec`` that optionally raises on non-zero exit."""
        result = self._client.exec(self._vm_name, argv, timeout_sec=timeout_sec)
        if check and result.get("exit_code", 0) != 0:
            raise BhattiError(
                f"exec failed in {self._vm_name}: {argv} -> "
                f"exit={result.get('exit_code')} stderr={result.get('stderr','')[:200]}"
            )
        return result

    def _capture_pane(self) -> str:
        """tmux capture-pane -t claude -p (last 50 lines)."""
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return ""
        result = self._exec(
            [
                "sudo",
                "-u",
                _VM_USER,
                "tmux",
                "capture-pane",
                "-t",
                _VM_TMUX_SESSION,
                "-p",
            ],
            timeout_sec=10,
            check=False,
        )
        return result.get("stdout", "") or ""

    def terminal_capture(self, lines: int = 0) -> str:
        """Capture the in-VM tmux pane for the terminal-view tab.

        Mirrors ``CCSession.terminal_capture``: ``lines=0`` returns the
        complete scrollback, ``lines>0`` returns the last N visible rows.
        Routes ``tmux capture-pane`` through ``bhatti exec``.
        """
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return "(session is dormant — VM no longer exists)"
        if self.is_dormant:
            return "(session is dormant — VM not running)"
        argv = [
            "sudo",
            "-u",
            _VM_USER,
            "tmux",
            "capture-pane",
            "-t",
            _VM_TMUX_SESSION,
            "-p",
        ]
        if lines == 0:
            # Full scrollback: -S - = start of history, -E - = end
            argv += ["-S", "-", "-E", "-"]
        else:
            # Last N visible rows.
            argv += ["-S", f"-{int(lines)}"]
        try:
            result = self._exec(argv, timeout_sec=10, check=False)
        except BhattiError:
            return ""
        return result.get("stdout", "") or ""

    def _tmux_send_literal(self, text: str) -> None:
        """``tmux send-keys -l`` (literal) — special chars are passed through."""
        self._exec(
            [
                "sudo",
                "-u",
                _VM_USER,
                "tmux",
                "send-keys",
                "-t",
                _VM_TMUX_SESSION,
                "-l",
                text,
            ],
            timeout_sec=10,
        )

    def _tmux_send_enter(self) -> None:
        """``tmux send-keys -t claude Enter`` — submits the line."""
        self._exec(
            [
                "sudo",
                "-u",
                _VM_USER,
                "tmux",
                "send-keys",
                "-t",
                _VM_TMUX_SESSION,
                "Enter",
            ],
            timeout_sec=10,
        )

    # Public surface (CCSession parity) ---------------------------------------

    @property
    def working_dir(self) -> str:
        return self._working_dir

    @property
    def vm_name(self) -> str:
        return self._vm_name

    @property
    def session_id(self) -> Optional[str]:
        """Claude session id (UUID from the JSONL filename)."""
        return self._claude_session_id

    @property
    def is_dormant(self) -> bool:
        """True iff the VM is missing or not in a 'running' state."""
        try:
            info = self._client.get_vm(self._vm_name)
        except BhattiNotFound:
            return True
        except BhattiError:
            return True
        # Accept whatever shape get_vm returns. Common keys: "status", "state".
        status = (info.get("status") or info.get("state") or "").lower()
        return status != "running"

    def _ensure_jsonl_path(self) -> Optional[str]:
        """Resolve and cache the JSONL path *inside* the VM.

        Re-discovers if claude rotated to a new transcript. Swallows
        BhattiError / BhattiNotFound so callers (e.g. ``state``) don't blow
        up if the VM was destroyed mid-poll.
        """
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return self._jsonl_path
        try:
            if not self._jsonl_path or not self._client.file_exists(self._vm_name, self._jsonl_path):
                self._discover_jsonl_path()
        except (BhattiError, BhattiNotFound):
            return self._jsonl_path
        return self._jsonl_path

    def send_message(self, text: str) -> None:
        """Type ``text`` into the claude prompt and press Enter."""
        if not text:
            return
        # Re-raise BhattiSessionStale so the caller sees a clear "VM gone"
        # error instead of a silent no-op or freshly-recreated empty VM.
        self._ensure_started()
        # Literal mode is required so e.g. semicolons / dollar signs aren't
        # treated as tmux key names.
        self._tmux_send_literal(text)
        self._tmux_send_enter()

    def answer(self, option_index: int) -> None:
        """Answer an AskUserQuestion by typing the option digit + Enter.

        ``option_index`` is 1-based, matching ``CCSession.answer``.
        """
        if option_index < 1:
            raise ValueError("option_index must be >= 1")
        self._ensure_started()
        self._tmux_send_literal(str(option_index))
        self._tmux_send_enter()

    def current_question(self) -> Optional[dict]:
        """Return the most recent unanswered AskUserQuestion, or None.

        Logic mirrors ``CCSession._unanswered_question_from_events``: scan the
        events JSONL for AskUserQuestion PreToolUse with no later PostToolUse.
        """
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return None
        events = self._fetch_events_jsonl()
        if not events:
            return None
        last_ask: Optional[dict] = None
        answered: set[str] = set()
        for line in events.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            tool = evt.get("tool_name", "")
            event = evt.get("_cchost_event", "")
            if tool == "AskUserQuestion" and event == "PreToolUse":
                tool_input = evt.get("tool_input", {})
                questions = tool_input.get("questions", [])
                if questions:
                    last_ask = {
                        "tool_use_id": evt.get("tool_use_id", ""),
                        "questions": questions,
                    }
            elif tool == "AskUserQuestion" and event == "PostToolUse":
                tid = evt.get("tool_use_id", "")
                if tid:
                    answered.add(tid)
        if not last_ask or last_ask.get("tool_use_id") in answered:
            return None
        first_q = last_ask["questions"][0]
        return self._build_question_result(
            first_q.get("question", ""),
            first_q.get("options", []),
        )

    @staticmethod
    def _build_question_result(question_text: str, raw_options: list) -> dict:
        """Identical to ``CCSession._build_question_result`` (filters chat-only opts)."""
        filtered: list[dict] = []
        for opt in raw_options:
            label = opt.get("label", "") if isinstance(opt, dict) else ""
            if label.lower() in ("type something.", "chat about this"):
                continue
            filtered.append(
                {
                    "label": label,
                    "index": len(filtered) + 1,
                    "description": opt.get("description", "") if isinstance(opt, dict) else "",
                }
            )
        return {"question": question_text, "options": filtered}

    @property
    def state(self) -> str:
        """Classify state via state_classifier, materialising VM files locally.

        We can't psutil into the VM, so the classifier's CPU heuristic is
        useless here. We compensate with two adjustments:

        1. Pass our own PID so ``psutil.pid_exists`` returns True and the
           classifier doesn't short-circuit to ``dormant``.
        2. Override the "fresh session, no stop reason" branch by checking
           whether the in-VM events or JSONL files have been modified in the
           last ``_ACTIVITY_WINDOW_SEC`` seconds. If yes, return ``working``
           directly — claude is actively producing tool events / tokens.

        We rate-limit the file materialisation to ``_STATE_CACHE_TTL`` so
        rapid polls don't pummel ``client.read_file``.
        """
        if self.is_dormant:
            return "dormant"

        # Lazy sessions report dormant until materialised: the classifier
        # needs the host-mirror JSONL/events files, which only get written
        # after ``_start`` runs. Don't trigger materialisation here — the
        # state-poll path is hot, and a rehydrated stale-VM session would
        # raise ``BhattiSessionStale`` repeatedly. The UI will see the
        # session as dormant and the next send_message will surface the
        # stale-VM error to the user instead.
        if not self._started:
            return "dormant"

        from state_classifier import classify  # local import: avoids cycle in some contexts

        now = time.time()
        if now - self._last_state_fetch > _STATE_CACHE_TTL:
            self._materialise_state_files()
            self._last_state_fetch = now

        # Activity override: if any state file was just touched, claude is
        # working. This compensates for not having a real CPU% signal.
        for path in (self._host_events_mirror, self._host_jsonl_mirror):
            if os.path.exists(path):
                age = now - os.path.getmtime(path)
                if age < _ACTIVITY_WINDOW_SEC:
                    # Don't short-circuit on a question/permission state
                    # though — let the classifier surface those. Quick check:
                    # if the events tail has an unanswered AskUserQuestion,
                    # don't override.
                    if self.current_question() is None:
                        return "working"
                    break

        return classify(
            claude_pid=os.getpid(),
            jsonl_path=self._host_jsonl_mirror if os.path.exists(self._host_jsonl_mirror) else None,
            events_path=self._host_events_mirror if os.path.exists(self._host_events_mirror) else None,
        )

    def _materialise_state_files(self) -> None:
        """Copy the in-VM JSONL + events files to host tempdir for the classifier.

        IMPORTANT: only rewrite the local mirror when content actually changed.
        Otherwise mtime would tick on every poll and our activity-window check
        would always think claude was working.
        """
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return
        # Events first — small file, always exists once the workspace is prepared.
        events_data = self._fetch_events_jsonl()
        if events_data is not None:
            self._write_if_changed(self._host_events_mirror, events_data)

        # JSONL — may not exist yet if the very first ready-poll hasn't found it.
        path = self._ensure_jsonl_path()
        if path:
            try:
                data = self._client.read_file(self._vm_name, path)
                if isinstance(data, bytes):
                    payload = data.decode("utf-8", errors="replace")
                else:
                    payload = str(data)
                self._write_if_changed(self._host_jsonl_mirror, payload)
            except BhattiError as e:
                logger.debug("Failed to fetch JSONL mirror: %s", e)

    @staticmethod
    def _write_if_changed(path: str, content: str) -> None:
        """Write ``content`` to ``path`` only if it differs from current contents.

        Preserves mtime when nothing changed, which is essential for the
        activity-window check in ``state``.
        """
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    existing = f.read()
                if existing == content:
                    return
            with open(path, "w") as f:
                f.write(content)
        except OSError:
            logger.debug("Mirror write failed for %s", path, exc_info=True)

    def _fetch_events_jsonl(self) -> Optional[str]:
        """Return the contents of /workspace/.cchost-events.jsonl as text."""
        events_path = f"{self._working_dir}/.cchost-events.jsonl"
        try:
            data = self._client.read_file(self._vm_name, events_path)
        except BhattiError:
            return None
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        if data is None:
            return None
        return str(data)

    # CCSession-parity read APIs ---------------------------------------------
    #
    # These methods are called by server.py per-session endpoints. They mirror
    # the tmux ``CCSession`` shapes exactly (the JSONL/event-parsing logic is
    # parallel to ``cchost.CCSession`` rather than calling its bound methods,
    # since CCSession depends on tmux/libtmux state we don't have here).

    @staticmethod
    def _parse_jsonl_text(text: str) -> list[dict]:
        """Parse a JSONL blob into a list of dicts; bad lines silently skipped."""
        out: list[dict] = []
        if not text:
            return out
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                out.append(entry)
        return out

    @staticmethod
    def _parse_jsonl_path(path: str) -> list[dict]:
        """Parse a host-side JSONL file into a list of dicts."""
        try:
            with open(path, "r") as f:
                text = f.read()
        except OSError:
            return []
        return BhattiSession._parse_jsonl_text(text)

    def raw_transcript(self) -> dict:
        """Return the JSONL transcript entries (parity with ``CCSession.raw_transcript``).

        The tmux backend also merges a host-side chat sidecar; bhatti has no
        such sidecar (it would live inside the VM and isn't currently
        materialised), so we return entries-only.
        """
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return {"entries": [], "path": None, "count": 0}
        try:
            self._materialise_state_files()
        except (BhattiError, BhattiNotFound):
            pass
        path = self._host_jsonl_mirror if os.path.exists(self._host_jsonl_mirror) else None
        entries = self._parse_jsonl_path(path) if path else []
        # ``path`` field returned to clients points at the *in-VM* path, since
        # that's what callers expect to display ("where claude wrote it").
        vm_path = self._jsonl_path
        return {"entries": entries, "path": vm_path, "count": len(entries)}

    def progress_entries(self) -> list:
        """Return normalised progress events (parity with ``CCSession.progress_entries``)."""
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return []
        try:
            self._materialise_state_files()
        except (BhattiError, BhattiNotFound):
            pass
        if not os.path.exists(self._host_jsonl_mirror):
            return []
        entries = self._parse_jsonl_path(self._host_jsonl_mirror)
        # Local import — keeps the symbol live across formatter passes.
        from progress import normalize_jsonl_entries  # noqa: WPS433

        return normalize_jsonl_entries(entries)

    def progress_snapshot(self):
        """Return a derived progress snapshot (parity with ``CCSession.progress_snapshot``)."""
        from progress import derive_progress_snapshot  # noqa: WPS433

        try:
            self._ensure_started()
        except BhattiSessionStale:
            return derive_progress_snapshot([], is_question=False, is_prompt=False)
        return derive_progress_snapshot(
            self.progress_entries(),
            is_question=self.question_status(),
            is_prompt=self._is_tmux_idle(),
        )

    def question_status(self) -> bool:
        """True iff there's an unanswered AskUserQuestion on screen."""
        try:
            return self.current_question() is not None
        except (BhattiError, BhattiNotFound):
            return False

    def _is_tmux_idle(self) -> bool:
        """For bhatti: defer to the higher-level ``state`` classifier."""
        try:
            return self.state == "idle"
        except (BhattiError, BhattiNotFound):
            return False

    def subagents(self) -> list[dict]:
        """Return sub-agent summaries.

        TODO(bhatti): the tmux backend reads
        ``~/.claude/projects/-<slug>/<session-id>/subagents/*.jsonl``. Inside
        the VM these would live at ``/home/lohar/.claude/projects/...`` — we
        could materialise them via ``client.ls`` + ``client.read_file``, but
        the UI tolerates an empty list, so we punt for now.
        """
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return []
        return []

    def files(self) -> list[str]:
        """List files in the VM's working dir, recursively (matches CCSession).

        Uses a single ``find`` exec instead of ``client.ls`` so subdirectories
        are walked the same way ``os.walk`` does for tmux sessions. Without
        this, files written into ``inbox/<threadId>/...`` (gmail attachments)
        and other nested paths don't show up in the Files tab.

        Tolerates ``BhattiError``/``BhattiNotFound`` by returning ``[]``.
        """
        try:
            self._ensure_started()
        except BhattiSessionStale:
            return []
        # ``find -type f`` recursively. We exclude hidden entries via prune
        # at any depth (``-name '.*' -prune``) so dotfiles match CCSession.
        # Output is one absolute path per line.
        cmd = f"find {self._working_dir} " f"\\( -name '.*' -prune \\) -o -type f -print"
        try:
            result = self._exec(["sh", "-c", cmd], timeout_sec=15, check=False)
        except (BhattiError, BhattiNotFound):
            return []
        stdout = result.get("stdout", "") or ""
        prefix = self._working_dir.rstrip("/") + "/"
        rels: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith(prefix):
                continue
            rels.append(line[len(prefix) :])
        return sorted(rels)

    def read_file(self, relative_path: str) -> bytes:
        """Read a workspace file inside the VM.

        Path-traversal guard: rejects any path containing a ``..`` segment or
        an absolute path. Maps ``BhattiNotFound`` to ``FileNotFoundError`` and
        ``BhattiError`` to itself (callers handle 5xx).
        """
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("relative_path must be a non-empty string")
        if relative_path.startswith("/"):
            raise ValueError(f"relative_path must be relative: {relative_path!r}")
        if ".." in relative_path.split("/"):
            raise ValueError(f"path traversal blocked: {relative_path!r}")
        try:
            self._ensure_started()
        except BhattiSessionStale as e:
            # Map to FileNotFoundError so existing callers (which already
            # handle missing files) don't 500 on stale VMs.
            raise FileNotFoundError(str(e)) from e
        vm_path = os.path.join(self._working_dir, relative_path)
        try:
            data = self._client.read_file(self._vm_name, vm_path)
        except BhattiNotFound as e:
            raise FileNotFoundError(str(e)) from e
        if isinstance(data, str):
            return data.encode("utf-8")
        return data

    # send / answer / queue_message ------------------------------------------

    def _wait_for_response(self, timeout: int) -> "Response":
        """Poll until the session is idle / has a question / timeout.

        Returns a Response whose shape matches what
        ``server._serialize_send_response`` consumes:
        ``text``, ``role``, ``raw``, ``is_question``, ``questions``.
        """
        from cchost import Response  # noqa: WPS433 — keep import live across formatter

        start = time.time()
        # Brief grace for state to leave 'idle' so we don't immediately
        # decide the run is done before claude even started.
        idle_grace_deadline = start + 5.0
        saw_non_idle = False

        while time.time() - start < timeout:
            try:
                current_state = self.state
            except (BhattiError, BhattiNotFound):
                current_state = "dormant"

            # If a question is pending, surface it immediately.
            try:
                question = self.current_question()
            except (BhattiError, BhattiNotFound):
                question = None
            if question:
                # Pull the latest assistant text out of the transcript so the
                # response carries some context alongside the question.
                text = self._latest_assistant_text() or question.get("question", "")
                return Response(
                    text=text,
                    role="assistant",
                    is_question=True,
                    questions=[question],
                )

            if current_state != "idle":
                saw_non_idle = True
            elif current_state == "idle" and (saw_non_idle or time.time() > idle_grace_deadline):
                # Settled: read the latest assistant turn and return.
                text = self._latest_assistant_text()
                return Response(text=text or "", role="assistant", raw={})

            time.sleep(0.5)

        return Response(text="(timeout waiting for response)", role="assistant")

    def _latest_assistant_text(self) -> str:
        """Extract the text from the most recent ``assistant`` JSONL entry."""
        try:
            self._materialise_state_files()
        except (BhattiError, BhattiNotFound):
            pass
        if not os.path.exists(self._host_jsonl_mirror):
            return ""
        entries = self._parse_jsonl_path(self._host_jsonl_mirror)
        for entry in reversed(entries):
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message", {})
            if not isinstance(message, dict):
                continue
            content = message.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                if parts:
                    return "\n".join(parts)
        return ""

    def send(self, message: str, timeout: int = 600) -> "Response":
        """Send a message and wait for the response (parity with CCSession.send)."""
        # Re-raise BhattiSessionStale: callers must hear about destroyed VMs.
        self._ensure_started()
        if self.is_dormant:
            raise RuntimeError(f"Session {self.id} is dormant")
        self.send_message(message)
        return self._wait_for_response(timeout)

    # NOTE: this overrides the simple ``answer(option_index)`` defined earlier
    # in the class. We keep the same key-press behaviour and additionally wait
    # for the next response — matching ``CCSession.answer``'s contract.
    def answer(self, option_index: int = 1, timeout: int = 600) -> "Response":  # type: ignore[override]
        """Answer an AskUserQuestion and wait for the next response."""
        self._ensure_started()
        if self.is_dormant:
            raise RuntimeError(f"Session {self.id} is dormant")
        if option_index < 1:
            raise ValueError("option_index must be >= 1")
        self._tmux_send_literal(str(option_index))
        self._tmux_send_enter()
        return self._wait_for_response(timeout)

    def queue_message(self, message: str) -> dict:
        """Send a message even if claude is currently busy.

        Claude Code's CLI buffers typed input, so we just call ``send_message``
        unconditionally and report whether the session was busy at the time.
        """
        # Re-raise BhattiSessionStale before any state queries — same shape
        # as ``send`` / ``send_message`` since this is a send-shaped method.
        self._ensure_started()
        try:
            current_state = self.state
        except (BhattiError, BhattiNotFound):
            current_state = "dormant"
        try:
            had_question = self.current_question() is not None
        except (BhattiError, BhattiNotFound):
            had_question = False
        was_busy = current_state in ("working", "awaiting_permission", "awaiting_question") or had_question
        self.send_message(message)
        return {
            "status": "queued" if was_busy else "sent",
            "was_busy": was_busy,
        }

    def destroy(self) -> None:
        """Best-effort tear-down of the VM and any host tempdir."""
        try:
            self._client.destroy_vm(self._vm_name, force=True)
        except (BhattiError, BhattiNotFound) as e:
            logger.debug("destroy_vm failed for %s: %s", self._vm_name, e)
        try:
            import shutil

            shutil.rmtree(self._state_tmpdir, ignore_errors=True)
        except OSError:
            pass
