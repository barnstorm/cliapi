# Systemd Service Files

Run Agent Gateway as a systemd service for persistent background operation.

## Available Services

| Service | Port | Backend | Description |
|---------|------|---------|-------------|
| `agent-gateway-claude` | 8080 | Claude Code | Forces all requests to use Claude |
| `agent-gateway-codex` | 8081 | Codex | Forces all requests to use Codex |

## Installation

1. **Edit service files** to match your setup:
   ```bash
   # Update User, Group, and WorkingDirectory in each service file
   vim systemd/agent-gateway-claude.service
   vim systemd/agent-gateway-codex.service
   ```

2. **Copy to systemd directory**:
   ```bash
   sudo cp systemd/agent-gateway-*.service /etc/systemd/system/
   ```

3. **Reload systemd**:
   ```bash
   sudo systemctl daemon-reload
   ```

4. **Enable and start**:
   ```bash
   # Claude backend on port 8080
   sudo systemctl enable --now agent-gateway-claude

   # Codex backend on port 8081
   sudo systemctl enable --now agent-gateway-codex
   ```

## Management

```bash
# Check status
sudo systemctl status agent-gateway-claude
sudo systemctl status agent-gateway-codex

# View logs
journalctl -u agent-gateway-claude -f
journalctl -u agent-gateway-codex -f

# Restart
sudo systemctl restart agent-gateway-claude

# Stop
sudo systemctl stop agent-gateway-claude

# Disable (prevent start on boot)
sudo systemctl disable agent-gateway-claude
```

## Configuration

### Environment Variables

Set in the service file under `[Service]`:

| Variable | Description |
|----------|-------------|
| `AGENT_GATEWAY_FORCE_AGENT` | Force specific backend: `claude`, `codex`, `amazonq`, `aider` |
| `AGENT_GATEWAY_KEY` | API key for authentication (optional) |

### Ports

Edit `ExecStart` to change the port:
```ini
ExecStart=/usr/bin/python3 agent_server.py --port 8080 --host 127.0.0.1
```

### Expose Publicly

Change `--host 127.0.0.1` to `--host 0.0.0.0` (and set `AGENT_GATEWAY_KEY` for auth).

## Creating Additional Backends

Copy an existing service file and modify:
```bash
cp systemd/agent-gateway-claude.service systemd/agent-gateway-aider.service
# Edit: port, AGENT_GATEWAY_FORCE_AGENT, SyslogIdentifier
```

## User Service (No Root)

To run as a user service without sudo:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/agent-gateway-claude.service ~/.config/systemd/user/

# Remove User= and Group= lines, update WorkingDirectory
vim ~/.config/systemd/user/agent-gateway-claude.service

systemctl --user daemon-reload
systemctl --user enable --now agent-gateway-claude
```
