from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class RequirementStatus:
    ok: bool
    messages: list[str]


def check_requirements() -> RequirementStatus:
    messages: list[str] = []
    ok = True

    if sys.version_info < (3, 11):
        ok = False
        messages.append("Se requiere Python 3.11 o superior.")
    else:
        messages.append(f"Python detectado: {sys.version.split()[0]}")

    python = _python_for_external_tools()
    if python is None:
        ok = False
        messages.append("No se encontro python en PATH.")

    if python:
        try:
            result = subprocess.run(
                [python, "-m", "venv", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                ok = False
                messages.append("El modulo venv no esta disponible.")
        except (OSError, subprocess.SubprocessError):
            ok = False
            messages.append("No se pudo comprobar el modulo venv.")

    return RequirementStatus(ok=ok, messages=messages)


def _python_for_external_tools() -> str | None:
    if not getattr(sys, "frozen", False):
        return sys.executable
    return shutil.which("python")

