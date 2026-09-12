from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.backup_task import (
    TASK_NAME,
    build_backup_task_command,
    install_backup_task,
    query_backup_task,
    run_backup_check_now,
    validate_backup_task_for_config,
)
from app.diagnostics import DiagnosticItem, _check_backup_task


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


class BackupTaskTests(unittest.TestCase):
    def test_build_command_uses_absolute_venv_python_manage_py_and_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))

            command = build_backup_task_command(project, venv)

            self.assertEqual(command.python, python.resolve())
            self.assertEqual(command.manage_py, (project / "manage.py").resolve())
            self.assertEqual(command.working_directory, project.resolve())
            self.assertIn("run_daily_backup_check", command.arguments)

    def test_install_backup_task_creates_enabled_hourly_system_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))
            captured_xml = {}

            def fake_run_hidden(command, **_kwargs):
                captured_xml["text"] = Path(command[command.index("/XML") + 1]).read_text(encoding="utf-16")
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("app.backup_task.run_hidden", side_effect=fake_run_hidden) as run_hidden:
                with mock.patch("app.backup_task.mark_backup_automation_active") as mark_active:
                    status = install_backup_task(project, venv)

            command = run_hidden.call_args.args[0]
            self.assertEqual(command[:5], ["schtasks", "/Create", "/TN", TASK_NAME, "/XML"])
            self.assertIn("/F", command)
            self.assertTrue(status.exists)
            self.assertTrue(status.enabled)
            self.assertIn("<UserId>S-1-5-18</UserId>", captured_xml["text"])
            self.assertIn("<Interval>PT1H</Interval>", captured_xml["text"])
            self.assertIn(f"<Command>{python.resolve()}</Command>", captured_xml["text"])
            self.assertIn("<WorkingDirectory>", captured_xml["text"])
            mark_active.assert_called_once_with(project, venv)

    def test_install_backup_task_is_idempotent_and_does_not_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            result = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch("app.backup_task.run_hidden", return_value=result) as run_hidden:
                with mock.patch("app.backup_task.mark_backup_automation_active"):
                    install_backup_task(project, venv)
                    install_backup_task(project, venv)

            calls = [call.args[0] for call in run_hidden.call_args_list]
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(command[command.index("/TN") + 1] == TASK_NAME for command in calls))
            self.assertTrue(all("/F" in command for command in calls))

    def test_install_backup_task_updates_paths_when_project_location_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_project, old_venv, _old_python = _make_project(root, "OldPagosFiducia")
            new_project, new_venv, new_python = _make_project(root, "NewPagosFiducia")
            captured_xml = []

            def fake_run_hidden(command, **_kwargs):
                captured_xml.append(Path(command[command.index("/XML") + 1]).read_text(encoding="utf-16"))
                return mock.Mock(returncode=0, stdout="", stderr="")

            with mock.patch("app.backup_task.run_hidden", side_effect=fake_run_hidden):
                with mock.patch("app.backup_task.mark_backup_automation_active"):
                    install_backup_task(old_project, old_venv)
                    install_backup_task(new_project, new_venv)

            self.assertIn(str(new_python.resolve()), captured_xml[-1])
            self.assertIn(str(new_project.resolve()), captured_xml[-1])

    def test_install_backup_task_failure_is_controlled_and_does_not_mark_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            result = mock.Mock(returncode=1, stdout="", stderr="Access denied")
            with mock.patch("app.backup_task.run_hidden", return_value=result):
                with mock.patch("app.backup_task.mark_backup_automation_active") as mark_active:
                    with mock.patch("app.backup_task.LOGGER.error"):
                        with self.assertRaisesRegex(RuntimeError, "No se pudo crear"):
                            install_backup_task(project, venv)
            mark_active.assert_not_called()

    def test_query_backup_task_reads_enabled_paths_from_xml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            command = build_backup_task_command(project, venv)
            from app.backup_task import _task_xml

            result = mock.Mock(returncode=0, stdout=_task_xml(command), stderr="")
            with mock.patch("app.backup_task.run_hidden", return_value=result):
                status = query_backup_task()

            self.assertTrue(status.exists)
            self.assertTrue(status.enabled)
            self.assertEqual(status.python, command.python)
            self.assertEqual(status.manage_py, command.manage_py)
            self.assertEqual(status.working_directory, command.working_directory)

    def test_validate_backup_task_detects_mismatched_existing_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, _python = _make_project(Path(temp_dir))
            other_project, other_venv, _other_python = _make_project(Path(temp_dir), "Other")
            from app.backup_task import _task_xml

            other_command = build_backup_task_command(other_project, other_venv)
            result = mock.Mock(returncode=0, stdout=_task_xml(other_command), stderr="")
            with mock.patch("app.backup_task.run_hidden", return_value=result):
                status = validate_backup_task_for_config(project, venv)

            self.assertTrue(status.exists)
            self.assertIn("requiere reparacion", status.message)

    def test_run_backup_check_now_uses_same_real_command_and_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))
            result = mock.Mock(returncode=0, stdout="Sin cambios.", stderr="")
            with mock.patch("app.backup_task.run_hidden", return_value=result) as run_hidden:
                output = run_backup_check_now(project, venv)

            command = run_hidden.call_args.args[0]
            self.assertEqual(command, [str(python.resolve()), str((project / "manage.py").resolve()), "run_daily_backup_check"])
            self.assertEqual(run_hidden.call_args.kwargs["cwd"], project.resolve())
            self.assertEqual(output, "Sin cambios.")

    def test_backup_task_diagnostic_reports_expected_items(self) -> None:
        items: list[DiagnosticItem] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            project, venv, python = _make_project(Path(temp_dir))
            storage = Path(temp_dir) / "Backups"
            storage.mkdir()
            with mock.patch("app.diagnostics.read_env", return_value={"BACKUP_STORAGE_PATH": str(storage)}):
                with mock.patch("app.diagnostics.detect_postgres_tools", return_value=mock.Mock(pg_dump=Path("pg_dump.exe"))):
                    with mock.patch(
                        "app.diagnostics.validate_backup_task_for_config",
                        return_value=mock.Mock(
                            exists=True,
                            enabled=True,
                            message="Tarea de backups activa y apuntando a la instalacion configurada.",
                            python=python,
                            manage_py=project / "manage.py",
                            working_directory=project,
                        ),
                    ):
                        _check_backup_task(items, project, venv)

        names = [item.name for item in items]
        self.assertIn("Backups", names)
        self.assertIn("pg_dump", names)
        self.assertIn("Tarea de backups", names)
        self.assertTrue(all(item.level == "ok" for item in items))


if __name__ == "__main__":
    unittest.main()
