#!/bin/bash
# Start cchost: API server + LibreChat UI
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting cchost..."
echo ""

# Start the cchost API server
echo "1. Starting API server on :8420..."
cd "$SCRIPT_DIR"
python3 server.py &
API_PID=$!
sleep 2

# Start LibreChat
echo "2. Starting LibreChat on :3080..."
cd "$SCRIPT_DIR/librechat"
docker compose up -d

echo ""
echo "Ready!"
echo "  cchost API:  http://localhost:8420/docs"
echo "  cchost UI:   http://localhost:8420/ui"
echo "  LibreChat:   http://localhost:3080"
echo ""
echo "To stop: kill $API_PID && cd $SCRIPT_DIR/librechat && docker compose down"
echo ""

# Wait for API server
wait $API_PID
