# Tify Agent Command Center

A dark-themed web dashboard to manage multiple Telegram Claude AI bots running as systemd services on a VPS.

## Features

- **Agent Management** — create, edit, restart, and delete Telegram bots
- **Memory Viewer** — view conversation history as chat bubbles, clear memory
- **Cron Scheduler** — per-agent cron jobs with a visual schedule builder
- **File Browser** — browse and download output files per agent
- **Log Viewer** — live journalctl logs per bot service
- **Skills Browser** — view Claude Code skills from `~/.claude/skills/`
- **VPS Status** — uptime, memory, disk, load average dashboard

## Stack

- **Backend**: FastAPI + uvicorn (Python 3.8+)
- **Frontend**: Single HTML file — Tailwind CSS CDN + Alpine.js CDN
- **Auth**: API key via `X-API-Key` header, stored in localStorage

## Quick Start (VPS)

### 1. Clone / copy files

```bash
mkdir -p /opt/tify-commandcenter
cd /opt/tify-commandcenter
# copy all files here
```

### 2. Configure environment

```bash
cp .env.example .env
nano .env   # Set COMMAND_CENTER_KEY to a strong secret
```

### 3. Deploy

```bash
bash deploy.sh
```

The deploy script will:
1. Copy files to `/opt/tify-commandcenter/`
2. Install Python requirements into the existing bot venv
3. Install and enable the systemd service
4. Start the service and print the access URL

### Manual start (dev)

```bash
pip install fastapi uvicorn python-multipart
COMMAND_CENTER_KEY=mysecret python main.py
```

Then open `http://localhost:8080` and enter your API key.

## VPS Requirements

The dashboard assumes this directory layout already exists:

```
/opt/telegram-claude-bot/
├── bot.py
├── venv/
├── config-general.json
├── config-product.json
├── config-finance.json
└── instances/
    ├── general/
    │   ├── memory/6165792902.json
    │   └── output/
    ├── product/
    │   ├── memory/6165792902.json
    │   └── output/
    └── finance/
        ├── memory/6165792902.json
        └── output/
```

Systemd services named `bot-{instance_id}` (e.g. `bot-general`, `bot-product`, `bot-finance`).

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/agents` | List all agents with systemd status |
| POST | `/api/agents` | Create new agent + systemd service |
| PUT | `/api/agents/{id}` | Update agent config + restart |
| DELETE | `/api/agents/{id}` | Stop service, delete config & files |
| POST | `/api/agents/{id}/restart` | Restart systemd service |
| GET | `/api/agents/{id}/logs` | Last 50 journalctl lines |
| GET | `/api/agents/{id}/memory` | Read owner memory file |
| DELETE | `/api/agents/{id}/memory` | Delete memory file |
| GET | `/api/cron?agent={id}` | List cron jobs for agent |
| POST | `/api/cron` | Add cron job |
| PUT | `/api/cron/{index}` | Edit cron job |
| DELETE | `/api/cron/{index}` | Delete cron job |
| GET | `/api/agents/{id}/files` | List output files |
| GET | `/api/agents/{id}/files/download` | Download file |
| GET | `/api/skills` | List Claude skills |
| GET | `/api/models` | Claude + Ollama models |
| GET | `/api/status` | VPS uptime/memory/disk/load |
| GET | `/api/presets` | Agent preset prompts |

## Security Notes

- Set a strong `COMMAND_CENTER_KEY` before exposing to the internet
- The service runs as root to manage systemd — restrict network access if possible
- Consider putting nginx in front with HTTPS
