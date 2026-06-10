#!/usr/bin/env python3
"""
OpenAI-compatible HTTP server for Agent Gateway.

Exposes local coding agents as /v1/chat/completions endpoint.
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from hmac import compare_digest
from typing import Generator

from flask import Flask, Response, jsonify, request

app = Flask(__name__)

# Simple API key auth (set AGENT_GATEWAY_KEY env var, or leave empty to disable)
API_KEY = os.environ.get("AGENT_GATEWAY_KEY", "")

# Force a specific agent regardless of model requested (for dedicated instances)
# Valid values: claude, codex, amazonq, aider, kiro (or empty to use model-based routing)
FORCE_AGENT = os.environ.get("AGENT_GATEWAY_FORCE_AGENT", "")


def check_auth():
    """Check API key if configured (constant-time comparison)."""
    if not API_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return compare_digest(auth[7:], API_KEY)
    return False


# Directory containing agent-call script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_CALL = os.path.join(SCRIPT_DIR, "agent-call")

# Single source of truth for backends + model aliases. Falls back to these
# built-in defaults if agents.json is missing or unreadable, so the server
# still runs out of the box.
REGISTRY_PATH = os.environ.get("AGENT_GATEWAY_REGISTRY", os.path.join(SCRIPT_DIR, "agents.json"))

_DEFAULT_REGISTRY = {
    "agents": {
        "claude": {"binary": "claude", "owned_by": "anthropic"},
        "amazonq": {"binary": "q", "owned_by": "amazon"},
        "codex": {"binary": "codex", "owned_by": "openai"},
        "aider": {"binary": "aider", "owned_by": "aider"},
        "kiro": {"binary": "kiro-cli", "binary_env": "KIRO_CLI", "owned_by": "amazon"},
    },
    "models": {
        "claude-code": {"agent": "claude"},
        "claude-code-opus": {"agent": "claude", "model": "opus"},
        "claude-code-sonnet": {"agent": "claude", "model": "sonnet"},
        "amazon-q": {"agent": "amazonq"},
        "amazonq": {"agent": "amazonq"},
        "codex": {"agent": "codex"},
        "kiro": {"agent": "kiro"},
        "kiro-cli": {"agent": "kiro"},
        "aider": {"agent": "aider"},
        "aider-gpt4": {"agent": "aider", "model": "gpt-4"},
        "aider-claude": {"agent": "aider", "model": "claude-3-opus-20240229"},
    },
}


def load_registry(path: str = REGISTRY_PATH) -> dict:
    """Build MODEL_MAP, AVAILABLE_MODELS, and AGENT_BINARIES from agents.json."""
    try:
        with open(path) as f:
            reg = json.load(f)
    except (OSError, ValueError):
        reg = _DEFAULT_REGISTRY

    agents = reg.get("agents", {})
    models = reg.get("models", {})

    model_map = {
        alias: {"agent": cfg["agent"], "model": cfg.get("model")}
        for alias, cfg in models.items()
    }

    available = [
        {
            "id": alias,
            "object": "model",
            "owned_by": agents.get(cfg["agent"], {}).get("owned_by", "unknown"),
        }
        for alias, cfg in models.items()
    ]

    # agent -> resolved CLI binary (honoring per-agent env overrides like KIRO_CLI)
    binaries = {
        name: os.environ.get(spec["binary_env"], spec["binary"]) if spec.get("binary_env") else spec["binary"]
        for name, spec in agents.items()
    }

    return {"model_map": model_map, "available_models": available, "agent_binaries": binaries}


_registry = load_registry()
MODEL_MAP = _registry["model_map"]
AVAILABLE_MODELS = _registry["available_models"]
AGENT_BINARIES = _registry["agent_binaries"]


class AgentError(Exception):
    """A backend agent failed. Carries an HTTP status and OpenAI error type."""

    def __init__(self, message: str, status: int = 502, err_type: str = "agent_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.err_type = err_type


def extract_content(content) -> str:
    """Extract text from content (handles both string and array formats)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Handle array format: [{"type": "text", "text": "..."}, ...]
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(texts)
    return str(content) if content else ""


