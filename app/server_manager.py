from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin
from pathlib import Path

import psutil

from .django_manager import python_executable
from .subprocess_utils import popen_hidden


SERVER_RUNNING = "Servidor en ejecución"
SERVER_STOPPED = "Servidor detenido"
SERVER_UNKNOWN = "Estado desconocido"


@dataclass(frozen=True)
class ServerStatus:
    running: bool
    pid: int | None
    host: str
    port: int
    listening: bool
    uptime: str | None
    state: str
    port_message: str
    process_message: str
    conflict_message: str | None = None


DEFAULT_WSGI_MODULE = "config.wsgi:application"


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_process_on_port(port: int) -> str | None:
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            for conn in proc.net_connections(kind="inet"):
                if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                    return f"{proc.info['name']} (PID {proc.info['pid']})"
        except (psutil.Error, PermissionError):
            continue
    return None


def find_waitress_listener_pid(
    port: int,
    project_path: Path,
    wsgi_module: str = DEFAULT_WSGI_MODULE,
    venv_path: Path | None = None,
    expected_pid: int | None = None,
) -> int | None:
    if expected_pid and process_matches_waitress(
        expected_pid,
        port,
        project_path,
        wsgi_module=wsgi_module,
        venv_path=venv_path,
    ):
        return expected_pid
    if expected_pid and _trusted_stored_pid_matches_listener(expected_pid, port):
        return expected_pid

    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid = int(proc.info["pid"])
        except (psutil.Error, PermissionError):
            continue
        if process_matches_waitress(pid, port, project_path, wsgi_module=wsgi_module, venv_path=venv_path):
            return pid
    return None


