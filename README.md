# Agent Gateway

Unified API interface for local coding agents (Claude Code, Amazon Q, Codex, Aider). Exposes interactive CLI tools as stateless, OpenAI-compatible endpoints.

## Quick Start

```bash
# CLI mode - one-shot execution
./agent-call "explain this codebase"

# HTTP server - OpenAI-compatible API
python3 agent_server.py --port 8080

# Daemon mode - low latency persistent process
./agent-daemon.sh &
./agent-client.py "quick question"
```

## Installation

Requirements:
- Python 3.10+
- Flask (`pip install flask`)
- jq (optional, for JSON schema extraction)
- At least one agent installed: `claude`, `q`, `codex`, or `aider`

```bash
chmod +x agent-call agent-daemon.sh agent-client.py agent_server.py
```

## Modes

### Mode 1: CLI (`agent-call`)

One-shot execution with automatic prompt wrapping for non-interactive behavior.

```bash
# Basic prompt
./agent-call "list all TODO comments"

# With JSON schema output
./agent-call -j '{"type":"object","properties":{"files":{"type":"array"}}}' "list source files"

# Different agent
./agent-call -a amazonq "explain the auth flow"

# With project context
./agent-call -c ~/myproject "add tests to main.py"

# Quiet mode (stdout only)
./agent-call -q "what is 2+2"
```

**Options:**
| Flag | Purpose |
|------|---------|
| `-a, --agent` | Agent: claude, amazonq, codex, aider (default: claude) |
| `-m, --model` | Model override |
| `-c, --context` | Working directory |
| `-j, --json-schema` | JSON schema for structured output |
| `-q, --quiet` | Suppress stderr |
| `-r, --raw` | Skip prompt wrapping |

### Mode 2: HTTP Server (`agent_server.py`)

OpenAI-compatible REST API.

```bash
# Start server
python3 agent_server.py --port 8080

# With API key authentication
AGENT_GATEWAY_KEY=mysecret python3 agent_server.py --port 8080
```

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/chat/completions` | Chat completion |
| GET | `/v1/models` | List available models |
| GET | `/health` | Health check |

**Example request:**
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer mysecret" \
  -d '{
    "model": "claude-code",
    "messages": [{"role": "user", "content": "explain main.py"}]
  }'
```

**With OpenAI SDK:**
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8080/v1",
    api_key="mysecret"
)

response = client.chat.completions.create(
    model="claude-code",
    messages=[{"role": "user", "content": "refactor database.py"}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "schema": {
                "type": "object",
                "properties": {"changes": {"type": "array"}},
                "required": ["changes"]
            }
        }
    }
)
```

**Available models:**
| Model | Agent |
|-------|-------|
| `claude-code` | Claude Code (default) |
| `claude-code-opus` | Claude Code with Opus |
| `claude-code-sonnet` | Claude Code with Sonnet |
| `amazon-q` | Amazon Q |
| `codex` | OpenAI Codex CLI |
| `aider` | Aider |
| `aider-gpt4` | Aider with GPT-4 |
| `aider-claude` | Aider with Claude |

### Mode 3: Daemon (`agent-daemon.sh` + `agent-client.py`)

Persistent Claude Code process for low-latency repeated calls. Eliminates ~2-5s process startup time.

```bash
# Terminal 1: Start daemon
./agent-daemon.sh

# Terminal 2: Send requests
./agent-client.py "analyze main.py"
./agent-client.py "now check tests.py"  # context cleared automatically

# Multi-turn conversation (preserve context)
./agent-client.py --no-clear "read config.json"
./agent-client.py --no-clear "update timeout to 30"
./agent-client.py "done"  # clears after this one
```

**Client options:**
| Flag | Purpose |
|------|---------|
| `--no-clear` | Don't clear context after response |
| `--raw` | Output raw JSON stream |
| `--raw-prompt` | Skip prompt wrapping |
| `-t, --timeout` | Response timeout in seconds |

## Context Isolation

| Mode | Isolation Method |
|------|------------------|
| CLI | New process per call |
| HTTP | New subprocess per request |
| Daemon | `/clear` command after each response |

## Public Exposure

To expose the HTTP server publicly (e.g., for OpenRouter):

```bash
# Start with auth
AGENT_GATEWAY_KEY=mysecret python3 agent_server.py --port 8080

