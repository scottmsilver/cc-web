#!/bin/bash
# Claude Code hook that logs events for cchost progress tracking.
# Receives JSON on stdin, appends to a progress log file.
# The log file path is derived from the session's working directory.

# Read the JSON input
INPUT=$(cat)

# Extract the working directory and event name
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
EVENT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hook_event_name',''))" 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))" 2>/dev/null)

# Write to progress log in the working directory
if [ -n "$CWD" ]; then
    LOG="$CWD/.cchost-events.jsonl"
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
