# Agent Gateway: Technical Specification

## Overview

Agent Gateway provides a unified, API-compatible interface to local coding agents (Claude Code, Amazon Q, Codex, Aider). It transforms interactive CLI tools into stateless, programmable endpoints suitable for automation, orchestration, and integration with external systems like OpenRouter.

## Problem Statement

Modern coding agents are designed for interactive terminal use. They share common limitations when used programmatically:

1. **Interactive prompts**: Agents request confirmation before file edits, command execution, and other operations. This blocks automated pipelines.

2. **Context accumulation**: Agents maintain conversation history, causing context pollution across unrelated requests.

3. **Inconsistent interfaces**: Each agent has different CLI flags, input methods, and output formats.

4. **No structured output**: Responses are free-form text, requiring brittle parsing for downstream processing.

5. **No API exposure**: Agents cannot be called over HTTP or integrated with OpenAI-compatible toolchains.

## Goals

1. Normalize the interface across agents into a single invocation pattern
2. Ensure one-shot execution without interactive blocking
3. Support structured JSON output with schema validation
4. Expose agents as OpenAI-compatible HTTP endpoints
5. Minimize latency through persistent daemon mode
6. Reset context between requests to ensure isolation

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Consumers                               │
│  (OpenRouter, OpenAI SDK, curl, scripts, orchestrators)         │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     agent_server.py                             │
│              OpenAI-compatible HTTP endpoint                    │
│         POST /v1/chat/completions, GET /v1/models               │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        agent-call                               │
│                    Bash wrapper script                          │
│  - Prompt wrapping for one-shot execution                       │
│  - Agent dispatch (claude, amazonq, codex, aider)               │
│  - JSON schema passthrough                                      │
│  - stderr suppression                                           │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Coding Agents                              │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐        │
│  │  Claude   │ │  Amazon   │ │   Codex   │ │   Aider   │        │
│  │   Code    │ │     Q     │ │           │ │           │        │
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

### Daemon Mode (Low Latency)

```
┌──────────────┐      FIFO       ┌──────────────┐      FIFO       ┌──────────────┐
│    Client    │ ──────────────▶ │    Daemon    │ ◀─────────────▶ │ Claude Code  │
│              │ ◀────────────── │              │   stream-json   │  (persistent)│
└──────────────┘                 └──────────────┘                 └──────────────┘
                                        │
                                        │ sends /clear
                                        │ after each response
                                        ▼
                                 Context Reset
```

## Components

### 1. agent-call (Bash)

The core wrapper script that normalizes agent invocation.

**Responsibilities:**
- Parse unified CLI arguments
- Wrap prompts with one-shot execution instructions
- Dispatch to appropriate agent CLI
- Pass JSON schema to agents that support it
- Suppress TUI noise from stderr
- Handle working directory context

**Key Flags:**

| Flag | Purpose |
|------|---------|
| `-a, --agent` | Select agent: claude, amazonq, codex, aider |
| `-m, --model` | Model override (agent-specific) |
| `-c, --context` | Working directory for agent |
| `-j, --json-schema` | JSON schema for structured output |
| `-r, --raw` | Skip prompt wrapping |
| `-q, --quiet` | Suppress all stderr |
| `-v, --verbose` | Show agent stderr |

**Prompt Wrapping:**

All prompts are suffixed with instructions that enforce non-interactive behavior:

```
---
IMPORTANT: This is a non-interactive, one-shot execution. You must:
- Complete the entire task in a single response
- Do NOT ask clarifying questions—make reasonable assumptions and state them
- Do NOT wait for confirmation—proceed with the most sensible approach
- Do NOT ask "would you like me to..." or "shall I..."—just do it
- If multiple interpretations exist, pick the most likely one and note your choice
- Provide complete, working output rather than partial solutions
- If you cannot complete something, explain why and provide what you can
```

**Agent-Specific Handling:**

| Agent | CLI Pattern | Permission Bypass | JSON Support |
|-------|-------------|-------------------|--------------|
| Claude Code | `claude -p --permission-mode bypassPermissions` | Native flag | `--json-schema` |
| Amazon Q | `echo $prompt \| q chat --no-interactive` | N/A | Prompt-based |
| Codex | `codex exec --full-auto` | Native flag | Prompt-based |
| Aider | `aider --yes --no-git --message` | `--yes` flag | Prompt-based |

### 2. agent_server.py (Python/Flask)

HTTP server exposing OpenAI-compatible endpoints.

**Endpoints:**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/v1/chat/completions` | Chat completion (main endpoint) |
| GET | `/v1/models` | List available model aliases |
| GET | `/health` | Health check |

**Request Format:**

Standard OpenAI chat completion request with extensions:

```json
{
  "model": "claude-code",
  "messages": [
    {"role": "system", "content": "You are a code assistant."},
    {"role": "user", "content": "List all Python files"}
  ],
  "stream": false,
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "schema": {
        "type": "object",
        "properties": {
          "files": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["files"]
      }
    }
  },
  "context": "/path/to/project"
}
```

**Model Mapping:**

The `model` field maps to agent + configuration:

| Model String | Agent | Notes |
|--------------|-------|-------|
| `claude-code` | Claude Code | Default |
| `claude-code-opus` | Claude Code | Uses opus model |
| `claude-code-sonnet` | Claude Code | Uses sonnet model |
| `amazon-q` | Amazon Q | |
| `codex` | Codex CLI | |
| `aider` | Aider | Default model |
| `aider-gpt4` | Aider | Forces GPT-4 |
| `aider-claude` | Aider | Forces Claude |

**Response Format:**

Standard OpenAI chat completion response:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "claude-code",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "{\"files\": [\"main.py\", \"utils.py\"]}"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 50,
    "completion_tokens": 25,
    "total_tokens": 75
  }
}
```

