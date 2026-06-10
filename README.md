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
| `-a, --agent` | Agent: claude, amazonq, codex, aider, kiro (default: claude) |
| `-m, --model` | Model override |
| `-c, --context` | Working directory |
| `-j, --json-schema` | JSON schema for structured output |
| `-q, --quiet` | Suppress stderr |
| `-r, --raw` | Skip prompt wrapping |
| `-s, --stream` | Stream output as produced (text only, no buffering) |

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
| GET | `/health` | Readiness check — reports installed backends; 503 if the forced/only agent is missing |

**Streaming:** with `"stream": true`, the server forwards the agent's output as
real Server-Sent Events as it is produced (not buffered-then-chunked), and kills
the backend process if the client disconnects. Streaming is skipped when a JSON
schema is requested, since structured output can't be streamed.

**Error semantics:** agent failures map to proper HTTP status codes with an
OpenAI-style error envelope, rather than being returned as a successful
completion. A backend that errors or isn't authenticated yields `502`, a
timeout yields `504`, and an unknown model (when no agent is forced) yields
`400`. This lets consumers like Hermes distinguish a real answer from a failure.

**Tool calling:** OpenAI `tools`/`functions` can't be fulfilled by a coding-agent
backend. Optional tools (`tool_choice` absent/`"auto"`/`"none"`) are accepted and
ignored, so clients that always attach tools still work. A *required* tool call
(`tool_choice: "required"` or a specific function) yields `400` rather than a
text answer pretending to be a tool call.

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
| `kiro` | Kiro CLI (AWS) |
| `aider` | Aider |
| `aider-gpt4` | Aider with GPT-4 |
| `aider-claude` | Aider with Claude |

**Extending the registry (`agents.json`):**

Backends and model aliases are defined in `agents.json`, the single source of
truth the server reads for routing, `/v1/models`, and the health probe:

```json
{
  "agents": {
    "kiro": { "binary": "kiro-cli", "binary_env": "KIRO_CLI", "owned_by": "amazon" }
  },
  "models": {
    "kiro": { "agent": "kiro" },
    "aider-gpt4": { "agent": "aider", "model": "gpt-4" }
  }
}
```

- Adding a **model alias** (or changing a binary/owner) is a config-only edit.
- Adding a **new backend** also needs a `run_<agent>` function and dispatch case
  in `agent-call`, since each agent's invocation is bespoke.
- Point the server at an alternate file with `AGENT_GATEWAY_REGISTRY=/path.json`.
  If the file is missing it falls back to built-in defaults.

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
- Hermes Agent (Nous Research — via its `custom` provider)

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

## Backend: Kiro CLI

[Kiro](https://kiro.dev) (AWS) can sit behind the gateway like any other agent.
Select it per-request with `"model": "kiro"`, or dedicate a whole instance to it
with `AGENT_GATEWAY_FORCE_AGENT=kiro`.

The gateway invokes Kiro's [headless mode](https://kiro.dev/docs/cli/headless/):

```bash
kiro-cli chat --no-interactive --trust-all-tools "<prompt>"
```

Notes:
- `--trust-all-tools` is Kiro's permission bypass (counterpart of Claude Code's
  `bypassPermissions`) — required so headless runs don't stall on approval.
- Authenticate via `kiro-cli login` or the `KIRO_API_KEY` environment variable.
- Headless mode has no per-call model flag; passing `-m` runs
  `kiro-cli settings chat.defaultModel <model>` first, which changes your
  persistent default.
- ANSI color codes are stripped from output
  ([kirodotdev/Kiro#8352](https://github.com/kirodotdev/Kiro/issues/8352)).

If your install differs, override the invocation without touching code:

```bash
export KIRO_CLI=kiro-cli                                     # binary name
export KIRO_ARGS="chat --no-interactive --trust-all-tools"   # subcommand/flags
```

```bash
# One-shot CLI against Kiro
./agent-call -a kiro "summarize this repo"

# Dedicated Kiro-backed HTTP gateway
AGENT_GATEWAY_FORCE_AGENT=kiro python3 agent_server.py --port 8082
```

## Consumer: Hermes Agent (and other API clients)

The gateway *is* the API. Any agent that can call an OpenAI-compatible
`/v1/chat/completions` endpoint — such as
[Hermes Agent](https://github.com/NousResearch/hermes-agent) — can use it as
its LLM backend, with a real coding agent like **Kiro** doing the work behind
the scenes.

```
  Hermes Agent ──HTTP──▶  agent_server.py  ──▶  agent-call  ──▶  kiro-cli
  (consumer)              (OpenAI API)                           (backend)
```

1. Start a gateway dedicated to Kiro so the consumer doesn't need to know our
   model aliases (any `model` string it sends is accepted and routed to Kiro):

```bash
AGENT_GATEWAY_KEY=secret AGENT_GATEWAY_FORCE_AGENT=kiro \
  python3 agent_server.py --port 8082
```

2. Configure Hermes Agent's `custom` provider in `~/.hermes/config.yaml`:

```yaml
model:
  default: kiro
  provider: custom
  base_url: http://localhost:8082/v1
  api_key: secret
```

   Or per-invocation / mid-session:

```bash
hermes chat --provider custom --model kiro
# or inside a session:
/model custom:kiro
```

3. Or per-request, without forcing the agent, target the backend by model name:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer secret" \
  -H "Content-Type: application/json" \
  -d '{"model":"kiro","messages":[{"role":"user","content":"explain main.py"}]}'
```

> When `AGENT_GATEWAY_FORCE_AGENT` is set, the gateway no longer rejects unknown
> model names — it forwards every request to the forced backend. That keeps it
> drop-in compatible with consumers (like Hermes) regardless of what model
> identifier they send.

## Development

```bash
pip install -r requirements-dev.txt
pytest                 # server routing, error mapping, health, auth, dispatch
```

`tests/test_server.py` covers the HTTP layer; `tests/test_agent_call.py`
exercises the `agent-call` dispatcher against stub agent binaries, so no real
agent CLIs are required to run the suite.

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
| `agents.json` | Backend + model-alias registry (single source of truth) |
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
