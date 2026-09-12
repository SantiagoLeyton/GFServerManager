from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.backup_task import build_backup_task_command
from app.config_manager import is_config_complete
from app.django_manager import project_python_executable, require_project_python
from app.startup_task import build_startup_task_command


def _make_project(root: Path, name: str = "PagosFiducia") -> Path:
    project = root / name
    scripts = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_text("", encoding="utf-8")
    (project / "manage.py").write_text("", encoding="utf-8")
    return project


class ProjectPythonResolutionTests(unittest.TestCase):
    def test_project_path_resolves_project_venv_python(self) -> None:
        project = Path("C:/GestionFiduciaria/PagosFiducia")

        python = project_python_executable(project)

        self.assertEqual(python, project / ".venv" / "Scripts" / "python.exe")

    def test_different_project_path_gets_its_own_venv_python(self) -> None:
        project = Path("D:/Apps/Gestion/PagosFiducia")

        python = project_python_executable(project)

        self.assertEqual(python, project / ".venv" / "Scripts" / "python.exe")

    def test_missing_project_python_fails_with_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "PagosFiducia"
            project.mkdir()
            expected = project / ".venv" / "Scripts" / "python.exe"

            with self.assertRaisesRegex(RuntimeError, "No se encontró el entorno virtual"):
                require_project_python(project)

            try:
                require_project_python(project)
            except RuntimeError as exc:
                self.assertIn(str(expected), str(exc))

    def test_startup_task_ignores_stale_configured_venv_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = _make_project(root)
            stale_venv = root / "OldPythonFromConfig"

            command = build_startup_task_command(project, stale_venv, port=8123)

            self.assertEqual(command.python, (project / ".venv" / "Scripts" / "python.exe").resolve())

    def test_backup_task_ignores_stale_configured_venv_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = _make_project(root)
            stale_venv = root / "OldPythonFromConfig"

            command = build_backup_task_command(project, stale_venv)

            self.assertEqual(command.python, (project / ".venv" / "Scripts" / "python.exe").resolve())

    def test_complete_config_uses_project_venv_even_if_stored_venv_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = _make_project(root)
            (project / ".env").write_text("DJANGO_SECRET_KEY=x\n", encoding="utf-8")
            stale_venv = root / "OldUserProfile" / "AppData" / "Local" / "Programs" / "Python"
            config_path = root / "server_manager.json"
            config_path.write_text("{}", encoding="utf-8")
            config = {
                "project_path": str(project),
                "venv_path": str(stale_venv),
                "wsgi_module": "config.wsgi:application",
                "host": "0.0.0.0",
                "port": 8000,
                "installation_status": "installed",
            }

            with mock.patch("app.config_manager.CONFIG_PATH", config_path):
                self.assertTrue(is_config_complete(config))


if __name__ == "__main__":
    unittest.main()
