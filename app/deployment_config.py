from __future__ import annotations

import logging
import os
import shutil
import smtplib
import tempfile
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .environment_manager import read_env, update_env
from .subprocess_utils import run_hidden


LOGGER = logging.getLogger(__name__)

DEFAULT_BACKUP_STORAGE_PATH = Path(r"C:\GestionFiduciaria\Backups")
GMAIL_ENV_VALUES = {
    "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
    "EMAIL_HOST": "smtp.gmail.com",
    "EMAIL_PORT": "587",
    "EMAIL_USE_TLS": "True",
    "EMAIL_USE_SSL": "False",
}


class DeploymentConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PostgresTools:
    pg_dump: Path
    pg_restore: Path


def default_backup_storage_path() -> Path:
    return DEFAULT_BACKUP_STORAGE_PATH


def validate_backup_directory(path_value: str | Path) -> Path:
    path = _normalize_directory_path(path_value)
    if path.exists() and not path.is_dir():
        raise DeploymentConfigError("La ruta seleccionada para copias de seguridad no corresponde a una carpeta.")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DeploymentConfigError(
            "No fue posible utilizar la carpeta seleccionada para almacenar las copias de seguridad. "
            "Verifique los permisos de la ubicación."
        ) from exc
    if not path.is_dir():
        raise DeploymentConfigError("La ruta seleccionada para copias de seguridad no corresponde a una carpeta.")

    marker: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path, prefix=".gfsm_", suffix=".tmp", delete=False) as temp:
            marker = Path(temp.name)
            temp.write("Gestion Fiduciaria backup storage validation")
        content = marker.read_text(encoding="utf-8")
        if "backup storage validation" not in content:
            raise OSError("No fue posible leer el archivo temporal de validación.")
        marker.unlink()
    except OSError as exc:
        if marker and marker.exists():
            try:
                marker.unlink()
            except OSError:
                LOGGER.warning("No se pudo eliminar archivo temporal de validacion: %s", marker)
        raise DeploymentConfigError(
            "No fue posible utilizar la carpeta seleccionada para almacenar las copias de seguridad. "
            "Verifique los permisos de la ubicación."
        ) from exc
    return path


def prepare_backup_directory(path_value: str | Path) -> Path:
    path = validate_backup_directory(path_value)
    _prepare_windows_acl(path)
    validate_backup_directory(path)
    return path


def detect_postgres_tools(project_path: Path | None = None) -> PostgresTools:
    env_values = read_env(project_path) if project_path else {}
    dump_candidates = _candidate_executables("pg_dump", env_values.get("BACKUP_PG_DUMP_PATH"))
    restore_candidates = _candidate_executables("pg_restore", env_values.get("BACKUP_PG_RESTORE_PATH"))
    pg_dump = _first_existing(dump_candidates)
    pg_restore = _first_existing(restore_candidates)
    if not pg_dump or not pg_restore:
        raise DeploymentConfigError(
            "No fue posible detectar pg_dump.exe y pg_restore.exe. Verifique la instalación de PostgreSQL."
        )
    return PostgresTools(pg_dump=pg_dump, pg_restore=pg_restore)


def backup_env_values(backup_path: Path, tools: PostgresTools) -> dict[str, str]:
    return {
        "BACKUP_STORAGE_PATH": str(backup_path),
        "BACKUP_PG_DUMP_PATH": str(tools.pg_dump),
        "BACKUP_PG_RESTORE_PATH": str(tools.pg_restore),
    }


def gmail_env_values(email: str, app_password: str | None = None) -> dict[str, str]:
    normalized_email = _validate_email(email)
    values = dict(GMAIL_ENV_VALUES)
    values["EMAIL_HOST_USER"] = normalized_email
    values["DEFAULT_FROM_EMAIL"] = normalized_email
    values["SERVER_EMAIL"] = normalized_email
    if app_password is not None:
        values["EMAIL_HOST_PASSWORD"] = app_password
    return values


def validate_gmail_settings(values: dict[str, str]) -> None:
    required = {
        "EMAIL_BACKEND": "backend configurado",
        "EMAIL_HOST": "host SMTP",
        "EMAIL_PORT": "puerto SMTP",
        "EMAIL_HOST_USER": "usuario SMTP",
        "EMAIL_HOST_PASSWORD": "contraseña de aplicación",
    }
    missing = [label for key, label in required.items() if not str(values.get(key, "")).strip()]
    if missing:
        raise DeploymentConfigError("La configuración de correo está incompleta: " + ", ".join(missing) + ".")
    try:
        _validate_email(values["EMAIL_HOST_USER"])
        port = int(str(values["EMAIL_PORT"]).strip())
    except ValueError as exc:
        raise DeploymentConfigError("El puerto SMTP no es válido.") from exc
    if port < 1 or port > 65535:
        raise DeploymentConfigError("El puerto SMTP no es válido.")
    use_tls = str(values.get("EMAIL_USE_TLS", "False")).strip().lower() in {"1", "true", "yes", "on"}
    use_ssl = str(values.get("EMAIL_USE_SSL", "False")).strip().lower() in {"1", "true", "yes", "on"}
    if use_tls and use_ssl:
        raise DeploymentConfigError("La configuración SMTP no puede usar TLS y SSL al mismo tiempo.")


