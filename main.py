"""
Tify Agent Command Center — FastAPI Backend
Runs on VPS port 8080, manages Telegram Claude bots as systemd services.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_BOT_DIR = "/opt/telegram-claude-bot"
OWNER_ID = 6165792902
PROTECTED_AGENTS = {"general"}

CLAUDE_MODELS: Dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-7",
    "haiku": "claude-haiku-4-5-20251001",
}

AGENT_PRESETS: Dict[str, str] = {
    "general": (
        "Kamu adalah asisten AI pribadi yang membantu. "
        "Jawab ringkas dan jelas dalam Bahasa Indonesia."
    ),
    "social": (
        "Kamu adalah ahli Social Media Marketing. "
        "Bantu buat konten, strategi, caption, dan analisis tren. "
        "Jawab dalam Bahasa Indonesia."
    ),
    "product": (
        "Kamu adalah Product Manager berpengalaman. "
        "Bantu dengan product roadmap, user stories, prioritisasi fitur. "
        "Jawab dalam Bahasa Indonesia."
    ),
    "dev": (
        "Kamu adalah Software Engineer senior. "
        "Bantu dengan coding, debugging, arsitektur sistem. "
        "Jawab dalam Bahasa Indonesia."
    ),
    "finance": (
        "Kamu adalah analis keuangan berpengalaman. "
        "Bantu dengan budgeting, analisis bisnis, laporan keuangan. "
        "Jawab dalam Bahasa Indonesia."
    ),
    "marketing": (
        "Kamu adalah Digital Marketing Strategist. "
        "Bantu dengan kampanye, SEO, copywriting. "
        "Jawab dalam Bahasa Indonesia."
    ),
    "research": (
        "Kamu adalah Research Analyst. "
        "Bantu dengan riset pasar, analisis data. "
        "Jawab dalam Bahasa Indonesia."
    ),
}

SYSTEMD_SERVICE_TEMPLATE = """\
[Unit]
Description=Claude Bot - {bot_name}
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/telegram-claude-bot
ExecStart=/opt/telegram-claude-bot/venv/bin/python bot.py /opt/telegram-claude-bot/config-{instance_id}.json
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

# ---------------------------------------------------------------------------
# App + Auth
# ---------------------------------------------------------------------------

app = FastAPI(title="Tify Agent Command Center", version="1.0.0")

API_KEY = os.getenv("COMMAND_CENTER_KEY", "change-this-secret-key")


def verify_api_key(request: Request) -> None:
    key = request.headers.get("X-API-Key", "")
    if key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AgentCreate(BaseModel):
    instance_id: str
    bot_name: str
    bot_token: str
    preset: str = "general"
    model: str = "sonnet"
    model_type: str = "claude"
    system_prompt: Optional[str] = None


class AgentUpdate(BaseModel):
    bot_name: Optional[str] = None
    bot_token: Optional[str] = None
    preset: Optional[str] = None
    model: Optional[str] = None
    model_type: Optional[str] = None
    system_prompt: Optional[str] = None


class CronJobCreate(BaseModel):
    schedule: str          # "0 9 * * *"
    command: str
    agent: str


class CronJobUpdate(BaseModel):
    schedule: Optional[str] = None
    command: Optional[str] = None
    agent: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_path(instance_id: str) -> Path:
    return Path(BASE_BOT_DIR) / f"config-{instance_id}.json"


