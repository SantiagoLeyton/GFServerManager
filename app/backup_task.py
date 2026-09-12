from __future__ import annotations

import logging
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .django_manager import django_shell, require_project_python
from .subprocess_utils import run_hidden


LOGGER = logging.getLogger(__name__)
TASK_NAME = "GestionFiduciaria-BackupCheck"
TASK_AUTHOR = "Gestion Fiduciaria Server Manager"
TASK_DESCRIPTION = "Ejecuta la comprobacion periodica de backups de Gestion Fiduciaria."
TASK_NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"
TASK_REPETITION_INTERVAL = "PT1M"


@dataclass(frozen=True)
class BackupTaskCommand:
    python: Path
    manage_py: Path
    working_directory: Path
    arguments: str


@dataclass(frozen=True)
class BackupTaskStatus:
    exists: bool
    enabled: bool
    message: str
    python: Path | None = None
    manage_py: Path | None = None
    working_directory: Path | None = None


def build_backup_task_command(project_path: Path, venv_path: Path) -> BackupTaskCommand:
    project = project_path.resolve()
    python = require_project_python(project).resolve()
    manage_py = (project / "manage.py").resolve()
    if not manage_py.exists():
        raise RuntimeError(f"No existe manage.py: {manage_py}")
    return BackupTaskCommand(
        python=python,
        manage_py=manage_py,
        working_directory=project,
        arguments=f'"{manage_py}" run_daily_backup_check',
    )


def install_backup_task(project_path: Path, venv_path: Path) -> BackupTaskStatus:
    command = build_backup_task_command(project_path, venv_path)
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
            LOGGER.warning("No se pudo eliminar XML temporal de tarea de backup: %s", xml_path)
    if result.returncode != 0:
        output = _combined_output(result)
        LOGGER.error("No se pudo crear tarea programada %s: %s", TASK_NAME, output)
        raise RuntimeError(
            "No se pudo crear la tarea programada de backups. Ejecute GFServerManager como administrador."
        )
    mark_backup_automation_active(project_path, venv_path)
    LOGGER.info(
        "Tarea programada de backups configurada: name=%s python=%s manage_py=%s cwd=%s run_as=SYSTEM interval=%s",
        TASK_NAME,
        command.python,
        command.manage_py,
        command.working_directory,
        TASK_REPETITION_INTERVAL,
    )
    return BackupTaskStatus(
        exists=True,
        enabled=True,
        message=f"Automatizacion de backups activa: {TASK_NAME}.",
        python=command.python,
        manage_py=command.manage_py,
        working_directory=command.working_directory,
    )


def ensure_backup_task_for_config(project_path: Path, venv_path: Path) -> BackupTaskStatus:
    status = validate_backup_task_for_config(project_path, venv_path)
    if status.exists and status.enabled:
        mark_backup_automation_active(project_path, venv_path)
        return status
    LOGGER.warning("Tarea de backups ausente o requiere reparacion: %s", status.message)
    return install_backup_task(project_path, venv_path)


def query_backup_task() -> BackupTaskStatus:
    result = run_hidden(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], timeout=15)
    if result.returncode != 0:
        output = _combined_output(result)
        if _task_missing_output(output):
            return BackupTaskStatus(False, False, "Automatizacion de backups pendiente de configuracion.")
        LOGGER.warning("No se pudo consultar tarea de backups: %s", output)
        return BackupTaskStatus(False, False, "No se pudo consultar la automatizacion de backups.")
    try:
        return _status_from_xml(result.stdout)
    except Exception:
        LOGGER.exception("No se pudo interpretar XML de tarea de backups")
        return BackupTaskStatus(True, False, "La tarea de backups existe, pero no se pudo validar su configuracion.")


def run_backup_check_now(project_path: Path, venv_path: Path) -> str:
    command = build_backup_task_command(project_path, venv_path)
    result = run_hidden(
        [str(command.python), str(command.manage_py), "run_daily_backup_check"],
        cwd=command.working_directory,
        timeout=300,
    )
    output = _combined_output(result) or "Comprobacion finalizada sin salida."
    if result.returncode != 0:
        raise RuntimeError(output)
    return output


