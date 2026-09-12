from __future__ import annotations

import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from app import django_manager
from app.admin_panel import AdminPanel
from app.server_manager import (
    HttpHealth,
    SERVER_APP_ERROR,
    SERVER_RUNNING,
    ServerStatus,
    check_http_health,
    get_server_status,
)


def _status(running: bool = False, pid: int | None = None, conflict: str | None = None) -> ServerStatus:
    return ServerStatus(
        running=running,
        pid=pid,
        host="0.0.0.0",
        port=8123,
        listening=running or bool(conflict),
        uptime=None,
        state=SERVER_RUNNING if running else "Servidor detenido",
        port_message="Puerto 8123 escuchando." if running else "Puerto 8123 libre.",
        process_message=f"Waitress activo con PID {pid}." if running else "No existe un proceso Waitress activo.",
        conflict_message=conflict,
        app_responding=running,
        app_ok=running,
        http_status_code=200 if running else None,
        http_message="HTTP 200." if running else "Aplicacion no comprobada.",
    )


def _panel(temp_dir: Path) -> AdminPanel:
    panel = AdminPanel.__new__(AdminPanel)
    project = temp_dir / "PagosFiducia"
    venv = project / ".venv"
    panel.config = {
        "project_path": str(project),
        "venv_path": str(venv),
        "host": "0.0.0.0",
        "port": 8123,
        "wsgi_module": "config.wsgi:application",
        "pid": None,
    }
    panel._info = mock.Mock()
    panel._database_ok = mock.Mock(return_value=True)
    panel._is_managed_waitress = mock.Mock(return_value=True)
    return panel


class HttpHealthTests(unittest.TestCase):
    def test_http_500_is_application_error_not_stopped_server(self) -> None:
        error = urllib.error.HTTPError("http://127.0.0.1:8123/", 500, "boom", None, None)

        with mock.patch("app.server_manager.urllib.request.urlopen", side_effect=error):
            health = check_http_health(8123, timeout_seconds=1)

        self.assertTrue(health.responding)
        self.assertFalse(health.ok)
        self.assertEqual(health.status_code, 500)

    def test_server_status_distinguishes_waitress_running_with_http_500(self) -> None:
        with mock.patch("app.server_manager.find_waitress_listener_pid", return_value=4567):
            with mock.patch("app.server_manager._format_uptime", return_value="00:01:00"):
                with mock.patch(
                    "app.server_manager.check_http_health",
                    return_value=HttpHealth(True, False, 500, "HTTP 500: la aplicacion respondio con error."),
                ):
                    status = get_server_status({"project_path": "C:/PagosFiducia", "port": 8123})

        self.assertTrue(status.running)
        self.assertEqual(status.state, SERVER_APP_ERROR)
        self.assertFalse(status.app_ok)
        self.assertEqual(status.http_status_code, 500)


