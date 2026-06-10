"""Tests for the agent-call bash dispatcher via real subprocess execution.

Stub agent binaries are placed on PATH so we can assert exactly how each agent
is invoked without needing the real CLIs installed.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
AGENT_CALL = ROOT / "agent-call"


def run(args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([str(AGENT_CALL), *args], capture_output=True, text=True, env=e)


@pytest.fixture
def stub_kiro(tmp_path, monkeypatch):
    """Install a fake kiro-cli that echoes its args/stdin and emits ANSI codes."""
    stub = tmp_path / "kiro-cli"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == settings ]]; then echo \"$*\" >> \"$KIRO_SETTINGS_LOG\"; exit 0; fi\n"
        "printf 'ARGS=[%s]\\n' \"$*\"\n"
        "printf '\\033[32mcolored\\033[0m\\n'\n"
    )
    stub.chmod(0o755)
    log = tmp_path / "settings.log"
    monkeypatch.setenv("KIRO_CLI", str(stub))
    monkeypatch.setenv("KIRO_SETTINGS_LOG", str(log))
    return log


def test_kiro_prompt_is_positional_with_trust_flag(stub_kiro):
    r = run(["-a", "kiro", "-r", "hello kiro"])
    assert r.returncode == 0
    assert "chat --no-interactive --trust-all-tools hello kiro" in r.stdout


def test_kiro_strips_ansi_codes(stub_kiro):
    r = run(["-a", "kiro", "-r", "x"])
    assert "\x1b[" not in r.stdout
    assert "colored" in r.stdout


def test_kiro_model_sets_default_model_setting(stub_kiro):
    r = run(["-a", "kiro", "-m", "claude-sonnet-4", "-r", "x"])
    assert r.returncode == 0
    assert stub_kiro.read_text().strip() == "settings chat.defaultModel claude-sonnet-4"


def test_unknown_agent_exits_nonzero():
    r = run(["-a", "bogus", "hi"])
    assert r.returncode != 0
    assert "Unknown agent" in r.stderr


def test_missing_prompt_exits_nonzero():
    r = run([])
    assert r.returncode != 0