def process_exists(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def process_start_time(pid: int | None) -> float | None:
    if not process_exists(pid):
        return None
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def is_waitress_process(
    pid: int | None,
    project_path: Path | None = None,
    port: int | None = None,
    wsgi_module: str = DEFAULT_WSGI_MODULE,
    venv_path: Path | None = None,
) -> bool:
    if not process_exists(pid):
        return False
    if port is not None and project_path is not None:
        return process_matches_waitress(
            pid,
            port,
            project_path,
            wsgi_module=wsgi_module,
            venv_path=venv_path,
        ) or _trusted_stored_pid_matches_listener(pid, port)
    try:
        proc = psutil.Process(pid)
        command = _normalized_command(proc)
        if not _command_has_waitress(command) or _normalize_text(wsgi_module) not in command:
            return False
        if project_path is None:
            return True
        return _process_related_to_project(proc, command, project_path, venv_path)
    except psutil.Error:
        return False


def stop_process(pid: int | None, timeout_seconds: int = 10) -> bool:
    if not process_exists(pid):
        return True
    proc = psutil.Process(pid)
    proc.terminate()
    try:
        proc.wait(timeout=timeout_seconds)
        return True
    except psutil.TimeoutExpired:
        return False


def port_conflict_message(port: int, project_path: Path) -> str | None:
    listener_pid = find_waitress_listener_pid(port, project_path)
    if listener_pid:
        return None
    process = find_process_on_port(port)
    if process:
        return f"El puerto {port} esta ocupado por {process}. No corresponde a Gestion Fiduciaria."
    if not is_port_available(port):
        return f"El puerto {port} esta ocupado por un proceso no identificable."
    return None


def get_server_status(config: dict | None) -> ServerStatus:
    config = config or {}
    host = str(config.get("host", "0.0.0.0") or "0.0.0.0")
    try:
        port = int(config.get("port", 8000))
    except (TypeError, ValueError):
        return ServerStatus(
            running=False,
            pid=None,
            host=host,
            port=8000,
            listening=False,
            uptime=None,
            state=SERVER_UNKNOWN,
            port_message="Puerto no disponible por configuracion invalida.",
            process_message="No se pudo determinar el proceso Waitress.",
        )

    project_value = config.get("project_path")
    project_path = Path(str(project_value)) if project_value else Path()
    venv_value = config.get("venv_path")
    venv_path = Path(str(venv_value)) if venv_value else None
    wsgi_module = str(config.get("wsgi_module") or DEFAULT_WSGI_MODULE)
    stored_pid = _safe_int(config.get("pid"))
    listener_pid = (
        find_waitress_listener_pid(
            port,
            project_path,
            wsgi_module=wsgi_module,
            venv_path=venv_path,
            expected_pid=stored_pid,
        )
        if project_value
        else None
    )
    if listener_pid:
        return ServerStatus(
            running=True,
            pid=listener_pid,
            host=host,
            port=port,
            listening=True,
            uptime=_format_uptime(listener_pid),
            state=SERVER_RUNNING,
            port_message=f"Puerto {port} escuchando.",
            process_message=f"Waitress activo con PID {listener_pid}.",
        )

    conflict = (
        port_conflict_message_for_config(port, project_path, wsgi_module=wsgi_module, venv_path=venv_path)
        if project_value
        else None
    )
    if conflict:
        return ServerStatus(
            running=False,
            pid=None,
            host=host,
            port=port,
            listening=True,
            uptime=None,
            state=SERVER_UNKNOWN,
            port_message=conflict,
            process_message="No existe un proceso Waitress activo para Gestion Fiduciaria.",
            conflict_message=conflict,
        )

    if is_port_available(port):
        return ServerStatus(
            running=False,
            pid=None,
            host=host,
            port=port,
            listening=False,
            uptime=None,
            state=SERVER_STOPPED,
            port_message=f"Puerto {port} libre.",
            process_message="No existe un proceso Waitress activo.",
        )

    process = find_process_on_port(port)
    detail = f" Lo usa {process}." if process else ""
    return ServerStatus(
        running=False,
        pid=None,
        host=host,
        port=port,
        listening=True,
        uptime=None,
        state=SERVER_UNKNOWN,
        port_message=f"Puerto {port} no disponible.{detail}",
        process_message="No se pudo confirmar un proceso Waitress activo.",
    )


def _format_uptime(pid: int | None) -> str | None:
    started = process_start_time(pid)
    if not started:
        return None
    seconds = int(time.time() - started)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def port_conflict_message_for_config(
    port: int,
    project_path: Path,
    wsgi_module: str = DEFAULT_WSGI_MODULE,
    venv_path: Path | None = None,
) -> str | None:
    listener_pid = find_waitress_listener_pid(port, project_path, wsgi_module=wsgi_module, venv_path=venv_path)
    if listener_pid:
        return None
    process = find_process_on_port(port)
    if process:
        return f"El puerto {port} esta ocupado por {process}. No corresponde a Gestion Fiduciaria."
    if not is_port_available(port):
        return f"El puerto {port} esta ocupado por un proceso no identificable."
    return None


def process_matches_waitress(
    pid: int | None,
    port: int,
    project_path: Path,
    wsgi_module: str = DEFAULT_WSGI_MODULE,
    venv_path: Path | None = None,
) -> bool:
    if not process_exists(pid):
        return False
    try:
        proc = psutil.Process(pid)
        command = _normalized_command(proc)
        return (
            _process_listens_on_port(proc, port)
            and _command_has_waitress(command)
            and _command_has_port(command, port)
            and _normalize_text(wsgi_module) in command
            and _process_related_to_project(proc, command, project_path, venv_path)
        )
    except psutil.Error:
        return False


def _process_listens_on_port(proc: psutil.Process, port: int) -> bool:
    try:
        for conn in proc.net_connections(kind="inet"):
            if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port:
                return True
    except (psutil.Error, PermissionError):
        return False
    return False


def _normalized_command(proc: psutil.Process) -> str:
    try:
        return _normalize_text(" ".join(proc.cmdline()))
    except (psutil.Error, PermissionError):
        return ""


def _commandline_access_denied(proc: psutil.Process) -> bool:
    try:
        proc.cmdline()
        return False
    except (psutil.AccessDenied, PermissionError):
        return True
    except psutil.Error:
        return False


def _command_has_waitress(command: str) -> bool:
    return "-m waitress" in command or "waitress-serve" in command or " waitress " in command


def _command_has_port(command: str, port: int) -> bool:
    port_text = str(port)
    return f"--port={port_text}" in command or f"--port {port_text}" in command or f" -p {port_text}" in command


def _process_related_to_project(
    proc: psutil.Process,
    command: str,
    project_path: Path,
    venv_path: Path | None = None,
) -> bool:
    candidates = [_normalize_path(project_path)]
    if venv_path:
        candidates.append(_normalize_path(venv_path))
    candidates = [item for item in candidates if item]
    if any(item in command for item in candidates):
        return True
    try:
        cwd = _normalize_path(Path(proc.cwd()))
        return any(item == cwd for item in candidates if item)
    except (psutil.Error, OSError, PermissionError):
        return False


def _normalize_path(path: Path) -> str:
    try:
        return _normalize_text(str(path.resolve()))
    except OSError:
        return _normalize_text(str(path))


def _normalize_text(value: str) -> str:
    return str(value).replace("/", "\\").strip().lower()


def _safe_int(value) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


def _trusted_stored_pid_matches_listener(pid: int, port: int) -> bool:
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return False
    return _commandline_access_denied(proc) and _process_listens_on_port(proc, port) and wait_for_http(port, 2)


def start_waitress(project_path: Path, venv: Path, port: int, host: str = "0.0.0.0") -> subprocess.Popen:
    own_pid = find_waitress_listener_pid(port, project_path)
    if own_pid:
        raise RuntimeError(f"Waitress ya esta ejecutandose para este proyecto con PID {own_pid}.")
    conflict = port_conflict_message(port, project_path)
    if conflict:
        raise RuntimeError(conflict)
    if not is_port_available(port):
        process = find_process_on_port(port)
        detail = f" Lo usa {process}." if process else ""
        raise RuntimeError(f"El puerto {port} esta ocupado.{detail}")

    command = [
        str(python_executable(venv)),
        "-m",
        "waitress",
        f"--host={host}",
        f"--port={port}",
        "config.wsgi:application",
    ]
    return popen_hidden(command, cwd=project_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_http(port: int, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    url = f"http://127.0.0.1:{port}/"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return 200 <= response.status < 500
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 500:
                return True
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    return False


def wait_for_static_file(port: int, static_url: str, timeout_seconds: int = 30) -> bool:
    deadline = time.time() + timeout_seconds
    url = urljoin(f"http://127.0.0.1:{port}/", static_url)
    while time.time() < deadline:
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=3) as response:
                return 200 <= response.status < 400 and int(response.headers.get("Content-Length", "1")) > 0
        except (OSError, urllib.error.URLError, urllib.error.HTTPError):
            time.sleep(1)
    return False
