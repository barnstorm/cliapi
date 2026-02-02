#!/usr/bin/env bash
# agent-daemon.sh
# Persistent Claude Code process with context reset between prompts

set -uo pipefail

FIFO_IN="/tmp/agent-in"
FIFO_OUT="/tmp/agent-out"
PIDFILE="/tmp/agent-daemon.pid"

cleanup() {
    [[ -n "${CLAUDE_PID:-}" ]] && kill "$CLAUDE_PID" 2>/dev/null
    rm -f "$FIFO_IN" "$FIFO_OUT" "$PIDFILE"
    exit 0
}
trap cleanup EXIT INT TERM

# Create FIFOs
rm -f "$FIFO_IN" "$FIFO_OUT"
mkfifo "$FIFO_IN"
mkfifo "$FIFO_OUT"

echo $$ > "$PIDFILE"

# Start Claude Code in stream mode
# Keep input open with fd 3
exec 3<>"$FIFO_IN"

claude \
    --input-format stream-json \
    --output-format stream-json \
    --permission-mode bypassPermissions \
    --no-session-persistence \
    --verbose \
    < "$FIFO_IN" \
    > "$FIFO_OUT" 2>/dev/null &

CLAUDE_PID=$!

echo "Daemon started (PID $$, Claude PID $CLAUDE_PID)"
echo "Input: $FIFO_IN"
echo "Output: $FIFO_OUT"

wait "$CLAUDE_PID"
