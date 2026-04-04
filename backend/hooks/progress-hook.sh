#!/bin/bash
# Claude Code hook that logs events for cchost progress tracking.
# Receives JSON on stdin, appends to a progress log file.
# Walks up from cwd to find the session root (where .cchost-events.jsonl already exists).

# Read the JSON input
INPUT=$(cat)

# Extract the working directory and event name
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
EVENT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null)

if [ -n "$CWD" ]; then
    # Walk up from cwd to find existing .cchost-events.jsonl (session root)
    LOG_DIR="$CWD"
    while [ "$LOG_DIR" != "/" ]; do
        if [ -f "$LOG_DIR/.cchost-events.jsonl" ]; then
            break
        fi
        PARENT=$(dirname "$LOG_DIR")
        if [ "$PARENT" = "$LOG_DIR" ]; then
            break
        fi
        LOG_DIR="$PARENT"
    done

    # If not found, default to cwd (first run creates it there)
    LOG="$LOG_DIR/.cchost-events.jsonl"
    TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)

    # Add timestamp and write the full event
    echo "$INPUT" | python3 -c "
import sys, json
data = json.load(sys.stdin)
data['_cchost_timestamp'] = '$TIMESTAMP'
data['_cchost_event'] = '$EVENT'
print(json.dumps(data))
" >> "$LOG" 2>/dev/null
fi

# Exit 0 — don't interfere with Claude's execution
exit 0
