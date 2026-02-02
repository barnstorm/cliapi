#!/bin/bash
#
# Setup script for integrating Agent Gateway with OpenClaw
#
# Usage: ./setup-openclaw.sh [port] [model]
#   port  - Agent Gateway port (default: 8080)
#   model - Default model: claude-code, codex, amazon-q, aider (default: claude-code)
#

set -e

PORT="${1:-8080}"
MODEL="${2:-claude-code}"
OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
AUTH_PROFILES="$HOME/.openclaw/agents/main/agent/auth-profiles.json"

echo "Configuring OpenClaw to use Agent Gateway..."
echo "  Port: $PORT"
echo "  Model: $MODEL"
echo ""

# Check if openclaw is installed
if ! command -v openclaw &> /dev/null; then
    echo "Error: openclaw not found. Install it first."
    exit 1
fi

# Check if config files exist
if [ ! -f "$OPENCLAW_CONFIG" ]; then
    echo "Error: OpenClaw config not found at $OPENCLAW_CONFIG"
    echo "Run 'openclaw setup' first."
    exit 1
fi

# Enable copilot-proxy plugin
echo "Enabling copilot-proxy plugin..."
openclaw plugins enable copilot-proxy 2>/dev/null || true

# Add provider config using openclaw config set
echo "Configuring provider..."
openclaw config set models.providers.copilot-proxy.baseUrl "http://localhost:$PORT/v1"
openclaw config set models.providers.copilot-proxy.apiKey "n/a"
openclaw config set models.providers.copilot-proxy.api "openai-completions"
openclaw config set models.providers.copilot-proxy.authHeader false

# Add model definitions
echo "Adding model definitions..."
for m in claude-code claude-code-opus claude-code-sonnet codex amazon-q aider; do
    # Using jq to add models if available, otherwise manual approach
    if command -v jq &> /dev/null; then
        tmp=$(mktemp)
        jq --arg id "$m" '.models.providers["copilot-proxy"].models += [{"id": $id, "name": $id, "contextWindow": 128000, "maxTokens": 8192}]' "$OPENCLAW_CONFIG" > "$tmp" && mv "$tmp" "$OPENCLAW_CONFIG"
    fi
done

# Add auth profile
echo "Adding auth profile..."
if [ -f "$AUTH_PROFILES" ] && command -v jq &> /dev/null; then
    tmp=$(mktemp)
    jq '.profiles["copilot-proxy:local"] = {"type": "token", "provider": "copilot-proxy", "token": "n/a"} | .lastGood["copilot-proxy"] = "copilot-proxy:local"' "$AUTH_PROFILES" > "$tmp" && mv "$tmp" "$AUTH_PROFILES"
else
    echo "Warning: Could not update auth profiles. You may need to add manually."
fi

# Set default model
echo "Setting default model to copilot-proxy/$MODEL..."
openclaw models set "copilot-proxy/$MODEL"

# Restart gateway
echo "Restarting OpenClaw gateway..."
openclaw gateway restart 2>/dev/null || true

echo ""
echo "Done! OpenClaw is now configured to use Agent Gateway."
echo ""
echo "Make sure Agent Gateway is running:"
echo "  python3 agent_server.py --port $PORT"
echo ""
echo "Test with:"
echo "  openclaw agent --agent main --message 'hello'"
