from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def venv_path(project_path: Path) -> Path:
    return project_path / ".venv"


def python_executable(venv: Path) -> Path:
    return venv / "Scripts" / "python.exe"


def ensure_virtualenv(project_path: Path) -> Path:
    venv = venv_path(project_path)
    executable = python_executable(venv)
    if executable.exists():
        return venv
    subprocess.run([sys.executable, "-m", "venv", str(venv)], cwd=project_path, check=True)
    return venv


def install_dependencies(project_path: Path, venv: Path) -> None:
    python = python_executable(venv)
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd=project_path, check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], cwd=project_path, check=True)
    subprocess.run([str(python), "-m", "pip", "install", "waitress"], cwd=project_path, check=True)


def migrate(project_path: Path, venv: Path) -> None:
    subprocess.run([str(python_executable(venv)), "manage.py", "migrate", "--noinput"], cwd=project_path, check=True)


def collectstatic(project_path: Path, venv: Path) -> None:
    subprocess.run(
        [str(python_executable(venv)), "manage.py", "collectstatic", "--noinput"],
        cwd=project_path,
        check=True,
    )

def django_shell(project_path: Path, venv: Path, code: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(python_executable(venv)), "manage.py", "shell", "-c", code],
        cwd=project_path,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )

