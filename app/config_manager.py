from __future__ import annotations

import json
import socket
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_config import app_root


CONFIG_PATH = app_root() / "data" / "server_manager.json"


@dataclass
class ServerConfig:
    project_path: str
    venv_path: str
    wsgi_module: str
    host: str
    port: int
    ipv4: str
    hostname: str
    configured_at: str
    installation_status: str
    pid: int | None = None


def detect_hostname() -> str:
    return socket.gethostname()


def detect_local_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def save_config(config: ServerConfig) -> None:
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2, ensure_ascii=False), encoding="utf-8")


def load_config() -> dict[str, Any] | None:
    if not CONFIG_PATH.exists():
        return None
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def update_config(values: dict[str, Any]) -> dict[str, Any]:
    config = load_config() or {}
    config.update(values)
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return config


def is_config_complete(config: dict[str, Any] | None) -> bool:
    if not config:
        return False
    required = ["project_path", "venv_path", "wsgi_module", "host", "port", "installation_status"]
    if any(not config.get(key) for key in required):
        return False
    if config.get("installation_status") != "installed":
        return False
    project_path = Path(config["project_path"])
    venv_path = Path(config["venv_path"])
    return (
        project_path.exists()
        and (project_path / "manage.py").exists()
        and (project_path / ".env").exists()
        and venv_path.exists()
        and (venv_path / "Scripts" / "python.exe").exists()
    )


def build_config(project_path: Path, venv_path: Path, port: int, pid: int | None = None) -> ServerConfig:
    return ServerConfig(
        project_path=str(project_path.resolve()),
        venv_path=str(venv_path.resolve()),
        wsgi_module="config.wsgi:application",
        host="0.0.0.0",
        port=port,
        ipv4=detect_local_ipv4(),
        hostname=detect_hostname(),
        configured_at=datetime.now().isoformat(timespec="seconds"),
        installation_status="installed",
        pid=pid,
    )