def _read_config(instance_id: str) -> Dict[str, Any]:
    p = _config_path(instance_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{instance_id}' not found")
    with open(p) as f:
        return json.load(f)


def _write_config(instance_id: str, data: Dict[str, Any]) -> None:
    p = _config_path(instance_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


def _service_name(instance_id: str) -> str:
    return f"bot-{instance_id}"


def _systemd_status(instance_id: str) -> str:
    """Returns 'active', 'inactive', 'failed', or 'unknown'."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", _service_name(instance_id)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _systemctl(action: str, instance_id: str) -> Dict[str, Any]:
    svc = _service_name(instance_id)
    try:
        result = subprocess.run(
            ["systemctl", action, svc],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {"success": result.returncode == 0, "output": result.stdout + result.stderr}
    except Exception as e:
        return {"success": False, "output": str(e)}


def _create_systemd_service(instance_id: str, bot_name: str) -> None:
    content = SYSTEMD_SERVICE_TEMPLATE.format(
        bot_name=bot_name, instance_id=instance_id
    )
    service_file = Path(f"/etc/systemd/system/bot-{instance_id}.service")
    service_file.write_text(content)
    subprocess.run(["systemctl", "daemon-reload"], timeout=10)
    subprocess.run(["systemctl", "enable", f"bot-{instance_id}"], timeout=10)


def _remove_systemd_service(instance_id: str) -> None:
    svc = _service_name(instance_id)
    subprocess.run(["systemctl", "stop", svc], timeout=15)
    subprocess.run(["systemctl", "disable", svc], timeout=10)
    service_file = Path(f"/etc/systemd/system/bot-{instance_id}.service")
    if service_file.exists():
        service_file.unlink()
    subprocess.run(["systemctl", "daemon-reload"], timeout=10)


def _list_configs() -> List[str]:
    base = Path(BASE_BOT_DIR)
    if not base.exists():
        return []
    return [
        p.stem.removeprefix("config-")
        for p in base.glob("config-*.json")
    ]


# ---------------------------------------------------------------------------
# Cron helpers
# ---------------------------------------------------------------------------

CRON_TAG_RE = re.compile(r"^#\s*agent:(\S+)\s*$")


def _read_crontab() -> List[str]:
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()
    except Exception:
        return []


def _write_crontab(lines: List[str]) -> None:
    content = "\n".join(lines) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cron", delete=False) as f:
        f.write(content)
        tmp = f.name
    subprocess.run(["crontab", tmp], timeout=5)
    os.unlink(tmp)


def _parse_cron_jobs(lines: List[str]) -> List[Dict[str, Any]]:
    """Parse crontab lines into job dicts, pairing agent tags with their job line."""
    jobs = []
    pending_agent = None
    raw_index = 0
    for line in lines:
        m = CRON_TAG_RE.match(line)
        if m:
            pending_agent = m.group(1)
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            jobs.append(
                {
                    "index": raw_index,
                    "agent": pending_agent or "",
                    "raw": stripped,
                    "schedule": " ".join(stripped.split()[:5]),
                    "command": " ".join(stripped.split()[5:]),
                }
            )
            raw_index += 1
            pending_agent = None
        else:
            pending_agent = None
    return jobs


def _rebuild_crontab_lines(jobs: List[Dict[str, Any]]) -> List[str]:
    lines = []
    for job in jobs:
        if job.get("agent"):
            lines.append(f"# agent:{job['agent']}")
        lines.append(f"{job['schedule']} {job['command']}")
    return lines


# ---------------------------------------------------------------------------
# AGENTS endpoints
# ---------------------------------------------------------------------------


@app.get("/api/agents", dependencies=[Depends(verify_api_key)])
def list_agents():
    ids = _list_configs()
    agents = []
    for iid in ids:
        try:
            cfg = _read_config(iid)
        except Exception:
            continue
        cfg["status"] = _systemd_status(iid)
        agents.append(cfg)
    return agents


@app.post("/api/agents", dependencies=[Depends(verify_api_key)])
def create_agent(body: AgentCreate):
    iid = body.instance_id.strip().lower()
    if not re.match(r"^[a-z0-9_-]+$", iid):
        raise HTTPException(400, "instance_id must be lowercase alphanumeric/dash/underscore")
    if _config_path(iid).exists():
        raise HTTPException(409, f"Agent '{iid}' already exists")

    cfg: Dict[str, Any] = {
        "instance_id": iid,
        "bot_name": body.bot_name,
        "bot_token": body.bot_token,
        "preset": body.preset,
        "owner_id": OWNER_ID,
        "model": body.model,
        "model_type": body.model_type,
    }
    if body.system_prompt:
        cfg["system_prompt"] = body.system_prompt

    _write_config(iid, cfg)

    # Create instance directories
    for subdir in ["memory", "output"]:
        Path(BASE_BOT_DIR, "instances", iid, subdir).mkdir(parents=True, exist_ok=True)

    # Create & start systemd service
    try:
        _create_systemd_service(iid, body.bot_name)
        _systemctl("start", iid)
    except Exception as e:
        return {"success": True, "warning": f"Config saved but systemd error: {e}", "agent": cfg}

    return {"success": True, "agent": cfg}


@app.put("/api/agents/{agent_id}", dependencies=[Depends(verify_api_key)])
def update_agent(agent_id: str, body: AgentUpdate):
    cfg = _read_config(agent_id)
    if body.bot_name is not None:
        cfg["bot_name"] = body.bot_name
    if body.bot_token is not None:
        cfg["bot_token"] = body.bot_token
    if body.preset is not None:
        cfg["preset"] = body.preset
    if body.model is not None:
        cfg["model"] = body.model
    if body.model_type is not None:
        cfg["model_type"] = body.model_type
    if body.system_prompt is not None:
        cfg["system_prompt"] = body.system_prompt

    _write_config(agent_id, cfg)

    # Update service description if name changed
    if body.bot_name is not None:
        try:
            _create_systemd_service(agent_id, cfg["bot_name"])
        except Exception:
            pass

    _systemctl("restart", agent_id)
    return {"success": True, "agent": cfg}


@app.delete("/api/agents/{agent_id}", dependencies=[Depends(verify_api_key)])
def delete_agent(agent_id: str):
    if agent_id in PROTECTED_AGENTS:
        raise HTTPException(403, f"Cannot delete protected agent '{agent_id}'")
    _read_config(agent_id)  # 404 if not found

    try:
        _remove_systemd_service(agent_id)
    except Exception:
        pass

    _config_path(agent_id).unlink(missing_ok=True)

    instance_dir = Path(BASE_BOT_DIR, "instances", agent_id)
    if instance_dir.exists():
        shutil.rmtree(instance_dir)

    return {"success": True, "deleted": agent_id}


@app.post("/api/agents/{agent_id}/restart", dependencies=[Depends(verify_api_key)])
def restart_agent(agent_id: str):
    _read_config(agent_id)  # 404 check
    result = _systemctl("restart", agent_id)
    return result


@app.get("/api/agents/{agent_id}/logs", dependencies=[Depends(verify_api_key)])
def get_agent_logs(agent_id: str):
    _read_config(agent_id)  # 404 check
    svc = _service_name(agent_id)
    try:
        result = subprocess.run(
            ["journalctl", "-u", svc, "-n", "50", "--no-pager", "--output=short-iso"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {"logs": result.stdout or result.stderr or "(no logs)"}
    except Exception as e:
        return {"logs": f"Error fetching logs: {e}"}


# ---------------------------------------------------------------------------
# MEMORY endpoints
# ---------------------------------------------------------------------------


def _memory_path(agent_id: str) -> Path:
    return Path(BASE_BOT_DIR, "instances", agent_id, "memory", f"{OWNER_ID}.json")


@app.get("/api/agents/{agent_id}/memory", dependencies=[Depends(verify_api_key)])
def get_memory(agent_id: str):
    _read_config(agent_id)
    mp = _memory_path(agent_id)
    if not mp.exists():
        return {"messages": [], "count": 0, "exists": False}
    with open(mp) as f:
        data = json.load(f)
    messages = data if isinstance(data, list) else data.get("messages", [])
    return {"messages": messages, "count": len(messages), "exists": True}


@app.delete("/api/agents/{agent_id}/memory", dependencies=[Depends(verify_api_key)])
def clear_memory(agent_id: str):
    _read_config(agent_id)
    mp = _memory_path(agent_id)
    if mp.exists():
        mp.unlink()
    return {"success": True, "deleted": str(mp)}


# ---------------------------------------------------------------------------
# CRON endpoints
# ---------------------------------------------------------------------------


@app.get("/api/cron", dependencies=[Depends(verify_api_key)])
def list_cron(agent: Optional[str] = Query(default=None)):
    lines = _read_crontab()
    jobs = _parse_cron_jobs(lines)
    if agent:
        jobs = [j for j in jobs if j["agent"] == agent]
    return {"jobs": jobs}


@app.post("/api/cron", dependencies=[Depends(verify_api_key)])
def add_cron(body: CronJobCreate):
    lines = _read_crontab()
    jobs = _parse_cron_jobs(lines)
    new_job = {
        "index": len(jobs),
        "agent": body.agent,
        "schedule": body.schedule,
        "command": body.command,
        "raw": f"{body.schedule} {body.command}",
    }
    jobs.append(new_job)
    _write_crontab(_rebuild_crontab_lines(jobs))
    return {"success": True, "job": new_job}


@app.put("/api/cron/{index}", dependencies=[Depends(verify_api_key)])
def update_cron(index: int, body: CronJobUpdate):
    lines = _read_crontab()
    jobs = _parse_cron_jobs(lines)
    if index < 0 or index >= len(jobs):
        raise HTTPException(404, f"Cron job index {index} not found")
    job = jobs[index]
    if body.schedule is not None:
        job["schedule"] = body.schedule
    if body.command is not None:
        job["command"] = body.command
    if body.agent is not None:
        job["agent"] = body.agent
    job["raw"] = f"{job['schedule']} {job['command']}"
    jobs[index] = job
    _write_crontab(_rebuild_crontab_lines(jobs))
    return {"success": True, "job": job}


@app.delete("/api/cron/{index}", dependencies=[Depends(verify_api_key)])
def delete_cron(index: int):
    lines = _read_crontab()
    jobs = _parse_cron_jobs(lines)
    if index < 0 or index >= len(jobs):
        raise HTTPException(404, f"Cron job index {index} not found")
    removed = jobs.pop(index)
    _write_crontab(_rebuild_crontab_lines(jobs))
    return {"success": True, "removed": removed}


# ---------------------------------------------------------------------------
# FILES endpoints
# ---------------------------------------------------------------------------


def _output_dir(agent_id: str) -> Path:
    return Path(BASE_BOT_DIR, "instances", agent_id, "output")


@app.get("/api/agents/{agent_id}/files", dependencies=[Depends(verify_api_key)])
def list_files(agent_id: str, path: str = Query(default="")):
    _read_config(agent_id)
    base = _output_dir(agent_id)
    target = (base / path).resolve() if path else base.resolve()

    # Security: prevent path traversal
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")

    if not target.exists():
        return {"files": []}

    files = []
    for item in sorted(target.iterdir()):
        stat = item.stat()
        files.append(
            {
                "name": item.name,
                "path": str(item.relative_to(base)),
                "is_dir": item.is_dir(),
                "size": stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return {"files": files}


@app.get("/api/agents/{agent_id}/files/download", dependencies=[Depends(verify_api_key)])
def download_file(agent_id: str, path: str = Query(...)):
    _read_config(agent_id)
    base = _output_dir(agent_id)
    target = (base / path).resolve()

    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")

    if not target.exists() or target.is_dir():
        raise HTTPException(404, "File not found")

    return FileResponse(path=str(target), filename=target.name)


# ---------------------------------------------------------------------------
# SKILLS endpoint
# ---------------------------------------------------------------------------


@app.get("/api/skills", dependencies=[Depends(verify_api_key)])
def list_skills():
    skills_dir = Path.home() / ".claude" / "skills"
    if not skills_dir.exists():
        return {"skills": []}

    skills = []
    for item in sorted(skills_dir.iterdir()):
        if not item.is_dir():
            continue
        description = ""
        skill_md = item / "SKILL.md"
        if skill_md.exists():
            with open(skill_md) as f:
                first_line = f.readline().strip()
                description = first_line.lstrip("#").strip()
        skills.append({"name": item.name, "description": description, "path": str(item)})
    return {"skills": skills}


# ---------------------------------------------------------------------------
# MODELS endpoint
# ---------------------------------------------------------------------------


@app.get("/api/models", dependencies=[Depends(verify_api_key)])
def list_models():
    import urllib.error
    import urllib.request

    ollama_models: List[str] = []
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as resp:
            data = json.loads(resp.read())
            ollama_models = [m["name"] for m in data.get("models", [])]
    except Exception:
        ollama_models = []

    return {
        "claude": CLAUDE_MODELS,
        "ollama": ollama_models,
    }


# ---------------------------------------------------------------------------
# STATUS endpoint
# ---------------------------------------------------------------------------


@app.get("/api/status", dependencies=[Depends(verify_api_key)])
def get_status():
    # Uptime
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.read().split()[0])
        days = int(seconds // 86400)
        hours = int((seconds % 86400) // 3600)
        minutes = int((seconds % 3600) // 60)
        uptime = f"{days}d {hours}h {minutes}m"
    except Exception:
        uptime = "unknown"

    # Memory
    mem = {"total": 0, "used": 0, "free": 0, "percent": 0}
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    meminfo[parts[0].rstrip(":")] = int(parts[1])
        total = meminfo.get("MemTotal", 0)
        available = meminfo.get("MemAvailable", 0)
        used = total - available
        mem = {
            "total": total * 1024,
            "used": used * 1024,
            "free": available * 1024,
            "percent": round((used / total) * 100, 1) if total else 0,
        }
    except Exception:
        pass

    # Disk
    disk = {"total": 0, "used": 0, "free": 0, "percent": 0}
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        disk = {
            "total": total,
            "used": used,
            "free": free,
            "percent": round((used / total) * 100, 1) if total else 0,
        }
    except Exception:
        pass

    # Load average
    load = [0.0, 0.0, 0.0]
    try:
        load = list(os.getloadavg())
    except Exception:
        pass

    # Agents summary
    ids = _list_configs()
    agents_summary = []
    for iid in ids:
        try:
            cfg = _read_config(iid)
            agents_summary.append(
                {
                    "instance_id": iid,
                    "bot_name": cfg.get("bot_name", iid),
                    "status": _systemd_status(iid),
                }
            )
        except Exception:
            continue

    return {
        "uptime": uptime,
        "memory": mem,
        "disk": disk,
        "load": {"1m": load[0], "5m": load[1], "15m": load[2]},
        "agents": agents_summary,
    }


# ---------------------------------------------------------------------------
# PRESETS endpoint
# ---------------------------------------------------------------------------


@app.get("/api/presets", dependencies=[Depends(verify_api_key)])
def get_presets():
    return {"presets": AGENT_PRESETS}


# ---------------------------------------------------------------------------
# Static files + SPA fallback
# ---------------------------------------------------------------------------

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/")
def serve_index():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse({"detail": "Frontend not found"}, status_code=404)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
