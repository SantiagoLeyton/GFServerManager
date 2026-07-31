from __future__ import annotations

import json
import logging
import socket
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .logging_config import app_root


CONFIG_PATH = app_root() / "data" / "server_manager.json"
LOGGER = logging.getLogger(__name__)


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
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.exception("server_manager.json esta corrupto")
        return None
    except OSError:
        LOGGER.exception("No se pudo leer server_manager.json")
        return None
    if not isinstance(data, dict):
        LOGGER.error("server_manager.json no contiene un objeto JSON")
        return None
    return data


def update_config(values: dict[str, Any]) -> dict[str, Any]:
    config = load_config() or {}
    config.update(values)
    CONFIG_PATH.parent.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    return config


def validate_config(config: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    if not CONFIG_PATH.exists():
        return ["No existe data/server_manager.json."]
    if config is None:
        return ["La configuracion local no se pudo leer o esta corrupta."]
    required = ["project_path", "venv_path", "wsgi_module", "host", "port", "installation_status"]
    for key in required:
        if not config.get(key):
            errors.append(f"Falta {key}.")
    try:
        port = int(config.get("port", 0))
        if port < 1024 or port > 65535:
            errors.append("El puerto configurado esta fuera del rango valido.")
    except (TypeError, ValueError):
        errors.append("El puerto configurado no es numerico.")
    project_path = Path(str(config.get("project_path", "")))
    venv_path = Path(str(config.get("venv_path", "")))
    if config.get("project_path") and not project_path.exists():
        errors.append("La ruta del proyecto no existe.")
    if config.get("venv_path") and not venv_path.exists():
        errors.append("La ruta del entorno virtual no existe.")
    return errors


def is_config_complete(config: dict[str, Any] | None) -> bool:
    if validate_config(config):
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


def try_reconstruct_config(search_root: Path | None = None) -> dict[str, Any] | None:
    root = search_root or app_root().parent
    candidates = [root / "PagosFiducia", root / "GestionFiduciaria"]
    for project_path in candidates:
        venv_path = project_path / ".venv"
        if (
            (project_path / "manage.py").exists()
            and (project_path / "config" / "wsgi.py").exists()
            and (project_path / ".env").exists()
            and (venv_path / "Scripts" / "python.exe").exists()
        ):
            config = build_config(project_path, venv_path, 8000, pid=None)
            save_config(config)
            LOGGER.warning("server_manager.json fue reconstruido desde %s", project_path)
            return load_config()
    LOGGER.warning("No se pudo reconstruir server_manager.json automaticamente")
    return None


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
