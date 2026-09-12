from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.backup_task import TASK_NAME as BACKUP_TASK_NAME
from app.diagnostics import DiagnosticItem, _check_postgres, _check_startup_task
from app.server_manager import start_waitress
from app.startup_task import (
    BOOT_DELAY,
    RESTART_COUNT,
    RESTART_INTERVAL,
    TASK_NAME,
    build_startup_task_command,
    install_startup_task,
    query_startup_task,
    validate_startup_task_for_config,
)


def _make_project(root: Path, name: str = "PagosFiducia") -> tuple[Path, Path, Path]:
    project = root / name
    venv = project / ".venv"
    scripts = venv / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    manage_py = project / "manage.py"
    python.write_text("", encoding="utf-8")
    manage_py.write_text("", encoding="utf-8")
    return project, venv, python


class StartupTaskTests(unittest.TestCase):
    def test_build_command_uses_absolute_venv_python_waitress_and_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))

            command = build_startup_task_command(project, venv, host="0.0.0.0", port=8123)

            self.assertEqual(command.python, python.resolve())
            self.assertEqual(command.working_directory, project.resolve())
            self.assertEqual(command.arguments, "-m waitress --host=0.0.0.0 --port=8123 config.wsgi:application")

    def test_install_startup_task_creates_enabled_boot_system_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))
            captured_xml = {}

            def fake_run_hidden(command, **_kwargs):
                captured_xml["text"] = Path(command[command.index("/XML") + 1]).read_text(encoding="utf-16")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("app.startup_task.run_hidden", side_effect=fake_run_hidden) as run_hidden:
                status = install_startup_task(project, venv, port=8123)

            command = run_hidden.call_args.args[0]
            self.assertEqual(command[:5], ["schtasks", "/Create", "/TN", TASK_NAME, "/XML"])
            self.assertIn("/F", command)
            self.assertTrue(status.exists)
            self.assertTrue(status.enabled)
            self.assertIn("<BootTrigger>", captured_xml["text"])
            self.assertIn(f"<Delay>{BOOT_DELAY}</Delay>", captured_xml["text"])
            self.assertIn("<UserId>S-1-5-18</UserId>", captured_xml["text"])
            self.assertIn("<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>", captured_xml["text"])
            self.assertIn(f"<Interval>{RESTART_INTERVAL}</Interval>", captured_xml["text"])
            self.assertIn(f"<Count>{RESTART_COUNT}</Count>", captured_xml["text"])
            self.assertIn("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", captured_xml["text"])
            self.assertIn(f"<Command>{python.resolve()}</Command>", captured_xml["text"])
            self.assertIn("<Arguments>-m waitress --host=0.0.0.0 --port=8123 config.wsgi:application</Arguments>", captured_xml["text"])
            self.assertIn(f"<WorkingDirectory>{project.resolve()}</WorkingDirectory>", captured_xml["text"])

    def test_install_startup_task_is_idempotent_and_does_not_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            result = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch("app.startup_task.run_hidden", return_value=result) as run_hidden:
                install_startup_task(project, venv)
                install_startup_task(project, venv)

            calls = [call.args[0] for call in run_hidden.call_args_list]
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(command[command.index("/TN") + 1] == TASK_NAME for command in calls))
            self.assertTrue(all("/F" in command for command in calls))

    def test_install_startup_task_updates_paths_when_project_location_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_project, old_venv, _old_python = _make_project(root, "OldPagosFiducia")
            new_project, new_venv, new_python = _make_project(root, "NewPagosFiducia")
            captured_xml = []

            def fake_run_hidden(command, **_kwargs):
                captured_xml.append(Path(command[command.index("/XML") + 1]).read_text(encoding="utf-16"))
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("app.startup_task.run_hidden", side_effect=fake_run_hidden):
                install_startup_task(old_project, old_venv)
                install_startup_task(new_project, new_venv)

            self.assertIn(str(new_python.resolve()), captured_xml[-1])
            self.assertIn(str(new_project.resolve()), captured_xml[-1])
            self.assertNotIn(str(old_project.resolve()), captured_xml[-1])

    def test_install_startup_task_failure_is_controlled(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            result = mock.Mock(returncode=1, stdout="", stderr="Access denied")
            with mock.patch("app.startup_task.run_hidden", return_value=result):
                with mock.patch("app.startup_task.LOGGER.error"):
                    with self.assertRaisesRegex(RuntimeError, "No se pudo crear"):
                        install_startup_task(project, venv)

    def test_query_startup_task_reads_boot_system_waitress_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            command = build_startup_task_command(project, venv, host="127.0.0.1", port=9000)
            from app.startup_task import _task_xml

            result = mock.Mock(returncode=0, stdout=_task_xml(command), stderr="")
            with mock.patch("app.startup_task.run_hidden", return_value=result):
                status = query_startup_task()

            self.assertTrue(status.exists)
            self.assertTrue(status.enabled)
            self.assertTrue(status.boot_trigger)
            self.assertTrue(status.run_as_system)
            self.assertEqual(status.python, command.python)
            self.assertEqual(status.working_directory, command.working_directory)
            self.assertEqual(status.host, "127.0.0.1")
            self.assertEqual(status.port, 9000)
            self.assertEqual(status.wsgi_module, "config.wsgi:application")

    def test_validate_startup_task_detects_missing_disabled_or_mismatched_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            other_project, other_venv, _other_python = _make_project(Path(temp_dir), "Other")
            from app.startup_task import _task_xml

            other_command = build_startup_task_command(other_project, other_venv, port=9999)
            xml_text = _task_xml(other_command).replace(
                "    <Enabled>true</Enabled>\n    <Hidden>true</Hidden>",
                "    <Enabled>false</Enabled>\n    <Hidden>true</Hidden>",
            )
            result = mock.Mock(returncode=0, stdout=xml_text, stderr="")
            with mock.patch("app.startup_task.run_hidden", return_value=result):
                status = validate_startup_task_for_config(project, venv, port=8000)

            self.assertTrue(status.exists)
            self.assertFalse(status.enabled)
            self.assertIn("requiere reparacion", status.message)
            self.assertIn("python.exe no coincide", status.message)
            self.assertIn("la tarea esta deshabilitada", status.message)

    def test_validate_startup_task_reports_missing_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            result = mock.Mock(returncode=1, stdout="", stderr="ERROR: no existe")
            with mock.patch("app.startup_task.run_hidden", return_value=result):
                status = validate_startup_task_for_config(project, venv)

            self.assertFalse(status.exists)
            self.assertEqual(status.message, "Autoarranque pendiente de configuracion.")

    def test_startup_task_diagnostic_reports_configured_task(self) -> None:
        items: list[DiagnosticItem] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))
            with mock.patch(
                "app.diagnostics.validate_startup_task_for_config",
                return_value=mock.Mock(
                    exists=True,
                    enabled=True,
                    message="Tarea de servidor activa y apuntando a la instalacion configurada.",
                    python=python,
                    working_directory=project,
                ),
            ):
                _check_startup_task(items, project, venv, {"host": "0.0.0.0", "port": 8000})

        self.assertEqual(items[0].name, "Autoarranque")
        self.assertEqual(items[0].level, "ok")

    def test_startup_task_diagnostic_detects_disabled_task(self) -> None:
        items: list[DiagnosticItem] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            with mock.patch(
                "app.diagnostics.validate_startup_task_for_config",
                return_value=mock.Mock(
                    exists=True,
                    enabled=False,
                    message="Tarea de servidor requiere reparacion: la tarea esta deshabilitada.",
                ),
            ):
                _check_startup_task(items, project, venv, {"host": "0.0.0.0", "port": 8000})

        self.assertEqual(items[0].level, "error")
        self.assertIn("deshabilitada", items[0].message)

    def test_postgresql_unavailable_is_reported_without_traceback_to_user(self) -> None:
        items: list[DiagnosticItem] = []
        result = mock.Mock(ok=False, message="PostgreSQL no disponible.", elapsed_ms=None)
        with mock.patch("app.diagnostics.env_database_credentials", return_value=mock.Mock()):
            with mock.patch("app.diagnostics.test_connection", return_value=result):
                _check_postgres(items, Path("PagosFiducia"))

        self.assertEqual(items[0].name, "PostgreSQL")
        self.assertEqual(items[0].level, "error")
        self.assertEqual(items[0].message, "PostgreSQL no disponible.")

    def test_backup_task_name_is_not_modified_by_server_startup_task(self) -> None:
        self.assertEqual(TASK_NAME, "GestionFiduciaria-Servidor")
        self.assertEqual(BACKUP_TASK_NAME, "GestionFiduciaria-BackupCheck")
        self.assertNotEqual(TASK_NAME, BACKUP_TASK_NAME)


