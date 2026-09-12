from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config_manager import detect_hostname, detect_local_ipv4
from .database_manager import DatabaseCredentials


@dataclass
class EnvWriteResult:
    path: Path
    backup_path: Path | None
    allowed_hosts: list[str]
    csrf_trusted_origins: list[str]


def build_env_values(credentials: DatabaseCredentials, port: int) -> dict[str, str]:
    hostname = detect_hostname()
    ipv4 = detect_local_ipv4()
    hosts = _unique(["localhost", "127.0.0.1", ipv4, hostname])
    origins = _unique([f"http://{host}:{port}" for host in hosts])

    return {
        "DJANGO_SECRET_KEY": secrets.token_urlsafe(50),
        "DJANGO_DEBUG": "False",
        "DJANGO_ALLOWED_HOSTS": ",".join(hosts),
        "DJANGO_CSRF_TRUSTED_ORIGINS": ",".join(origins),
        "DB_NAME": credentials.database,
        "DB_USER": credentials.user,
        "DB_PASSWORD": credentials.password,
        "DB_HOST": credentials.host,
        "DB_PORT": str(credentials.port),
        "DB_CONNECT_TIMEOUT": "5",
        "SESSION_COOKIE_SECURE": "False",
        "CSRF_COOKIE_SECURE": "False",
    }


def read_env(project_path: Path) -> dict[str, str]:
    env_path = project_path / ".env"
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _unquote_env_value(value.strip())
    return values


def env_database_credentials(project_path: Path) -> DatabaseCredentials:
    values = read_env(project_path)
    return DatabaseCredentials(
        host=values.get("DB_HOST", "localhost"),
        port=int(values.get("DB_PORT", "5432") or "5432"),
        database=values.get("DB_NAME", ""),
        user=values.get("DB_USER", ""),
        password=values.get("DB_PASSWORD", ""),
    )


def is_env_valid(project_path: Path) -> bool:
    values = read_env(project_path)
    required = [
        "DJANGO_SECRET_KEY",
        "DJANGO_DEBUG",
        "DJANGO_ALLOWED_HOSTS",
        "DB_NAME",
        "DB_USER",
        "DB_HOST",
        "DB_PORT",
        "BACKUP_STORAGE_PATH",
        "BACKUP_PG_DUMP_PATH",
        "BACKUP_PG_RESTORE_PATH",
        "EMAIL_BACKEND",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_PASSWORD",
        "EMAIL_USE_TLS",
        "EMAIL_USE_SSL",
        "DEFAULT_FROM_EMAIL",
        "SERVER_EMAIL",
        "GOOGLE_DRIVE_BACKUP_ENABLED",
        "GOOGLE_DRIVE_BACKUP_FOLDER_NAME",
        "GOOGLE_DRIVE_TIMEOUT_SECONDS",
    ]
    if not all(values.get(key) for key in required):
        return False
    drive_enabled = values.get("GOOGLE_DRIVE_BACKUP_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    if drive_enabled:
        return bool(values.get("GOOGLE_DRIVE_TOKEN_FILE") and values.get("GOOGLE_DRIVE_BACKUP_FOLDER_ID"))
    return True


def write_env(project_path: Path, values: dict[str, str], backup_existing: bool) -> EnvWriteResult:
    env_path = project_path / ".env"
    backup_path = None
    if env_path.exists():
        if not backup_existing:
            raise FileExistsError("Ya existe un archivo .env y no se confirmo su respaldo/sobrescritura.")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = project_path / f".env.backup_{timestamp}"
        shutil.copy2(env_path, backup_path)

    lines = [f"{key}={_format_env_value(value)}" for key, value in values.items()]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return EnvWriteResult(
        path=env_path,
        backup_path=backup_path,
        allowed_hosts=values["DJANGO_ALLOWED_HOSTS"].split(","),
        csrf_trusted_origins=values["DJANGO_CSRF_TRUSTED_ORIGINS"].split(","),
    )


def update_env(project_path: Path, updates: dict[str, str], backup_existing: bool = True) -> EnvWriteResult:
    env_path = project_path / ".env"
    backup_path = None
    if env_path.exists() and backup_existing:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = project_path / f".env.backup_{timestamp}"
        shutil.copy2(env_path, backup_path)

    existing_lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines() if env_path.exists() else []
    pending = {key: str(value) for key, value in updates.items()}
    output: list[str] = []
    seen: set[str] = set()
    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            output.append(raw_line)
            continue
        key, _value = raw_line.split("=", 1)
        clean_key = key.strip()
        if clean_key in pending:
            if clean_key not in seen:
                output.append(f"{clean_key}={_format_env_value(pending[clean_key])}")
                seen.add(clean_key)
            continue
        output.append(raw_line)
    for key, value in pending.items():
        if key not in seen:
            output.append(f"{key}={_format_env_value(value)}")

    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")
    merged = read_env(project_path)
    return EnvWriteResult(
        path=env_path,
        backup_path=backup_path,
        allowed_hosts=merged.get("DJANGO_ALLOWED_HOSTS", "").split(",") if merged.get("DJANGO_ALLOWED_HOSTS") else [],
        csrf_trusted_origins=(
            merged.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
            if merged.get("DJANGO_CSRF_TRUSTED_ORIGINS")
            else []
        ),
    )


def _format_env_value(value: str) -> str:
    value = str(value)
    if any(char in value for char in [" ", "#", "\n", "\r"]):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = value.strip()
        if value and value not in result:
            result.append(value)
    return result
