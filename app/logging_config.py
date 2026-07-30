from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def configure_logging() -> None:
    logs_dir = app_root() / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_path = logs_dir / "server_manager.log"

    formatter = logging.Formatter("%(levelname)s %(asctime)s %(name)s %(message)s")
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

