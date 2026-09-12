from __future__ import annotations

import subprocess
import sys
import json
import shutil
from pathlib import Path

from .subprocess_utils import run_hidden


def venv_path(project_path: Path) -> Path:
    return project_path / ".venv"


def python_executable(venv: Path) -> Path:
    return venv / "Scripts" / "python.exe"


def project_python_executable(project_path: Path) -> Path:
    return python_executable(venv_path(project_path))


def require_project_python(project_path: Path) -> Path:
    python = project_python_executable(project_path)
    if not python.exists():
        raise RuntimeError(f"No se encontró el entorno virtual de Gestión Fiduciaria:\n{python}")
    return python


def require_python_executable(venv: Path) -> Path:
    python = python_executable(venv)
    if not python.exists():
        raise RuntimeError(f"No se encontró el entorno virtual de Gestión Fiduciaria:\n{python}")
    return python


def ensure_virtualenv(project_path: Path) -> Path:
    venv = venv_path(project_path)
    executable = python_executable(venv)
    if executable.exists():
        return venv
    _run([_python_for_external_tools(), "-m", "venv", str(venv)], project_path)
    return venv


def install_dependencies(project_path: Path, venv: Path) -> None:
    python = require_project_python(project_path)
    _run([str(python), "-m", "pip", "install", "--upgrade", "pip"], project_path)
    _run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], project_path)
    _run([str(python), "-m", "pip", "install", "waitress"], project_path)


def migrate(project_path: Path, venv: Path) -> None:
    _run([str(require_project_python(project_path)), "manage.py", "migrate", "--noinput"], project_path)


def collectstatic(project_path: Path, venv: Path) -> None:
    _run(
        [str(require_project_python(project_path)), "manage.py", "collectstatic", "--noinput"],
        project_path,
    )

def django_shell(project_path: Path, venv: Path, code: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    return run_hidden(
        [str(require_project_python(project_path)), "manage.py", "shell", "-c", code],
        cwd=project_path,
        input=input_text,
        check=True,
    )


def find_static_probe_url(project_path: Path, venv: Path) -> str:
    code = r"""
import json
from pathlib import Path
from django.conf import settings

static_root = Path(settings.STATIC_ROOT)
preferred_suffixes = [".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".ico"]
files = [
    path for path in static_root.rglob("*")
    if path.is_file() and path.suffix not in {".gz", ".br", ".json"}
]
chosen = None
for suffix in preferred_suffixes:
    chosen = next((path for path in files if path.suffix.lower() == suffix), None)
    if chosen:
        break
if not chosen and files:
    chosen = files[0]
if not chosen:
    raise SystemExit("No se encontro ningun archivo en STATIC_ROOT despues de collectstatic.")
relative = chosen.relative_to(static_root).as_posix()
static_url = settings.STATIC_URL
if not static_url.endswith("/"):
    static_url += "/"
print(json.dumps({"url": static_url + relative, "relative": relative}))
"""
    result = django_shell(project_path, venv, code)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    return data["url"]


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    result = run_hidden(command, cwd=cwd)
    if result.returncode != 0:
        output = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
        command_text = " ".join(command)
        raise RuntimeError(f"Fallo el comando: {command_text}\n{output}")
    return result


def _python_for_external_tools() -> str:
    if not getattr(sys, "frozen", False):
        return sys.executable
    python = shutil.which("python")
    if python:
        return python
    raise RuntimeError("No se encontro python en PATH para crear el entorno virtual del proyecto Django.")