# Tunnel with ngrok
ngrok http 8080
```

## Local Integration

Use Agent Gateway as a drop-in replacement for OpenAI/OpenRouter in any compatible tool.

**Environment variables (works with many tools):**
```bash
export OPENAI_API_BASE=http://localhost:8080/v1
export OPENAI_API_KEY=unused
```

**Compatible tools:**
- Any OpenAI SDK client (Python, Node, etc.)
- aider
- Continue.dev
- Cursor (custom API endpoint)
- LangChain
- LlamaIndex

**LangChain example:**
```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:8080/v1",
    api_key="unused",
    model="claude-code"
)
```

**curl:**
```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-code","messages":[{"role":"user","content":"hello"}]}'
```

**llm (datasette):**
```bash
# Create config file
cat > ~/.config/io.datasette.llm/extra-openai-models.yaml << 'EOF'
- model_id: agent-gateway
  model_name: claude-code
  api_base: http://localhost:8080/v1
  api_key_name: agent-gateway
EOF

# Set API key
llm keys set agent-gateway --value unused

# Use it
llm -m agent-gateway "explain this code"
```

## OpenClaw Integration

[OpenClaw](https://openclaw.ai) can use Agent Gateway as its LLM backend via the `copilot-proxy` plugin.

**Quick setup:**
```bash
# Run the setup script
./setup-openclaw.sh 8080 claude-code

# Start Agent Gateway
python3 agent_server.py --port 8080

# Test
openclaw agent --agent main --message "hello"
```

**Manual setup:**

1. Enable the copilot-proxy plugin:
```bash
openclaw plugins enable copilot-proxy
```

2. Add provider config to `~/.openclaw/openclaw.json`:
```json
{
  "models": {
    "providers": {
      "copilot-proxy": {
        "baseUrl": "http://localhost:8080/v1",
        "apiKey": "n/a",
        "api": "openai-completions",
        "authHeader": false,
        "models": [
          {"id": "claude-code", "name": "claude-code", "contextWindow": 128000, "maxTokens": 8192},
          {"id": "codex", "name": "codex", "contextWindow": 128000, "maxTokens": 8192}
        ]
      }
    }
  }
}
```

3. Add auth profile to `~/.openclaw/agents/main/agent/auth-profiles.json`:
```json
{
  "profiles": {
    "copilot-proxy:local": {
      "type": "token",
      "provider": "copilot-proxy",
      "token": "n/a"
    }
  },
  "lastGood": {
    "copilot-proxy": "copilot-proxy:local"
  }
}
```

4. Set the default model and restart:
```bash
openclaw models set copilot-proxy/claude-code
openclaw gateway restart
```

**Running multiple backends:**

Use systemd services to run dedicated instances on different ports:
```bash
# Install user services
cp systemd/agent-gateway-claude.service ~/.config/systemd/user/
cp systemd/agent-gateway-codex.service ~/.config/systemd/user/

# Edit paths in service files, then:
systemctl --user daemon-reload
systemctl --user enable --now agent-gateway-claude  # port 8080
systemctl --user enable --now agent-gateway-codex   # port 8081
```

Update OpenClaw's `baseUrl` to point to the desired backend port.

## Security

The wrapper uses `--permission-mode bypassPermissions` for Claude Code, enabling arbitrary command execution. Mitigations:

- Run in sandboxed environment (container, VM)
- Use dedicated user with minimal privileges
- Keep HTTP server on localhost behind authenticated proxy
- Set `AGENT_GATEWAY_KEY` for API authentication

## Files

| File | Purpose |
|------|---------|
| `agent-call` | Bash wrapper for one-shot invocation |
| `agent_server.py` | OpenAI-compatible HTTP server |
| `agent-daemon.sh` | Persistent daemon launcher |
| `agent-client.py` | Daemon client with /clear support |
| `setup-openclaw.sh` | OpenClaw integration setup script |
| `systemd/` | Systemd service files for daemonization |
| `SPEC.md` | Technical specification |

## Troubleshooting

**Daemon not responding:**
```bash
# Check if running
cat /tmp/agent-daemon.pid
ps aux | grep agent-daemon

# Restart
pkill -f agent-daemon
rm -f /tmp/agent-*
./agent-daemon.sh &
```

**JSON schema not working:**
- Requires `jq` installed for extraction
- Only works with Claude Code agent

**Agent not found:**
- Ensure agent CLI is in PATH
- Test directly: `claude --version`, `q --version`, etc.
