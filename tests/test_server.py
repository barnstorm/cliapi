"""Tests for agent_server.py — routing, error mapping, health, and auth.

Run with: pytest tests/
"""

import importlib
import json

import pytest


@pytest.fixture
def srv(monkeypatch):
    """Fresh import of the server with a clean module state per test."""
    import agent_server
    importlib.reload(agent_server)
    return agent_server


@pytest.fixture
def client(srv):
    return srv.app.test_client()


# --- model routing -----------------------------------------------------------

def test_kiro_alias_routes_to_kiro_backend(srv, client, monkeypatch):
    captured = {}

    def fake(prompt, agent, model=None, **kw):
        captured["agent"] = agent
        return "ok"

    monkeypatch.setattr(srv, "call_agent", fake)
    r = client.post("/v1/chat/completions",
                    json={"model": "kiro", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert captured["agent"] == "kiro"


def test_unknown_model_rejected_when_not_forced(srv, client, monkeypatch):
    monkeypatch.setattr(srv, "FORCE_AGENT", "")
    r = client.post("/v1/chat/completions",
                    json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 400
    assert r.get_json()["error"]["type"] == "invalid_request_error"


def test_forced_agent_accepts_arbitrary_model(srv, client, monkeypatch):
    """A consumer (e.g. Hermes) can send its own model id to a forced backend."""
    captured = {}

    def fake(prompt, agent, model=None, **kw):
        captured["agent"] = agent
        return "ok from kiro"

    monkeypatch.setattr(srv, "FORCE_AGENT", "kiro")
    monkeypatch.setattr(srv, "call_agent", fake)
    r = client.post("/v1/chat/completions",
                    json={"model": "hermes-3-pro", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert captured["agent"] == "kiro"


# --- error propagation -------------------------------------------------------

def test_agent_failure_maps_to_502_not_200(srv, client, monkeypatch):
    def boom(*a, **k):
        raise srv.AgentError("kiro-cli: not logged in", status=502)

    monkeypatch.setattr(srv, "FORCE_AGENT", "kiro")
    monkeypatch.setattr(srv, "call_agent", boom)
    r = client.post("/v1/chat/completions",
                    json={"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502
    body = r.get_json()
    assert body["error"]["type"] == "agent_error"
    assert "not logged in" in body["error"]["message"]


def test_timeout_maps_to_504(srv, client, monkeypatch):
    def slow(*a, **k):
        raise srv.AgentError("timed out", status=504, err_type="timeout_error")

    monkeypatch.setattr(srv, "FORCE_AGENT", "kiro")
    monkeypatch.setattr(srv, "call_agent", slow)
    r = client.post("/v1/chat/completions",
                    json={"model": "x", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 504
    assert r.get_json()["error"]["type"] == "timeout_error"


# --- health ------------------------------------------------------------------

def test_health_503_when_forced_agent_missing(srv, client, monkeypatch):
    monkeypatch.setattr(srv, "FORCE_AGENT", "kiro")
    monkeypatch.setattr(srv.shutil, "which", lambda _b: None)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.get_json()["status"] == "unavailable"


def test_health_ok_when_an_agent_is_installed(srv, client, monkeypatch):
    monkeypatch.setattr(srv, "FORCE_AGENT", "")
    monkeypatch.setattr(srv.shutil, "which", lambda b: "/usr/bin/claude" if b == "claude" else None)
    r = client.get("/health")
    assert r.status_code == 200
    j = r.get_json()
    assert j["status"] == "ok"
    assert j["agents"]["claude"] is True
    assert j["agents"]["kiro"] is False


# --- auth --------------------------------------------------------------------

def test_auth_rejects_wrong_key(srv, client, monkeypatch):
    monkeypatch.setattr(srv, "API_KEY", "secret")
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer wrong"},
                    json={"model": "kiro", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_auth_accepts_correct_key(srv, client, monkeypatch):
    monkeypatch.setattr(srv, "API_KEY", "secret")
    monkeypatch.setattr(srv, "FORCE_AGENT", "kiro")
    monkeypatch.setattr(srv, "call_agent", lambda *a, **k: "ok")
    r = client.post("/v1/chat/completions",
                    headers={"Authorization": "Bearer secret"},
                    json={"model": "kiro", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200


# --- call_agent subprocess contract -----------------------------------------

def test_call_agent_raises_on_nonzero_exit(srv, tmp_path, monkeypatch):
    """A failing agent-call surfaces as AgentError, not as content."""
    stub = tmp_path / "agent-call"
    stub.write_text("#!/usr/bin/env bash\necho 'boom: agent not found' >&2\nexit 1\n")
    stub.chmod(0o755)
    monkeypatch.setattr(srv, "AGENT_CALL", str(stub))
    with pytest.raises(srv.AgentError) as exc:
        srv.call_agent("hi", "kiro")
    assert exc.value.status == 502
    assert "boom" in exc.value.message


def test_call_agent_returns_stdout_on_success(srv, tmp_path, monkeypatch):
    stub = tmp_path / "agent-call"
    stub.write_text("#!/usr/bin/env bash\necho 'hello world'\n")
    stub.chmod(0o755)
    monkeypatch.setattr(srv, "AGENT_CALL", str(stub))
    assert srv.call_agent("hi", "kiro") == "hello world"


# --- streaming ---------------------------------------------------------------

def _parse_sse(body):
    """Return (role_count, joined_content, finish_reason, saw_done)."""
    roles, contents, finish, done = 0, [], None, False
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            done = True
            continue
        obj = json.loads(payload)
        delta = obj["choices"][0]["delta"]
        if delta.get("role"):
            roles += 1
        if "content" in delta:
            contents.append(delta["content"])
        if obj["choices"][0]["finish_reason"]:
            finish = obj["choices"][0]["finish_reason"]
    return roles, "".join(contents), finish, done


def _stub_agent_call(tmp_path, srv, monkeypatch, body):
    stub = tmp_path / "agent-call"
    stub.write_text(body)
    stub.chmod(0o755)
    monkeypatch.setattr(srv, "AGENT_CALL", str(stub))
    monkeypatch.setattr(srv, "FORCE_AGENT", "kiro")


def test_streaming_forwards_real_output_incrementally(srv, client, tmp_path, monkeypatch):
    # Sleep between writes so the two halves arrive as separate reads — proves
    # we forward as produced rather than buffering the whole reply first.
    _stub_agent_call(tmp_path, srv, monkeypatch,
                     "#!/usr/bin/env bash\nprintf 'Hello '\nsleep 0.05\nprintf 'world'\n")
    r = client.post("/v1/chat/completions",
                    json={"model": "kiro", "stream": True,
                          "messages": [{"role": "user", "content": "hi"}]})
    assert r.headers["Content-Type"].startswith("text/event-stream")
    roles, content, finish, done = _parse_sse(r.get_data(as_text=True))
    assert roles == 1
    assert content == "Hello world"
    assert finish == "stop"
    assert done


def test_streaming_surfaces_backend_error(srv, client, tmp_path, monkeypatch):
    _stub_agent_call(tmp_path, srv, monkeypatch,
                     "#!/usr/bin/env bash\necho 'kiro: not authed' >&2\nexit 1\n")
    r = client.post("/v1/chat/completions",
                    json={"model": "kiro", "stream": True,
                          "messages": [{"role": "user", "content": "x"}]})
    _, content, _, done = _parse_sse(r.get_data(as_text=True))
    assert "not authed" in content
    assert done


def test_streaming_falls_back_to_buffered_for_json_schema(srv, client, monkeypatch):
    monkeypatch.setattr(srv, "FORCE_AGENT", "kiro")
    monkeypatch.setattr(srv, "call_agent", lambda *a, **k: '{"ok": true}')
    r = client.post("/v1/chat/completions",
                    json={"model": "kiro", "stream": True,
                          "response_format": {"type": "json_schema",
                                              "json_schema": {"schema": {"type": "object"}}},
                          "messages": [{"role": "user", "content": "x"}]})
    assert r.headers["Content-Type"].startswith("application/json")
    assert r.get_json()["object"] == "chat.completion"
