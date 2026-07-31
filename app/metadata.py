from __future__ import annotations

from pathlib import Path


PRODUCT_NAME = "Gestion Fiduciaria Server Manager"
APP_VERSION = "4.0.0"
APP_DESCRIPTION = (
    "Herramienta de instalacion y administracion diaria para el servidor de Gestion Fiduciaria."
)
APP_AUTHOR = "Constructora Centenario S.A.S."
APP_YEAR = "2026"
APP_LICENSE = "Uso interno"
APP_TECHNOLOGIES = ["Python", "Tkinter", "Django", "PostgreSQL", "Waitress"]


def app_icon_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "app.ico"