class ServerStartBehaviorTests(unittest.TestCase):
    def test_manual_start_does_not_launch_duplicate_when_waitress_is_already_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            with mock.patch("app.server_manager.find_waitress_listener_pid", return_value=4321):
                with mock.patch("app.server_manager.popen_hidden") as popen_hidden:
                    with self.assertRaisesRegex(RuntimeError, "ya esta ejecutandose"):
                        start_waitress(project, venv, 8000)

            popen_hidden.assert_not_called()

    def test_manual_start_uses_venv_python_waitress_and_project_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))
            process = mock.Mock(pid=1234)
            with mock.patch("app.server_manager.find_waitress_listener_pid", return_value=None):
                with mock.patch("app.server_manager.port_conflict_message", return_value=None):
                    with mock.patch("app.server_manager.is_port_available", return_value=True):
                        with mock.patch("app.server_manager._open_waitress_log", return_value=mock.Mock()):
                            with mock.patch("app.server_manager.popen_hidden", return_value=process) as popen_hidden:
                                started = start_waitress(project, venv, 8123, host="0.0.0.0")

            self.assertEqual(started, process)
            command = popen_hidden.call_args.args[0]
            self.assertEqual(command, [str(python.resolve()), "-m", "waitress", "--host=0.0.0.0", "--port=8123", "config.wsgi:application"])
            self.assertEqual(popen_hidden.call_args.kwargs["cwd"], project)


if __name__ == "__main__":
    unittest.main()
