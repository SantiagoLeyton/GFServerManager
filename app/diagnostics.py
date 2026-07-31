from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config_manager import CONFIG_PATH, load_config, try_reconstruct_config, validate_config
from .database_manager import test_connection
from .django_manager import python_executable
from .environment_manager import env_database_credentials, is_env_valid, read_env
from .logging_config import logs_dir
from .metadata import APP_VERSION
from .project_validator import validate_project
from .server_manager import SERVER_RUNNING, SERVER_STOPPED, get_server_status


LOGGER = logging.getLogger(__name__)
@dataclass
class DiagnosticItem:
    name: str
    level: str
    message: str


@dataclass
class DiagnosticReport:
    generated_at: str
    version: str
    items: list[DiagnosticItem]

    @property
    def overall_level(self) -> str:
        levels = {item.level for item in self.items}
        if "error" in levels:
            return "error"
        if "warning" in levels:
            return "warning"
        return "ok"

    @property
    def overall_message(self) -> str:
        if self.overall_level == "ok":
            return "Sistema correcto."
        if self.overall_level == "warning":
            return "Sistema operativo con advertencias."
        return "Sistema con errores que requieren atencion."


def run_diagnostics(config: dict | None = None) -> DiagnosticReport:
    config = config if config is not None else load_config()
    if config is None and not CONFIG_PATH.exists():
        config = try_reconstruct_config()
    items: list[DiagnosticItem] = []

    config_errors = validate_config(config)
    if config_errors:
        config_message = "; ".join(config_errors)
        config_level = "error"
    else:
        readable = os.access(CONFIG_PATH, os.R_OK)
        writable = os.access(CONFIG_PATH, os.W_OK)
        config_level = "ok" if readable and writable else "warning"
        config_message = "Configuración local legible." if readable and writable else "Configuración local sin permisos completos de lectura/escritura."
    items.append(_item("Configuración", config_level, config_message))

    project_path = Path(str((config or {}).get("project_path", "")))
    venv_path = Path(str((config or {}).get("venv_path", "")))

    _check_project(items, project_path)
    _check_venv(items, venv_path)
    _check_python(items, venv_path)
    _check_waitress(items, venv_path)
    _check_env(items, project_path)
    _check_server_status(items, config or {})
    _check_postgres(items, project_path)
    _check_database(items, project_path)
    _check_general(items)

    return DiagnosticReport(
        generated_at=datetime.now().isoformat(timespec="seconds"),
        version=APP_VERSION,
        items=items,
    )


