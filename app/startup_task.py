from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from .subprocess_utils import run_hidden


LOGGER = logging.getLogger(__name__)
TASK_NAME = "GestionFiduciaria-Servidor"


@dataclass(frozen=True)
class StartupTaskStatus:
    exists: bool
    message: str


def default_executable_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    dist_exe = Path(__file__).resolve().parent.parent / "dist" / "GFServerManager" / "GFServerManager.exe"
    if dist_exe.exists():
        return dist_exe.resolve()
    return Path(sys.executable).resolve()


def query_startup_task() -> StartupTaskStatus:
    result = run_hidden(["schtasks", "/Query", "/TN", TASK_NAME], timeout=15)
    if result.returncode == 0:
        return StartupTaskStatus(True, "Inicio automatico activo.")
    output = _combined_output(result)
    if _task_missing_output(output):
        return StartupTaskStatus(False, "Inicio automatico desactivado.")
    LOGGER.warning("No se pudo consultar la tarea programada: %s", output)
    return StartupTaskStatus(False, "No se pudo consultar el inicio automatico.")


def install_startup_task(executable_path: Path | None = None) -> StartupTaskStatus:
    exe = (executable_path or default_executable_path()).resolve()
    if not exe.exists():
        raise RuntimeError(f"No existe el ejecutable para configurar inicio automatico: {exe}")
    command = f'"{exe}" --startup'
    result = run_hidden(
        [
            "schtasks",
            "/Create",
            "/TN",
            TASK_NAME,
            "/SC",
            "ONSTART",
            "/RU",
            "SYSTEM",
            "/TR",
            command,
            "/RL",
            "HIGHEST",
            "/F",
        ],
        timeout=30,
    )
    if result.returncode != 0:
        output = _combined_output(result)
        LOGGER.error("No se pudo crear tarea programada %s: %s", TASK_NAME, output)
        raise RuntimeError(
            "No se pudo crear la tarea programada. Ejecute el instalador o GFServerManager como administrador."
        )
    LOGGER.info(
        "Tarea programada creada: name=%s executable=%s args=--startup trigger=ONSTART run_as=SYSTEM",
        TASK_NAME,
        exe,
    )
    return StartupTaskStatus(True, f"Inicio automatico activo: {TASK_NAME}.")


def remove_startup_task() -> StartupTaskStatus:
    result = run_hidden(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=30)
    if result.returncode == 0:
        LOGGER.info("Tarea programada eliminada: %s", TASK_NAME)
        return StartupTaskStatus(False, "Inicio automatico desactivado.")
    output = _combined_output(result)
    if _task_missing_output(output):
        return StartupTaskStatus(False, "Inicio automatico ya estaba desactivado.")
    LOGGER.error("No se pudo eliminar tarea programada %s: %s", TASK_NAME, output)
    raise RuntimeError("No se pudo eliminar la tarea programada. Ejecute GFServerManager como administrador.")


def _combined_output(result) -> str:
    return "\n".join(part.strip() for part in [result.stdout or "", result.stderr or ""] if part and part.strip())


def _task_missing_output(output: str) -> bool:
    text = output.lower()
    return any(
        fragment in text
        for fragment in [
            "no existe",
            "cannot find",
            "no se encuentra",
            "no puede encontrar el archivo especificado",
        ]
    )
