from __future__ import annotations

import logging
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .django_manager import require_project_python
from .server_manager import DEFAULT_WSGI_MODULE
from .subprocess_utils import run_hidden


LOGGER = logging.getLogger(__name__)
TASK_NAME = "GestionFiduciaria-Servidor"
TASK_AUTHOR = "Gestion Fiduciaria Server Manager"
TASK_DESCRIPTION = "Inicia Waitress de Gestion Fiduciaria al arrancar Windows."
TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
BOOT_DELAY = "PT60S"
RESTART_INTERVAL = "PT1M"
RESTART_COUNT = 3


@dataclass(frozen=True)
class StartupTaskCommand:
    python: Path
    working_directory: Path
    host: str
    port: int
    wsgi_module: str
    arguments: str


@dataclass(frozen=True)
class StartupTaskStatus:
    exists: bool
    enabled: bool
    message: str
    python: Path | None = None
    working_directory: Path | None = None
    host: str | None = None
    port: int | None = None
    wsgi_module: str | None = None
    boot_trigger: bool = False
    run_as_system: bool = False


def build_startup_task_command(
    project_path: Path,
    venv_path: Path,
    host: str = "0.0.0.0",
    port: int = 8000,
    wsgi_module: str = DEFAULT_WSGI_MODULE,
) -> StartupTaskCommand:
    project = project_path.resolve()
    if not project.exists() or not project.is_dir():
        raise RuntimeError(f"No existe la carpeta de Gestion Fiduciaria: {project}")
    python = require_project_python(project).resolve()
    return StartupTaskCommand(
        python=python,
        working_directory=project,
        host=str(host or "0.0.0.0"),
        port=int(port),
        wsgi_module=str(wsgi_module or DEFAULT_WSGI_MODULE),
        arguments=f"-m waitress --host={host or '0.0.0.0'} --port={int(port)} {wsgi_module or DEFAULT_WSGI_MODULE}",
    )


def install_startup_task(
    project_path: Path,
    venv_path: Path,
    host: str = "0.0.0.0",
    port: int = 8000,
    wsgi_module: str = DEFAULT_WSGI_MODULE,
) -> StartupTaskStatus:
    command = build_startup_task_command(project_path, venv_path, host=host, port=port, wsgi_module=wsgi_module)
    xml_text = _task_xml(command)
    with tempfile.NamedTemporaryFile("w", encoding="utf-16", suffix=".xml", delete=False) as temp:
        xml_path = Path(temp.name)
        temp.write(xml_text)
    try:
        result = run_hidden(
            [
                "schtasks",
                "/Create",
                "/TN",
                TASK_NAME,
                "/XML",
                str(xml_path),
                "/F",
            ],
            timeout=30,
        )
    finally:
        try:
            xml_path.unlink()
        except OSError:
            LOGGER.warning("No se pudo eliminar XML temporal de tarea de servidor: %s", xml_path)
    if result.returncode != 0:
        output = _combined_output(result)
        LOGGER.error("No se pudo crear tarea programada %s: %s", TASK_NAME, output)
        raise RuntimeError(
            "No se pudo crear la tarea programada de inicio del servidor. Ejecute GFServerManager como administrador."
        )
    LOGGER.info(
        "Tarea programada de servidor configurada: name=%s python=%s args=%s cwd=%s trigger=ONSTART delay=%s run_as=SYSTEM",
        TASK_NAME,
        command.python,
        command.arguments,
        command.working_directory,
        BOOT_DELAY,
    )
    return StartupTaskStatus(
        exists=True,
        enabled=True,
        message=f"Autoarranque activo: {TASK_NAME}.",
        python=command.python,
        working_directory=command.working_directory,
        host=command.host,
        port=command.port,
        wsgi_module=command.wsgi_module,
        boot_trigger=True,
        run_as_system=True,
    )


def query_startup_task() -> StartupTaskStatus:
    result = run_hidden(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], timeout=15)
    if result.returncode != 0:
        output = _combined_output(result)
        if _task_missing_output(output):
            return StartupTaskStatus(False, False, "Autoarranque pendiente de configuracion.")
        LOGGER.warning("No se pudo consultar la tarea programada de servidor: %s", output)
        return StartupTaskStatus(False, False, "No se pudo consultar el autoarranque.")
    try:
        return _status_from_xml(result.stdout)
    except Exception:
        LOGGER.exception("No se pudo interpretar XML de tarea de servidor")
        return StartupTaskStatus(True, False, "La tarea de servidor existe, pero no se pudo validar su configuracion.")