def export_diagnostics(report: DiagnosticReport) -> Path:
    path = logs_dir() / f"diagnostico_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    lines = [
        "Gestion Fiduciaria Server Manager - Diagnostico",
        f"Fecha y hora: {report.generated_at}",
        f"Version: {report.version}",
        f"Estado general: {report.overall_message}",
        "",
        "Resultados:",
    ]
    for item in report.items:
        lines.append(f"- {item.name}: {item.level.upper()} - {item.message}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    LOGGER.info("Diagnostico exportado en %s", path)
    return path


def level_icon(level: str) -> str:
    return {"ok": "🟢", "warning": "🟡", "error": "🔴"}.get(level, "🟡")


def _check_project(items: list[DiagnosticItem], project_path: Path) -> None:
    if not project_path:
        items.append(_item("Proyecto", "error", "No hay ruta de proyecto configurada."))
        return
    try:
        inspection = validate_project(project_path)
    except Exception:
        LOGGER.exception("Fallo al validar proyecto")
        items.append(_item("Proyecto", "error", "No se pudo validar el proyecto."))
        return
    items.append(_item("Proyecto", "ok" if inspection.valid else "error", "Proyecto válido." if inspection.valid else "; ".join(inspection.errors)))


def _check_venv(items: list[DiagnosticItem], venv_path: Path) -> None:
    python = python_executable(venv_path)
    if python.exists():
        items.append(_item("Entorno virtual", "ok", str(venv_path)))
    else:
        items.append(_item("Entorno virtual", "error", "No se encontro python.exe del entorno virtual."))


def _check_python(items: list[DiagnosticItem], venv_path: Path) -> None:
    python = python_executable(venv_path)
    if not python.exists():
        items.append(_item("Python", "error", "Python del entorno virtual no disponible."))
        return
    try:
        result = subprocess.run([str(python), "--version"], capture_output=True, text=True, timeout=10)
        version = (result.stdout or result.stderr).strip()
        items.append(_item("Python", "ok" if result.returncode == 0 else "error", version or "Sin salida de version."))
    except Exception:
        LOGGER.exception("No se pudo ejecutar Python del entorno virtual")
        items.append(_item("Python", "error", "No se pudo ejecutar Python del entorno virtual."))


def _check_waitress(items: list[DiagnosticItem], venv_path: Path) -> None:
    python = python_executable(venv_path)
    if not python.exists():
        items.append(_item("Waitress", "error", "No se puede comprobar sin entorno virtual."))
        return
    try:
        result = subprocess.run(
            [str(python), "-c", "import waitress; print(getattr(waitress, '__version__', 'Waitress disponible'))"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout or result.stderr).strip()
        items.append(_item("Waitress", "ok" if result.returncode == 0 else "error", output or "Waitress disponible."))
    except Exception:
        LOGGER.exception("No se pudo comprobar Waitress")
        items.append(_item("Waitress", "error", "Waitress no disponible o no ejecutable."))


def _check_env(items: list[DiagnosticItem], project_path: Path) -> None:
    env_path = project_path / ".env"
    if not env_path.exists():
        items.append(_item("Archivo .env", "error", "No existe .env. La instalacion esta incompleta."))
        return
    try:
        if not os.access(env_path, os.R_OK):
            items.append(_item("Archivo .env", "error", "No hay permisos de lectura sobre .env."))
            return
        values = read_env(project_path)
        valid = is_env_valid(project_path)
        missing = [
            key
            for key in ["DJANGO_SECRET_KEY", "DJANGO_DEBUG", "DJANGO_ALLOWED_HOSTS", "DB_NAME", "DB_USER", "DB_HOST", "DB_PORT"]
            if not values.get(key)
        ]
        writable = os.access(env_path, os.W_OK)
        if valid and writable:
            items.append(_item("Archivo .env", "ok", "Variables minimas presentes."))
        elif valid:
            items.append(_item("Archivo .env", "warning", "Variables minimas presentes, pero sin permiso de escritura."))
        else:
            items.append(_item("Archivo .env", "error", "Faltan: " + ", ".join(missing)))
    except Exception:
        LOGGER.exception("No se pudo leer .env")
        items.append(_item("Archivo .env", "error", "No se pudo leer .env."))


def _check_server_status(items: list[DiagnosticItem], config: dict) -> None:
    status = get_server_status(config)
    if status.state == SERVER_RUNNING:
        state_level = "ok"
        port_level = "ok"
        process_level = "ok"
    elif status.state == SERVER_STOPPED:
        state_level = "warning"
        port_level = "warning"
        process_level = "warning"
    else:
        state_level = "error"
        port_level = "error" if status.conflict_message else "warning"
        process_level = "warning"

    items.append(_item("Estado", state_level, status.state))
    items.append(_item("Puerto", port_level, status.port_message))
    items.append(_item("Proceso Waitress", process_level, status.process_message))


def _check_postgres(items: list[DiagnosticItem], project_path: Path) -> None:
    try:
        result = test_connection(env_database_credentials(project_path))
    except Exception:
        LOGGER.exception("No se pudo preparar prueba PostgreSQL")
        items.append(_item("PostgreSQL", "error", "No se pudo preparar la prueba de conexion."))
        return
    level = "ok" if result.ok else "error"
    elapsed = f" ({result.elapsed_ms} ms)" if result.elapsed_ms is not None else ""
    items.append(_item("PostgreSQL", level, result.message + elapsed))


def _check_database(items: list[DiagnosticItem], project_path: Path) -> None:
    try:
        credentials = env_database_credentials(project_path)
        if credentials.database:
            items.append(_item("Base de datos", "ok", f"Base configurada: {credentials.database}; usuario: {credentials.user or 'No disponible'}."))
        else:
            items.append(_item("Base de datos", "error", "DB_NAME no esta configurado."))
    except Exception:
        LOGGER.exception("No se pudo leer configuracion de base de datos")
        items.append(_item("Base de datos", "error", "No se pudo leer configuracion de base de datos."))


def _check_general(items: list[DiagnosticItem]) -> None:
    levels = {item.level for item in items if item.name != "Estado general"}
    if "error" in levels:
        items.append(_item("Estado general", "error", "Hay errores que requieren correccion."))
    elif "warning" in levels:
        items.append(_item("Estado general", "warning", "Hay advertencias pendientes."))
    else:
        items.append(_item("Estado general", "ok", "Sistema correcto."))


def _item(name: str, level: str, message: str) -> DiagnosticItem:
    return DiagnosticItem(name=name, level=level, message=message or "No disponible")