**Streaming:**

When `stream: true`, responses use Server-Sent Events. Since underlying agents don't stream, the complete response is chunked into simulated stream events.

### 3. agent-daemon.sh (Bash)

Persistent Claude Code process for low-latency repeated calls.

**Operation:**
1. Creates named pipes (FIFOs) for bidirectional communication
2. Starts Claude Code with `--input-format stream-json --output-format stream-json`
3. Keeps process alive indefinitely
4. Clients write JSON messages to input FIFO, read from output FIFO

**Advantages:**
- Eliminates process startup latency (~2-5s per call)
- Single process to monitor and manage
- Enables optional context persistence across calls

### 4. agent-client.py (Python)

Client for communicating with the daemon.

**Responsibilities:**
- Format prompts as stream-json messages
- Read and parse response stream
- Send `/clear` command after each response to reset context
- Extract text from various message types

**Message Flow:**

```
Client                          Daemon                         Claude Code
   │                               │                                │
   │─── user_message ─────────────▶│                                │
   │                               │─── user_message ──────────────▶│
   │                               │                                │
   │                               │◀── assistant_message ──────────│
   │                               │◀── content_block_delta ────────│
   │                               │◀── content_block_delta ────────│
   │                               │◀── result_text ────────────────│
   │◀── response stream ───────────│                                │
   │                               │                                │
   │─── user_message (/clear) ────▶│                                │
   │                               │─── /clear ────────────────────▶│
   │                               │◀── confirmation ───────────────│
   │◀── clear confirmation ────────│                                │
   │                               │                                │
   ▼                               ▼                                ▼
 Ready                          Waiting                      Context Reset
```

## Deployment Modes

### Mode 1: Direct CLI (Simplest)

```bash
./agent-call "explain this codebase"
```

- New process per invocation
- ~2-5s startup latency
- Full isolation guaranteed
- Suitable for: scripts, CI/CD, infrequent calls

### Mode 2: HTTP Server (API Access)

```bash
python agent_server.py --port 8080

# Expose publicly
ngrok http 8080
```

- OpenAI-compatible endpoint
- Spawns agent-call per request
- Suitable for: OpenRouter integration, remote access, multi-client

### Mode 3: Daemon (Low Latency)

```bash
# Terminal 1
./agent-daemon.sh

# Terminal 2
./agent-client.py "quick question"
```

- Single persistent process
- Sub-second response initiation
- Context cleared between calls via `/clear`
- Suitable for: high-frequency local calls, IDE integration

## Security Considerations

### Permission Bypass

The wrapper uses `--permission-mode bypassPermissions` for Claude Code, enabling:
- Arbitrary bash command execution
- File system read/write
- Network access

**Mitigations:**
- Run in sandboxed environment (container, VM)
- Restrict network egress
- Mount filesystems read-only where possible
- Use dedicated user with minimal privileges

### HTTP Server Exposure

If exposed publicly:
- Add authentication (API key, OAuth)
- Rate limiting
- Request logging
- Input validation

Recommended: Keep server on localhost, tunnel through authenticated proxy.

## Limitations

1. **No true streaming**: Underlying agents don't support streaming; responses are buffered and chunked.

2. **Agent availability**: Script assumes agents are installed and in PATH.

3. **Context size**: No explicit context window management; large contexts may fail silently.

4. **Error handling**: Agent errors may not propagate cleanly through all layers.

5. **Daemon fragility**: FIFO-based communication can hang if reader/writer dies unexpectedly.

6. **`/clear` behavior**: Depends on Claude Code's implementation; may change between versions.

## Future Considerations

1. **Connection pooling**: Multiple daemon instances for concurrent requests

2. **Queue system**: Redis/RabbitMQ for request distribution

3. **Retry logic**: Automatic retry with exponential backoff on transient failures

4. **Metrics**: Prometheus endpoints for latency, success rate, token usage

5. **Multi-agent routing**: Intelligent dispatch based on task type

6. **Context management**: Explicit context window tracking and truncation

7. **Caching**: Response caching for deterministic queries

## File Manifest

| File | Purpose |
|------|---------|
| `agent-call` | Bash wrapper for one-shot agent invocation |
| `agent_server.py` | OpenAI-compatible HTTP server |
| `agent-daemon.sh` | Persistent daemon launcher |
| `agent-client.py` | Daemon client with /clear support |

## Usage Examples

### Basic CLI

```bash
# Simple prompt
./agent-call "list all TODO comments in this repo"

# With JSON schema
./agent-call -j '{"type":"object","properties":{"todos":{"type":"array"}}}' \
  "find all TODOs"

# Different agent
./agent-call -a amazonq "explain the auth flow"

# With project context
./agent-call -c ~/projects/myapp "add input validation to user.py"
```

### HTTP API

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="unused")

response = client.chat.completions.create(
    model="claude-code",
    messages=[{"role": "user", "content": "refactor database.py"}],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "schema": {
                "type": "object",
                "properties": {
                    "changes": {"type": "array"},
                    "summary": {"type": "string"}
                }
            }
        }
    }
)

print(response.choices[0].message.content)
```

### Daemon Mode

```bash
# Start daemon
tmux new -s agent-daemon './agent-daemon.sh'

# Send requests (context cleared automatically)
./agent-client.py "analyze main.py"
./agent-client.py "now check tests.py"  # no memory of main.py

# Keep context for multi-turn
./agent-client.py --no-clear "read config.json"
./agent-client.py --no-clear "update the timeout to 30"
./agent-client.py "done, new topic"  # clears after this one
```
