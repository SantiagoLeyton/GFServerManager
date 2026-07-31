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
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    path = base / COMPANY_NAME.replace(" ", "") / TECHNICAL_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    path = app_data_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def migrate_legacy_data(logger: logging.Logger | None = None) -> None:
    global _MIGRATED
    if _MIGRATED:
        return
    _MIGRATED = True
    legacy_root = source_root()
    migrations = [
        (legacy_root / "data" / "server_manager.json", data_dir() / "server_manager.json"),
        (legacy_root / "logs" / "server_manager.log", logs_dir() / "server_manager.log"),
    ]
    for source, target in migrations:
        try:
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                if logger:
                    logger.info("Archivo persistente migrado desde %s hacia %s", source, target)
        except OSError:
            if logger:
                logger.exception("No se pudo migrar archivo persistente desde %s", source)


def configure_logging() -> None:
    log_path = logs_dir() / "server_manager.log"

    formatter = logging.Formatter("%(levelname)s %(asctime)s %(name)s %(message)s")
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    migrate_legacy_data(root_logger)
