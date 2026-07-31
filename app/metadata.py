from __future__ import annotations

import sys
from pathlib import Path


PRODUCT_NAME = "Gestión Fiduciaria Server Manager"
TECHNICAL_NAME = "GFServerManager"
COMPANY_NAME = "Constructora Centenario"
APP_VERSION = "1.0.0"
APP_DESCRIPTION = (
    "Herramienta de administración del servidor de Gestión Fiduciaria."
)
APP_AUTHOR = "Constructora Centenario"
APP_YEAR = "2026"
APP_LICENSE = "Uso interno"
APP_TECHNOLOGIES = ["Python", "Tkinter", "Django", "PostgreSQL", "Waitress"]


def app_icon_path() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets" / "app.ico"
    return Path(__file__).resolve().parent.parent / "assets" / "app.ico"
