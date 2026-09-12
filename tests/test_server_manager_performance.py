from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app.server_manager as server_manager


def _listen_connection(pid: int, port: int):
    return mock.Mock(pid=pid, status=server_manager.psutil.CONN_LISTEN, laddr=mock.Mock(port=port))


class ServerManagerPerformanceTests(unittest.TestCase):
    def test_find_process_on_port_uses_global_connections_not_process_scan(self) -> None:
        process = mock.Mock()
        process.name.return_value = "python.exe"
        with mock.patch("app.server_manager.psutil.net_connections", return_value=[_listen_connection(321, 8000)]):
            with mock.patch("app.server_manager.psutil.Process", return_value=process) as process_class:
                with mock.patch("app.server_manager.psutil.process_iter") as process_iter:
                    result = server_manager.find_process_on_port(8000)

        self.assertEqual(result, "python.exe (PID 321)")
        process_class.assert_called_once_with(321)
        process_iter.assert_not_called()

    def test_find_waitress_listener_pid_checks_only_port_listener_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            with mock.patch(
                "app.server_manager.psutil.net_connections",
                return_value=[_listen_connection(111, 8000), _listen_connection(222, 9000)],
            ):
                with mock.patch("app.server_manager.process_matches_waitress", return_value=True) as matches:
                    with mock.patch("app.server_manager.psutil.process_iter") as process_iter:
                        pid = server_manager.find_waitress_listener_pid(8000, project)

        self.assertEqual(pid, 111)
        matches.assert_called_once()
        self.assertEqual(matches.call_args.args[0], 111)
        process_iter.assert_not_called()


if __name__ == "__main__":
    unittest.main()
