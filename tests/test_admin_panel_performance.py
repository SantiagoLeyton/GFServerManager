from __future__ import annotations

import unittest
from unittest import mock

from app.admin_panel import AdminPanel


class AdminPanelPerformanceTests(unittest.TestCase):
    def test_refresh_status_dispatches_worker_without_blocking_navigation_thread(self) -> None:
        panel = AdminPanel.__new__(AdminPanel)
        panel._closed = False
        panel._status_refresh_running = False
        panel._status_refresh_pending = False
        panel.config = {"project_path": "C:/GestionFiduciaria/PagosFiducia", "port": 8000}
        panel._widget_exists = mock.Mock(return_value=True)

        created_threads = []

        class FakeThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon
                created_threads.append(self)

            def start(self):
                return None

        with mock.patch("app.admin_panel.load_config", return_value=panel.config):
            with mock.patch("app.admin_panel.get_server_status") as get_status:
                with mock.patch("app.admin_panel.threading.Thread", FakeThread):
                    panel._refresh_status()

        get_status.assert_not_called()
        self.assertTrue(panel._status_refresh_running)
        self.assertEqual(len(created_threads), 1)
        self.assertTrue(created_threads[0].daemon)


if __name__ == "__main__":
    unittest.main()