def messages_to_prompt(messages: list[dict]) -> str:
    """Convert OpenAI messages array to a single prompt string."""
    parts = []

    for msg in messages:
        role = msg.get("role", "user")
        content = extract_content(msg.get("content", ""))

        if role == "system":
            parts.append(f"[System]: {content}")
        elif role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"[Previous response]: {content}")

    return "\n\n".join(parts)


def extract_json_schema(response_format: dict | None) -> str | None:
    """Extract JSON schema from response_format if present."""
    if not response_format:
        return None

    if response_format.get("type") != "json_schema":
        return None

    json_schema = response_format.get("json_schema", {})
    schema = json_schema.get("schema")

    if schema:
        return json.dumps(schema)

    return None


def call_agent(
    prompt: str,
    agent: str,
    model: str | None = None,
    context: str | None = None,
    json_schema: str | None = None,
    timeout: int = 300,
) -> str:
    """Call agent-call and return the response.

    Raises AgentError on failure so the caller can map it to a proper HTTP
    status instead of returning the error text as a successful completion.
    """
    # No -q here: we want the agent's stderr so failures carry a real message.
    cmd = [AGENT_CALL, "-a", agent]

    if model:
        cmd.extend(["-m", model])

    if context:
        cmd.extend(["-c", context])

    if json_schema:
        cmd.extend(["-j", json_schema])

    cmd.append(prompt)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=context or SCRIPT_DIR,
        )
    except subprocess.TimeoutExpired:
        raise AgentError(
            f"Agent '{agent}' timed out after {timeout}s",
            status=504,
            err_type="timeout_error",
        )
    except FileNotFoundError:
        raise AgentError(f"agent-call not found at {AGENT_CALL}", status=500)
    except Exception as e:
        raise AgentError(str(e), status=500)

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "agent execution failed"
        raise AgentError(error_msg, status=502)

    return result.stdout.strip()


def generate_completion_id() -> str:
    """Generate a unique completion ID."""
    return f"chatcmpl-{uuid.uuid4().hex[:12]}"


def create_response(
    content: str,
    model: str,
    completion_id: str | None = None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> dict:
    """Create OpenAI-compatible response object."""
    if completion_id is None:
        completion_id = generate_completion_id()

    # Estimate tokens if not provided
    if prompt_tokens == 0:
        prompt_tokens = len(content.split()) // 2
    if completion_tokens == 0:
        completion_tokens = len(content.split())

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def stream_agent_response(
    prompt: str,
    agent: str,
    model: str | None,
    context: str | None,
    model_name: str,
) -> Generator[str, None, None]:
    """Stream a backend agent's output as OpenAI SSE chunks.

    Unlike the old buffer-then-chunk approach, this spawns agent-call in
    streaming mode and forwards output as the agent produces it. If the client
    disconnects, the backend subprocess is killed so it doesn't keep running
    (and holding a bypassPermissions shell open).
    """
    cmd = [AGENT_CALL, "-a", agent, "-s"]
    if model:
        cmd.extend(["-m", model])
    if context:
        cmd.extend(["-c", context])
    cmd.append(prompt)

    completion_id = generate_completion_id()
    created = int(time.time())

    def chunk(delta: dict, finish: str | None = None) -> str:
        return "data: " + json.dumps({
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }) + "\n\n"

    # Capture stderr to a temp file so a chatty agent can't deadlock on a full
    # stderr pipe, and so we can surface the message if it fails.
    errfile = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=errfile,
        cwd=context or SCRIPT_DIR,
    )

    yield chunk({"role": "assistant"})

    produced = False
    try:
        fd = proc.stdout.fileno()
        while True:
            data = os.read(fd, 4096)
            if not data:
                break
            produced = True
            yield chunk({"content": data.decode("utf-8", "replace")})
    except GeneratorExit:
        proc.kill()
        proc.wait()
        errfile.close()
        raise
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass

    ret = proc.wait()
    if ret != 0:
        errfile.seek(0)
        err = errfile.read().decode("utf-8", "replace").strip() or f"agent '{agent}' exited {ret}"
        if not produced:
            yield chunk({"content": f"[error] {err}"})
    errfile.close()

    yield chunk({}, finish="stop")
    yield "data: [DONE]\n\n"


