from __future__ import annotations

import socket
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin
from pathlib import Path

import psutil

from .django_manager import python_executable


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
                if conn.laddr and conn.laddr.port == port:
                    return f"{proc.info['name']} (PID {proc.info['pid']})"
        except (psutil.Error, PermissionError):
            continue
    return None


def start_waitress(project_path: Path, venv: Path, port: int) -> subprocess.Popen:
    if not is_port_available(port):
        process = find_process_on_port(port)
        detail = f" Lo usa {process}." if process else ""
        raise RuntimeError(f"El puerto {port} esta ocupado.{detail}")

    command = [
        str(python_executable(venv)),
        "-m",
        "waitress",
        "--host=0.0.0.0",
        f"--port={port}",
        "config.wsgi:application",
    ]
    return subprocess.Popen(command, cwd=project_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