def test_gmail_smtp(values: dict[str, str], recipient: str | None = None) -> None:
    validate_gmail_settings(values)
    host = values["EMAIL_HOST"].strip()
    port = int(values["EMAIL_PORT"])
    user = values["EMAIL_HOST_USER"].strip()
    password = values["EMAIL_HOST_PASSWORD"]
    use_tls = values.get("EMAIL_USE_TLS", "False").lower() in {"1", "true", "yes", "on"}
    use_ssl = values.get("EMAIL_USE_SSL", "False").lower() in {"1", "true", "yes", "on"}
    smtp_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    try:
        with smtp_class(host, port, timeout=15) as client:
            if use_tls:
                client.starttls()
            client.login(user, password)
            if recipient:
                recipient_email = _validate_email(recipient)
                message = EmailMessage()
                message["Subject"] = "Prueba de correo - Gestion Fiduciaria"
                message["From"] = values.get("DEFAULT_FROM_EMAIL") or user
                message["To"] = recipient_email
                message.set_content("Correo de prueba enviado desde Gestion Fiduciaria Server Manager.")
                client.send_message(message)
    except smtplib.SMTPException as exc:
        raise DeploymentConfigError("No fue posible autenticar con Gmail SMTP. Verifique la contraseña de aplicación.") from exc
    except OSError as exc:
        raise DeploymentConfigError("No fue posible conectar con Gmail SMTP. Verifique red, firewall o DNS.") from exc


def save_deployment_env(
    project_path: Path,
    backup_path: Path,
    tools: PostgresTools,
    gmail_email: str,
    gmail_password: str | None,
) -> None:
    updates = backup_env_values(backup_path, tools)
    updates.update(gmail_env_values(gmail_email, gmail_password))
    validate_gmail_settings({**read_env(project_path), **updates})
    update_env(project_path, updates, backup_existing=True)


def _validate_email(value: str | None) -> str:
    email = str(value or "").strip()
    if not email:
        raise DeploymentConfigError("Digite el correo Gmail remitente.")
    if "@" not in email or email.startswith("@") or email.endswith("@") or " " in email:
        raise DeploymentConfigError("El correo Gmail remitente no tiene un formato valido.")
    local, domain = email.rsplit("@", 1)
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise DeploymentConfigError("El correo Gmail remitente no tiene un formato valido.")
    return email.lower()


def _normalize_directory_path(path_value: str | Path) -> Path:
    raw = str(path_value).strip()
    if not raw:
        raise DeploymentConfigError("Seleccione la carpeta donde se almacenarán las copias de seguridad.")
    if "\x00" in raw:
        raise DeploymentConfigError("La ruta seleccionada no es válida.")
    path = Path(raw).expanduser()
    try:
        return path.resolve()
    except OSError as exc:
        raise DeploymentConfigError("La ruta seleccionada no es válida.") from exc


def _prepare_windows_acl(path: Path) -> None:
    if os.name != "nt":
        return
    result = run_hidden(
        [
            "icacls",
            str(path),
            "/grant",
            "*S-1-5-18:(OI)(CI)M",
            "*S-1-5-32-544:(OI)(CI)M",
        ],
        timeout=30,
    )
    if result.returncode != 0:
        LOGGER.warning("No se pudo ajustar ACL de carpeta de backups con icacls. Ruta=%s", path)


def _candidate_executables(tool: str, configured: str | None) -> list[Path]:
    executable = tool + ".exe" if os.name == "nt" else tool
    candidates: list[Path] = []
    if configured:
        configured_path = Path(configured)
        if configured_path.name.lower() == executable.lower() or configured_path.is_absolute():
            candidates.append(configured_path)
    found = shutil.which(executable) or shutil.which(tool)
    if found:
        candidates.append(Path(found))
    candidates.extend(_postgres_program_files_candidates(executable))
    return candidates


def _postgres_program_files_candidates(executable: str) -> list[Path]:
    roots = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")]
    candidates: list[Path] = []
    for root in [Path(value) for value in roots if value]:
        postgres_root = root / "PostgreSQL"
        if not postgres_root.exists():
            continue
        version_dirs = [item for item in postgres_root.iterdir() if item.is_dir()]
        version_dirs.sort(key=_postgres_version_key, reverse=True)
        candidates.extend(version / "bin" / executable for version in version_dirs)
    return candidates


def _postgres_version_key(path: Path) -> tuple[int, str]:
    try:
        return (int(path.name), path.name)
    except ValueError:
        return (-1, path.name)


def _first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None