def validate_startup_task_for_config(
    project_path: Path,
    venv_path: Path,
    host: str = "0.0.0.0",
    port: int = 8000,
    wsgi_module: str = DEFAULT_WSGI_MODULE,
) -> StartupTaskStatus:
    expected = build_startup_task_command(project_path, venv_path, host=host, port=port, wsgi_module=wsgi_module)
    status = query_startup_task()
    if not status.exists:
        return status
    errors: list[str] = []
    if not status.enabled:
        errors.append("la tarea esta deshabilitada")
    if not status.boot_trigger:
        errors.append("el trigger no es al iniciar Windows")
    if not status.run_as_system:
        errors.append("no se ejecuta como SYSTEM")
    if status.python != expected.python:
        errors.append("python.exe no coincide con la instalacion configurada")
    if status.working_directory != expected.working_directory:
        errors.append("working directory no coincide con la instalacion configurada")
    if status.host != expected.host:
        errors.append("host Waitress no coincide")
    if status.port != expected.port:
        errors.append("puerto Waitress no coincide")
    if status.wsgi_module != expected.wsgi_module:
        errors.append("modulo WSGI no coincide")
    for label, path in [("python.exe", status.python), ("working directory", status.working_directory)]:
        if not path or not path.exists():
            errors.append(f"{label} no existe")
    if errors:
        return StartupTaskStatus(
            exists=True,
            enabled=status.enabled,
            message="Tarea de servidor requiere reparacion: " + "; ".join(errors) + ".",
            python=status.python,
            working_directory=status.working_directory,
            host=status.host,
            port=status.port,
            wsgi_module=status.wsgi_module,
            boot_trigger=status.boot_trigger,
            run_as_system=status.run_as_system,
        )
    return StartupTaskStatus(
        exists=True,
        enabled=True,
        message="Tarea de servidor activa y apuntando a la instalacion configurada.",
        python=status.python,
        working_directory=status.working_directory,
        host=status.host,
        port=status.port,
        wsgi_module=status.wsgi_module,
        boot_trigger=True,
        run_as_system=True,
    )


def remove_startup_task() -> StartupTaskStatus:
    result = run_hidden(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], timeout=30)
    if result.returncode == 0:
        LOGGER.info("Tarea programada eliminada: %s", TASK_NAME)
        return StartupTaskStatus(False, False, "Autoarranque desactivado.")
    output = _combined_output(result)
    if _task_missing_output(output):
        return StartupTaskStatus(False, False, "Autoarranque ya estaba desactivado.")
    LOGGER.error("No se pudo eliminar tarea programada %s: %s", TASK_NAME, output)
    raise RuntimeError("No se pudo eliminar la tarea programada. Ejecute GFServerManager como administrador.")


def _task_xml(command: StartupTaskCommand) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="{TASK_NAMESPACE}">
  <RegistrationInfo>
    <Author>{TASK_AUTHOR}</Author>
    <Description>{TASK_DESCRIPTION}</Description>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>{BOOT_DELAY}</Delay>
    </BootTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>S-1-5-18</UserId>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <RestartOnFailure>
      <Interval>{RESTART_INTERVAL}</Interval>
      <Count>{RESTART_COUNT}</Count>
    </RestartOnFailure>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml_escape(str(command.python))}</Command>
      <Arguments>{_xml_escape(command.arguments)}</Arguments>
      <WorkingDirectory>{_xml_escape(str(command.working_directory))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _status_from_xml(xml_text: str) -> StartupTaskStatus:
    root = ET.fromstring(xml_text)
    ns = {"task": TASK_NAMESPACE}
    enabled_text = _find_text(root, "task:Settings/task:Enabled", ns)
    command_text = _find_text(root, "task:Actions/task:Exec/task:Command", ns)
    args_text = _find_text(root, "task:Actions/task:Exec/task:Arguments", ns)
    cwd_text = _find_text(root, "task:Actions/task:Exec/task:WorkingDirectory", ns)
    user_id = _find_text(root, "task:Principals/task:Principal/task:UserId", ns)
    boot_trigger = root.find("task:Triggers/task:BootTrigger", ns) is not None
    host, port, wsgi_module = _extract_waitress_args(args_text)
    enabled = (enabled_text or "true").strip().lower() == "true"
    return StartupTaskStatus(
        exists=True,
        enabled=enabled,
        message="Autoarranque activo." if enabled else "Autoarranque deshabilitado.",
        python=Path(command_text).resolve() if command_text else None,
        working_directory=Path(cwd_text).resolve() if cwd_text else None,
        host=host,
        port=port,
        wsgi_module=wsgi_module,
        boot_trigger=boot_trigger,
        run_as_system=user_id.strip().upper() == "S-1-5-18",
    )


def _extract_waitress_args(arguments: str) -> tuple[str | None, int | None, str | None]:
    parts = (arguments or "").split()
    host = None
    port = None
    module = None
    index = 0
    while index < len(parts):
        part = parts[index]
        if part.startswith("--host="):
            host = part.split("=", 1)[1]
        elif part == "--host" and index + 1 < len(parts):
            index += 1
            host = parts[index]
        elif part.startswith("--port="):
            port = _safe_int(part.split("=", 1)[1])
        elif part == "--port" and index + 1 < len(parts):
            index += 1
            port = _safe_int(parts[index])
        elif part.endswith(":application"):
            module = part
        index += 1
    return host, port, module


def _find_text(root: ET.Element, path: str, ns: dict[str, str]) -> str:
    item = root.find(path, ns)
    return item.text.strip() if item is not None and item.text else ""


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


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