@app.route("/health", methods=["GET"])
def health():
    """Health/readiness check.

    Reports which backend agents are actually installed (binary on PATH). If a
    forced agent is configured but missing, returns 503 so orchestrators can
    detect a misconfigured instance instead of getting 502s on every request.
    """
    agents = {name: shutil.which(binary) is not None for name, binary in AGENT_BINARIES.items()}

    status = "ok"
    http_status = 200
    if FORCE_AGENT:
        if not agents.get(FORCE_AGENT, shutil.which(FORCE_AGENT) is not None):
            status = "unavailable"
            http_status = 503
    elif not any(agents.values()):
        status = "unavailable"
        http_status = 503

    body = {
        "status": status,
        "service": "agent-gateway",
        "forced_agent": FORCE_AGENT or None,
        "agents": agents,
    }
    return jsonify(body), http_status


@app.route("/v1/models", methods=["GET"])
def list_models():
    """List available models."""
    return jsonify({
        "object": "list",
        "data": AVAILABLE_MODELS,
    })


@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """OpenAI-compatible chat completions endpoint."""
    if not check_auth():
        return jsonify({"error": {"message": "Invalid API key", "type": "auth_error"}}), 401

    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": {"message": "Request body required"}}), 400

        # Extract request parameters
        model_name = data.get("model", "claude-code")
        messages = data.get("messages", [])
        stream = data.get("stream", False)
        response_format = data.get("response_format")
        context = data.get("context")  # Custom extension for working directory

        if not messages:
            return jsonify({"error": {"message": "messages field required"}}), 400

        # OpenAI tool/function calling can't be satisfied by a coding-agent
        # backend. If the caller *requires* a tool call (tool_choice "required"
        # or a specific function), fail clearly instead of returning a text
        # answer that masquerades as a tool call. Optional tools (auto/none) are
        # accepted and ignored, so clients that always attach tools still work.
        tools = data.get("tools") or data.get("functions")
        tool_choice = data.get("tool_choice")
        tool_required = tool_choice == "required" or isinstance(tool_choice, dict)
        if tools and tool_required:
            return jsonify({
                "error": {
                    "message": (
                        "This gateway proxies coding agents and does not support "
                        "OpenAI tool/function calling. Remove `tool_choice` or set "
                        "it to \"auto\"/\"none\"."
                    ),
                    "type": "invalid_request_error",
                }
            }), 400

        # Map model to agent configuration.
        model_config = MODEL_MAP.get(model_name)

        if FORCE_AGENT:
            # Dedicated instance: route everything to the forced agent and accept
            # any model string the consumer sends (e.g. a Hermes client pointed at
            # a Kiro-backed gateway need not know our model aliases).
            agent = FORCE_AGENT
            model = None
        elif model_config:
            agent = model_config["agent"]
            model = model_config["model"]
        else:
            return jsonify({
                "error": {
                    "message": f"Unknown model: {model_name}",
                    "type": "invalid_request_error",
                }
            }), 400

        # Convert messages to prompt
        prompt = messages_to_prompt(messages)

        # Extract JSON schema if present
        json_schema = extract_json_schema(response_format)

        # Structured output can't stream, so streaming is only honored when no
        # JSON schema is requested.
        if stream and not json_schema:
            return Response(
                stream_agent_response(prompt, agent, model, context, model_name),
                mimetype="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",  # disable proxy buffering
                },
            )

        # Buffered path (raises AgentError -> proper HTTP status on failure)
        response_content = call_agent(
            prompt=prompt,
            agent=agent,
            model=model,
            context=context,
            json_schema=json_schema,
        )
        return jsonify(create_response(response_content, model_name))

    except AgentError as e:
        return jsonify({
            "error": {
                "message": e.message,
                "type": e.err_type,
            }
        }), e.status

    except Exception as e:
        return jsonify({
            "error": {
                "message": str(e),
                "type": "internal_error",
            }
        }), 500


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Agent Gateway HTTP Server")
    parser.add_argument("-p", "--port", type=int, default=8080, help="Port to listen on")
    parser.add_argument("-H", "--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")

    args = parser.parse_args()

    print(f"Starting Agent Gateway on http://{args.host}:{args.port}")
    print(f"Available models: {', '.join(m['id'] for m in AVAILABLE_MODELS)}")

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
