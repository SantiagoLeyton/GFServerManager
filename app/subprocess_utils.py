from __future__ import annotations

import locale
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Sequence


LOGGER = logging.getLogger(__name__)


def hidden_startup_kwargs(command: Sequence[str | os.PathLike[str]], *, log: bool = False) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startupinfo
    if log:
        LOGGER.debug("Subproceso oculto preparado command=%s hidden=%s", _safe_command(command), os.name == "nt")
    return kwargs


def run_hidden(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | str | None = None,
    input: str | None = None,
    check: bool = False,
    timeout: int | float | None = None,
    capture_output: bool = True,
    text: bool = True,
    log: bool = False,
) -> subprocess.CompletedProcess:
    kwargs = hidden_startup_kwargs(command, log=log)
    if text:
        kwargs.update(
            {
                "text": True,
                "encoding": locale.getpreferredencoding(False),
                "errors": "replace",
            }
        )
    result = subprocess.run(
        [str(part) for part in command],
        cwd=cwd,
        input=input,
        capture_output=capture_output,
        check=check,
        timeout=timeout,
        **kwargs,
    )
    if log:
        LOGGER.debug(
            "Subproceso oculto finalizado command=%s returncode=%s",
            _safe_command(command),
            result.returncode,
        )
    return result


def popen_hidden(
    command: Sequence[str | os.PathLike[str]],
    *,
    cwd: Path | str | None = None,
    stdout=None,
    stderr=None,
    stdin=None,
    log: bool = False,
) -> subprocess.Popen:
    kwargs = hidden_startup_kwargs(command, log=log)
    process = subprocess.Popen(
        [str(part) for part in command],
        cwd=cwd,
        stdout=stdout,
        stderr=stderr,
        stdin=stdin,
        **kwargs,
    )
    if log:
        LOGGER.debug("Subproceso oculto iniciado command=%s pid=%s", _safe_command(command), process.pid)
    return process


def _safe_command(command: Sequence[str | os.PathLike[str]]) -> str:
    hidden_next = False
    safe_parts: list[str] = []
    sensitive_flags = {"--password", "-p", "password", "contraseña", "contrasena"}
    for part in [str(item) for item in command]:
        lower = part.lower()
        if hidden_next:
            safe_parts.append("***")
            hidden_next = False
            continue
        if lower in sensitive_flags:
            safe_parts.append(part)
            hidden_next = True
            continue
        if "password=" in lower or "contraseña=" in lower or "contrasena=" in lower:
            safe_parts.append(part.split("=", 1)[0] + "=***")
        else:
            safe_parts.append(part)
    return " ".join(safe_parts)
