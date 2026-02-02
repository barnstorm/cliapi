# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Gateway is a unified API interface that transforms local coding agents (Claude Code, Amazon Q, Codex, Aider) into stateless, programmable endpoints. It normalizes agent invocation across three deployment modes: CLI, HTTP Server, and Daemon.

## Architecture

```
┌─────────────────────────────────────┐
│  Consumers (OpenRouter, SDK, curl)  │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│    agent_server.py                  │
│   OpenAI-compatible HTTP endpoint   │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│    agent-call (Bash wrapper)        │
│  - Prompt wrapping for one-shot     │
│  - Agent dispatch                   │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│  Coding Agents (claude, q, aider)   │
└─────────────────────────────────────┘
```

### Core Components

- **agent-call**: Bash wrapper that normalizes agent invocation. Handles prompt wrapping, agent dispatch, and JSON schema passthrough.
- **agent_server.py**: Flask-based OpenAI-compatible HTTP server. Maps models (claude-code, amazon-q, codex, aider) to agent-call invocations.
- **agent-daemon.sh**: Persistent Claude Code process using FIFOs for low-latency repeated calls.
- **agent-client.py**: Client for communicating with the daemon using Claude's stream-json protocol.

## Common Commands

### CLI Mode
```bash
./agent-call "what does this do?"
./agent-call -a amazonq -c ~/myproject "analyze code"
./agent-call -j '{"type":"object"...}' "list files"
./agent-call -v "prompt"   # verbose
./agent-call -q "prompt"   # quiet
```

### HTTP Server Mode
```bash
python3 agent_server.py --port 8080
# With auth:
AGENT_GATEWAY_KEY=secret python3 agent_server.py --port 8080
```

### Daemon Mode
```bash
./agent-daemon.sh &                     # Start daemon
./agent-client.py "question"            # Send request
./agent-client.py --no-clear "follow"   # Keep context
```

## Key Implementation Details

### Prompt Wrapping
All prompts are automatically wrapped with one-shot execution instructions via `wrap_prompt()` in agent-call. This forces:
- Complete single-response execution
- No clarifying questions
- No confirmation requests

### Agent-Specific Invocation
- **claude**: `--permission-mode bypassPermissions`
- **amazonq**: `q chat --no-interactive`
- **codex**: `--approval-mode full-auto`
- **aider**: `--yes --no-git`

### Daemon Communication
- Uses FIFOs at /tmp/agent-in and /tmp/agent-out
- Claude Code runs in stream-json mode
- `/clear` command sent after each response to reset context
- PID file at /tmp/agent-daemon.pid

### HTTP Server Endpoints
- `POST /v1/chat/completions` - Main endpoint (OpenAI format)
- `GET /v1/models` - List available models
- `GET /health` - Health check

## Dependencies

- Python 3.10+
- Flask (for agent_server.py only)
- jq (optional, for JSON schema extraction)
- At least one agent: `claude`, `q`, `codex`, or `aider`

## Security Notes

The system uses `--permission-mode bypassPermissions` for Claude Code, enabling arbitrary bash execution and file I/O. Run in sandboxed environments when exposed publicly.