class AdminPanelServerWorkerTests(unittest.TestCase):
    def test_start_with_valid_configuration_waits_for_real_http_health(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            panel = _panel(Path(temp))
            process = mock.Mock(pid=4444)
            process.poll.return_value = None

            with mock.patch("app.admin_panel.get_server_status", side_effect=[_status(False), _status(True, 4444)]):
                with mock.patch.object(panel, "_validate_server_start_preconditions"):
                    with mock.patch("app.admin_panel.find_static_probe_url", return_value="/static/app.css"):
                        with mock.patch("app.admin_panel.start_waitress", return_value=process) as start_waitress:
                            with mock.patch("app.admin_panel.check_http_health", return_value=HttpHealth(True, True, 200, "HTTP 200.")):
                                with mock.patch("app.admin_panel.wait_for_static_file", return_value=True):
                                    with mock.patch("app.admin_panel.update_config", side_effect=lambda values: {**panel.config, **values}):
                                        panel._start_server_worker()

            start_waitress.assert_called_once()
            self.assertEqual(panel.config["pid"], 4444)

    def test_start_does_not_declare_success_when_waitress_dies_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            panel = _panel(Path(temp))
            process = mock.Mock(pid=4444)
            process.poll.return_value = 1

            with mock.patch("app.admin_panel.get_server_status", return_value=_status(False)):
                with mock.patch.object(panel, "_validate_server_start_preconditions"):
                    with mock.patch("app.admin_panel.find_static_probe_url", return_value="/static/app.css"):
                        with mock.patch("app.admin_panel.start_waitress", return_value=process):
                            with mock.patch("app.admin_panel.tail_waitress_log", return_value="ImportError: broken"):
                                with mock.patch("app.admin_panel.update_config", side_effect=lambda values: {**panel.config, **values}):
                                    with self.assertRaisesRegex(RuntimeError, "finalizo inmediatamente"):
                                        panel._start_server_worker()

            panel._info.assert_not_called()

    def test_start_reports_http_500_as_application_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            panel = _panel(Path(temp))
            process = mock.Mock(pid=4444)
            process.poll.return_value = None

            with mock.patch("app.admin_panel.get_server_status", side_effect=[_status(False), _status(True, 4444)]):
                with mock.patch.object(panel, "_validate_server_start_preconditions"):
                    with mock.patch("app.admin_panel.find_static_probe_url", return_value="/static/app.css"):
                        with mock.patch("app.admin_panel.start_waitress", return_value=process):
                            with mock.patch(
                                "app.admin_panel.check_http_health",
                                return_value=HttpHealth(True, False, 500, "HTTP 500: la aplicacion respondio con error."),
                            ):
                                with mock.patch("app.admin_panel.update_config", side_effect=lambda values: {**panel.config, **values}):
                                    with self.assertRaisesRegex(RuntimeError, "Django devolvio"):
                                        panel._start_server_worker()

            self.assertEqual(panel.config["pid"], 4444)

    def test_stop_only_managed_waitress_and_waits_for_port_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            panel = _panel(Path(temp))
            with mock.patch("app.admin_panel.get_server_status", return_value=_status(True, 5555)):
                with mock.patch("app.admin_panel.process_exists", return_value=True):
                    with mock.patch("app.admin_panel.stop_process", return_value=True) as stop_process:
                        with mock.patch("app.admin_panel.wait_for_port_release", return_value=True) as wait_release:
                            with mock.patch("app.admin_panel.update_config", side_effect=lambda values: {**panel.config, **values}):
                                panel._stop_server_worker()

            stop_process.assert_called_once_with(5555)
            wait_release.assert_called_once_with(8123)
            self.assertIsNone(panel.config["pid"])

    def test_restart_waits_for_port_release_before_starting_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            panel = _panel(Path(temp))
            panel._start_server_worker = mock.Mock()
            with mock.patch("app.admin_panel.get_server_status", return_value=_status(True, 5555)):
                with mock.patch("app.admin_panel.process_exists", return_value=True):
                    with mock.patch("app.admin_panel.stop_process", return_value=True):
                        with mock.patch("app.admin_panel.wait_for_port_release", return_value=True) as wait_release:
                            with mock.patch("app.admin_panel.update_config", side_effect=lambda values: {**panel.config, **values}):
                                panel._restart_server_worker()

            wait_release.assert_called_once_with(8123)
            panel._start_server_worker.assert_called_once()

    def test_postgresql_unavailable_blocks_start_with_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            panel = _panel(root)
            project = root / "PagosFiducia"
            python = project / ".venv" / "Scripts" / "python.exe"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
            (project / ".env").write_text("DJANGO_SECRET_KEY=x\n", encoding="utf-8")
            panel._database_ok.return_value = False

            inspection = mock.Mock(valid=True, errors=[])
            with mock.patch("app.admin_panel.validate_project", return_value=inspection):
                with mock.patch("app.admin_panel.is_env_valid", return_value=True):
                    with self.assertRaisesRegex(RuntimeError, "PostgreSQL no esta disponible"):
                        panel._validate_server_start_preconditions()


class CommandFailureTests(unittest.TestCase):
    def test_migrate_failure_is_not_reported_as_success(self) -> None:
        result = subprocess.CompletedProcess(["python", "manage.py", "migrate"], 1, "out", "err")
        with mock.patch("app.django_manager.require_project_python", return_value=Path("C:/PagosFiducia/.venv/Scripts/python.exe")):
            with mock.patch("app.django_manager.run_hidden", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "Fallo el comando"):
                    django_manager.migrate(Path("C:/PagosFiducia"), Path("C:/PagosFiducia/.venv"))

    def test_collectstatic_failure_is_not_reported_as_success(self) -> None:
        result = subprocess.CompletedProcess(["python", "manage.py", "collectstatic"], 1, "", "static error")
        with mock.patch("app.django_manager.require_project_python", return_value=Path("C:/PagosFiducia/.venv/Scripts/python.exe")):
            with mock.patch("app.django_manager.run_hidden", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "static error"):
                    django_manager.collectstatic(Path("C:/PagosFiducia"), Path("C:/PagosFiducia/.venv"))


class VisibleVersionTests(unittest.TestCase):
    def test_admin_panel_does_not_show_version_text_in_visible_ui(self) -> None:
        source = Path("app/admin_panel.py").read_text(encoding="utf-8")
        self.assertNotIn("Server Manager v", source)
        self.assertNotIn('("Version", APP_VERSION)', source)
        self.assertNotIn("v{APP_VERSION}", source)


if __name__ == "__main__":
    unittest.main()
