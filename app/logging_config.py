from __future__ import annotations

import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .metadata import COMPANY_NAME, TECHNICAL_NAME


_MIGRATED = False


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return source_root()


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def app_data_dir() -> Path:
    program_data = os.environ.get("PROGRAMDATA")
    base = Path(program_data) if program_data else Path(os.environ.get("ALLUSERSPROFILE", r"C:\ProgramData"))
    path = base / COMPANY_NAME.replace(" ", "") / TECHNICAL_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_app_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / COMPANY_NAME.replace(" ", "") / TECHNICAL_NAME


def data_dir() -> Path:
    path = app_data_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_legacy_data() -> list[str]:
    global _MIGRATED
    if _MIGRATED:
        return []
    _MIGRATED = True
    messages: list[str] = []
    local_root = legacy_app_data_dir()
    repo_root = source_root()
    migrations = [
        (local_root / "data" / "server_manager.json", data_dir() / "server_manager.json"),
        (local_root / "logs" / "server_manager.log", logs_dir() / "server_manager.log"),
        (repo_root / "data" / "server_manager.json", data_dir() / "server_manager.json"),
        (repo_root / "logs" / "server_manager.log", logs_dir() / "server_manager.log"),
    ]
    for source, target in migrations:
        try:
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                messages.append(f"Archivo persistente migrado desde {source} hacia {target}")
        except OSError as exc:
            messages.append(f"No se pudo migrar archivo persistente desde {source}: {exc}")
    return messages


def configure_logging() -> None:
    migration_messages = migrate_legacy_data()
    log_path = logs_dir() / "server_manager.log"

    formatter = logging.Formatter("%(levelname)s %(asctime)s %(name)s %(message)s")
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    for message in migration_messages:
        root_logger.info(message)
