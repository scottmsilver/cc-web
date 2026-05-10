#!/bin/bash
# bake-cc-base.sh — Idempotently rebuild the cc-base bhatti image from scratch.
#
# What this produces: a saved image named "cc-base" with:
#   - Ubuntu 24.04 (from minimal tier) + Node 20 + npm + git + tmux + python3
#   - Claude Code CLI (latest)
#   - /opt/cchost/hooks/progress-hook.sh (mode 0755, owned by lohar)
#   - /opt/cchost/settings-template.json wired to that hook for all event types
#
# What this does NOT include (injected per-session by the host):
#   - ~/.claude/.credentials.json
#   - ~/.claude/settings.json (callers should copy /opt/cchost/settings-template.json)
#
# Run: ./scripts/bake-cc-base.sh
# Reqs: `bhatti` on PATH, authenticated (~/.bhatti/config.yaml).

set -euo pipefail

# ---- Config -----------------------------------------------------------------
BUILDER_VM="${BUILDER_VM:-cc-base-builder}"   # transient VM used only for baking
IMAGE_NAME="${IMAGE_NAME:-cc-base}"
BASE_IMAGE="${BASE_IMAGE:-minimal}"           # built-in tier we start from
DISK_MB="${DISK_MB:-4096}"
MEM_MB="${MEM_MB:-2048}"
CPUS="${CPUS:-2}"

# Resolve repo root so we can find the host-side hook to copy in.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOST_HOOK="$REPO_ROOT/backend/hooks/progress-hook.sh"

if [[ ! -f "$HOST_HOOK" ]]; then
  echo "fatal: missing $HOST_HOOK" >&2
  exit 1
fi

echo "==> bake-cc-base: builder=$BUILDER_VM image=$IMAGE_NAME base=$BASE_IMAGE"

# ---- Step 1: clean slate ----------------------------------------------------
# Destroy any existing builder VM so re-runs always start fresh.
if bhatti list 2>/dev/null | awk '{print $1}' | grep -qx "$BUILDER_VM"; then
  echo "==> destroying existing builder VM: $BUILDER_VM"
  bhatti destroy "$BUILDER_VM" --yes >/dev/null
fi

# Delete any prior image with the same name so `image save` won't collide.
if bhatti image list 2>/dev/null | awk 'NR>1 {print $2}' | grep -qx "$IMAGE_NAME"; then
  echo "==> deleting existing image: $IMAGE_NAME"
  bhatti image delete "$IMAGE_NAME" --yes >/dev/null 2>&1 || \
    bhatti image delete "$IMAGE_NAME" >/dev/null
fi

# ---- Step 2: spin up a builder from the minimal tier ------------------------
echo "==> creating builder VM from $BASE_IMAGE"
bhatti create \
  --name "$BUILDER_VM" \
  --image "$BASE_IMAGE" \
  --cpus "$CPUS" \
  --memory "$MEM_MB" \
  --disk-size "$DISK_MB" >/dev/null

# Helper: run a shell snippet inside the builder as root.
exec_root() {
  bhatti exec --timeout 600 "$BUILDER_VM" -- bash -lc "$1"
}

# ---- Step 3: install OS deps + Node 20 + Claude Code ------------------------
echo "==> installing base packages (git, tmux, python3, curl, sudo)"
exec_root '
  set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates curl git tmux python3 python3-pip sudo gnupg
  rm -rf /var/lib/apt/lists/*
'

echo "==> installing Node 20 (NodeSource) + Claude Code CLI"
exec_root '
  set -euo pipefail
  export DEBIAN_FRONTEND=noninteractive
  # NodeSource setup script — pinned to the v20 LTS branch path.
  # If you bump majors, also update the version below.
  curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup.sh
  bash /tmp/nodesource_setup.sh
  rm -f /tmp/nodesource_setup.sh
  apt-get install -y --no-install-recommends nodejs
  npm install -g @anthropic-ai/claude-code
  claude --version
  rm -rf /var/lib/apt/lists/*
'

# ---- Step 4: install /opt/cchost/ assets ------------------------------------
# The hook is what gives cchost its session-progress timeline. The
# settings-template wires it to every Claude Code event with an empty matcher
# (i.e. all tool calls / stops / notifications fire it).
echo "==> installing progress hook + settings template into /opt/cchost/"

# 4a. Push the hook from the host into the VM.
bhatti file write "$BUILDER_VM" /opt/cchost/hooks/progress-hook.sh < "$HOST_HOOK" >/dev/null

# 4b. Write the settings template directly via stdin (small JSON, no host file
#     needed — keeps the image config self-contained).
bhatti file write "$BUILDER_VM" /opt/cchost/settings-template.json >/dev/null <<'JSON'
{
  "hooks": {
    "PreToolUse":   [{"matcher": "", "hooks": [{"type": "command", "command": "/opt/cchost/hooks/progress-hook.sh"}]}],
    "PostToolUse":  [{"matcher": "", "hooks": [{"type": "command", "command": "/opt/cchost/hooks/progress-hook.sh"}]}],
    "Stop":         [{"matcher": "", "hooks": [{"type": "command", "command": "/opt/cchost/hooks/progress-hook.sh"}]}],
    "Notification": [{"matcher": "", "hooks": [{"type": "command", "command": "/opt/cchost/hooks/progress-hook.sh"}]}],
    "SubagentStart":[{"matcher": "", "hooks": [{"type": "command", "command": "/opt/cchost/hooks/progress-hook.sh"}]}],
    "SubagentStop": [{"matcher": "", "hooks": [{"type": "command", "command": "/opt/cchost/hooks/progress-hook.sh"}]}]
  }
}
JSON

# 4c. Lock down ownership + modes. Hook must be 0755 and executable.
exec_root '
  set -euo pipefail
  chown -R lohar:lohar /opt/cchost
  chmod 0755 /opt/cchost/hooks/progress-hook.sh
  chmod 0644 /opt/cchost/settings-template.json
  ls -la /opt/cchost /opt/cchost/hooks
'

# ---- Step 5: smoke test before saving ---------------------------------------
# Cheap sanity: the binary launches and the hook script is parseable.
echo "==> smoke test"
exec_root '
  claude --version
  bash -n /opt/cchost/hooks/progress-hook.sh
  python3 -c "import json; json.load(open(\"/opt/cchost/settings-template.json\"))"
'

# ---- Step 6: save as image --------------------------------------------------
echo "==> saving image: $IMAGE_NAME"
bhatti image save "$BUILDER_VM" --name "$IMAGE_NAME" >/dev/null

# ---- Step 7: tear down builder ----------------------------------------------
echo "==> destroying builder VM: $BUILDER_VM"
bhatti destroy "$BUILDER_VM" --yes >/dev/null

echo
echo "done. cc-base is ready."
echo "  bhatti create --name cc-test --image $IMAGE_NAME --cpus 2 --memory 2048 --disk-size 4096"