def mark_backup_automation_active(project_path: Path, venv_path: Path) -> None:
    code = r"""
from core.models import BackupSettings
settings_obj = BackupSettings.get_solo()
settings_obj.automation_status = BackupSettings.AutomationStatus.ACTIVE
settings_obj.save(update_fields=["automation_status", "updated_at"])
print("Automatizacion activa.")
"""
    django_shell(project_path, venv_path, code)


def validate_backup_task_for_config(project_path: Path, venv_path: Path) -> BackupTaskStatus:
    expected = build_backup_task_command(project_path, venv_path)
    status = query_backup_task()
    if not status.exists:
        return status
    errors: list[str] = []
    if not status.enabled:
        errors.append("la tarea esta deshabilitada")
    if status.python != expected.python:
        errors.append("python.exe no coincide con la instalacion configurada")
    if status.manage_py != expected.manage_py:
        errors.append("manage.py no coincide con la instalacion configurada")
    if status.working_directory != expected.working_directory:
        errors.append("working directory no coincide con la instalacion configurada")
    for label, path in [
        ("python.exe", status.python),
        ("manage.py", status.manage_py),
        ("working directory", status.working_directory),
    ]:
        if not path or not path.exists():
            errors.append(f"{label} no existe")
    if errors:
        return BackupTaskStatus(
            exists=True,
            enabled=status.enabled,
            message="Tarea de backups requiere reparacion: " + "; ".join(errors) + ".",
            python=status.python,
            manage_py=status.manage_py,
            working_directory=status.working_directory,
        )
    return BackupTaskStatus(
        exists=True,
        enabled=True,
        message="Tarea de backups activa y apuntando a la instalacion configurada.",
        python=status.python,
        manage_py=status.manage_py,
        working_directory=status.working_directory,
    )


def _task_xml(command: BackupTaskCommand) -> str:
    start_boundary = datetime.now().strftime("%Y-%m-%dT00:00:00")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="{TASK_NAMESPACE}">
  <RegistrationInfo>
    <Author>{TASK_AUTHOR}</Author>
    <Description>{TASK_DESCRIPTION}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start_boundary}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>{TASK_REPETITION_INTERVAL}</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
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
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
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


def _status_from_xml(xml_text: str) -> BackupTaskStatus:
    root = ET.fromstring(xml_text)
    ns = {"task": TASK_NAMESPACE}
    enabled_text = _find_text(root, "task:Settings/task:Enabled", ns)
    command_text = _find_text(root, "task:Actions/task:Exec/task:Command", ns)
    args_text = _find_text(root, "task:Actions/task:Exec/task:Arguments", ns)
    cwd_text = _find_text(root, "task:Actions/task:Exec/task:WorkingDirectory", ns)
    python = Path(command_text).resolve() if command_text else None
    manage_py = _extract_manage_py(args_text)
    cwd = Path(cwd_text).resolve() if cwd_text else None
    enabled = (enabled_text or "true").strip().lower() == "true"
    return BackupTaskStatus(
        exists=True,
        enabled=enabled,
        message="Automatizacion de backups activa." if enabled else "Automatizacion de backups deshabilitada.",
        python=python,
        manage_py=manage_py,
        working_directory=cwd,
    )


def _find_text(root: ET.Element, path: str, ns: dict[str, str]) -> str:
    item = root.find(path, ns)
    return item.text.strip() if item is not None and item.text else ""


def _extract_manage_py(arguments: str) -> Path | None:
    arguments = (arguments or "").strip()
    if not arguments:
        return None
    if arguments.startswith('"'):
        end = arguments.find('"', 1)
        if end > 1:
            return Path(arguments[1:end]).resolve()
    first = arguments.split(" ", 1)[0]
    return Path(first).resolve() if first else None


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
