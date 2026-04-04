#!/bin/bash
# Start cchost API server
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting cchost..."
echo ""

# Start the cchost API server
echo "Starting API server on :8420..."
cd "$SCRIPT_DIR"
python3 server.py &
API_PID=$!

echo ""
echo "Ready!"
echo "  cchost API:  http://localhost:8420/docs"
echo "  cchost UI:   http://localhost:8420/ui"
echo ""
echo "To stop: kill $API_PID"
echo ""

# Wait for API server
wait $API_PID
