from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .environment_manager import read_env, update_env
from .logging_config import app_data_dir
from .subprocess_utils import run_hidden


LOGGER = logging.getLogger(__name__)
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
DRIVE_FOLDER_NAME = "GestionFiduciaria-BACKUPS"
DRIVE_TIMEOUT_SECONDS = "20"
CREDENTIALS_FILE_NAME = "credentials.json"
TOKEN_FILE_NAME = "token.json"


class GoogleDriveConfigError(RuntimeError):
    pass


class GoogleDriveAuthorizationRequired(GoogleDriveConfigError):
    pass


@dataclass(frozen=True)
class GoogleDrivePaths:
    root: Path
    credentials: Path
    token: Path


@dataclass(frozen=True)
class GoogleDriveStatus:
    configured: bool
    credentials_present: bool
    token_present: bool
    enabled: bool
    folder_id: str
    folder_name: str
    message: str
    account_email: str | None = None


@dataclass(frozen=True)
class GoogleDriveSetupResult:
    token_path: Path
    folder_id: str
    folder_name: str
    account_email: str | None = None


def google_drive_paths(prepare_acl: bool = True) -> GoogleDrivePaths:
    root = app_data_dir() / "GoogleDrive"
    if prepare_acl:
        root.mkdir(parents=True, exist_ok=True)
        _prepare_windows_acl(root)
    return GoogleDrivePaths(root=root, credentials=root / CREDENTIALS_FILE_NAME, token=root / TOKEN_FILE_NAME)


def validate_credentials_file(path_value: str | Path) -> dict[str, Any]:
    path = Path(str(path_value).strip())
    if not path.exists() or not path.is_file():
        raise GoogleDriveConfigError("Seleccione un archivo de credenciales OAuth valido.")
    if path.suffix.lower() != ".json":
        raise GoogleDriveConfigError("El archivo de credenciales OAuth debe ser JSON.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoogleDriveConfigError("El archivo de credenciales OAuth no contiene JSON valido.") from exc
    installed = data.get("installed")
    if not isinstance(installed, dict):
        raise GoogleDriveConfigError("Las credenciales OAuth deben corresponder a una aplicacion de escritorio.")
    missing = [
        key
        for key in ["client_id", "client_secret", "auth_uri", "token_uri"]
        if not str(installed.get(key, "")).strip()
    ]
    if missing:
        raise GoogleDriveConfigError("Las credenciales OAuth estan incompletas: " + ", ".join(missing) + ".")
    return data


def copy_credentials_to_persistent_storage(source: str | Path) -> Path:
    validate_credentials_file(source)
    paths = google_drive_paths()
    temp_path = paths.credentials.with_suffix(".json.tmp")
    temp_path.write_text(Path(source).read_text(encoding="utf-8"), encoding="utf-8")
    os.replace(temp_path, paths.credentials)
    _prepare_windows_acl(paths.root)
    return paths.credentials


def configure_google_drive(project_path: Path, credentials_source: str | Path | None = None) -> GoogleDriveSetupResult:
    paths = google_drive_paths()
    if credentials_source:
        setup = authorize_google_drive(Path(credentials_source), paths.token.with_suffix(".new.json"))
        copy_credentials_to_persistent_storage(credentials_source)
        os.replace(setup.token_path, paths.token)
        setup = GoogleDriveSetupResult(
            token_path=paths.token.resolve(),
            folder_id=setup.folder_id,
            folder_name=setup.folder_name,
            account_email=setup.account_email,
        )
        update_env(project_path, google_drive_enabled_env_values(setup.token_path, setup.folder_id), backup_existing=True)
        return setup
    if not paths.credentials.exists():
        raise GoogleDriveConfigError("No hay credenciales OAuth configuradas para Google Drive.")
    setup = authorize_google_drive(paths.credentials, paths.token)
    updates = google_drive_enabled_env_values(setup.token_path, setup.folder_id)
    update_env(project_path, updates, backup_existing=True)
    return setup


def configure_google_drive_later(project_path: Path) -> None:
    update_env(project_path, google_drive_disabled_env_values(), backup_existing=True)


def authorize_google_drive(credentials_path: Path, token_path: Path | None = None) -> GoogleDriveSetupResult:
    token_path = token_path or google_drive_paths().token
    validate_credentials_file(credentials_path)
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise GoogleDriveConfigError("Las dependencias oficiales de Google Drive no estan instaladas.") from exc
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes=[DRIVE_SCOPE])
    try:
        credentials = flow.run_local_server(port=0, prompt="consent")
    except Exception as exc:
        raise GoogleDriveConfigError("La autorizacion de Google Drive fue cancelada o no se pudo completar.") from exc
    return persist_authorized_credentials(credentials, token_path)


def persist_authorized_credentials(credentials, token_path: Path) -> GoogleDriveSetupResult:
    if not getattr(credentials, "valid", False):
        raise GoogleDriveConfigError("Google Drive no devolvio un token valido.")
    service = build_drive_service(credentials)
    folder_id = ensure_drive_folder(service)
    account_email = get_drive_account_email(service)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    _prepare_windows_acl(token_path.parent)
    serialized = credentials.to_json()
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=token_path.parent, prefix=".token_", suffix=".json", delete=False) as temp:
        temp_path = Path(temp.name)
        temp.write(serialized)
    os.replace(temp_path, token_path)
    return GoogleDriveSetupResult(
        token_path=token_path.resolve(),
        folder_id=folder_id,
        folder_name=DRIVE_FOLDER_NAME,
        account_email=account_email,
    )


def load_drive_credentials(token_path: str | Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise GoogleDriveConfigError("Las dependencias oficiales de Google Drive no estan instaladas.") from exc
    path = Path(token_path)
    if not path.exists():
        raise GoogleDriveAuthorizationRequired("Google Drive requiere autorizacion nuevamente.")
    try:
        credentials = Credentials.from_authorized_user_file(str(path), scopes=[DRIVE_SCOPE])
    except Exception as exc:
        raise GoogleDriveAuthorizationRequired("Google Drive requiere autorizacion nuevamente.") from exc
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            path.write_text(credentials.to_json(), encoding="utf-8")
        except Exception as exc:
            raise GoogleDriveAuthorizationRequired("Google Drive requiere autorizacion nuevamente.") from exc
    if not credentials.valid:
        raise GoogleDriveAuthorizationRequired("Google Drive requiere autorizacion nuevamente.")
    return credentials


def build_drive_service(credentials):
    try:
        import google_auth_httplib2
        import httplib2
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise GoogleDriveConfigError("Las dependencias oficiales de Google Drive no estan instaladas.") from exc
    http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=int(DRIVE_TIMEOUT_SECONDS)))
    return build("drive", "v3", http=http, cache_discovery=False)


def test_google_drive_connection(project_path: Path) -> GoogleDriveStatus:
    env_values = read_env(project_path)
    token_file = env_values.get("GOOGLE_DRIVE_TOKEN_FILE", "")
    folder_id = env_values.get("GOOGLE_DRIVE_BACKUP_FOLDER_ID", "")
    folder_name = env_values.get("GOOGLE_DRIVE_BACKUP_FOLDER_NAME", DRIVE_FOLDER_NAME) or DRIVE_FOLDER_NAME
    if not token_file:
        raise GoogleDriveAuthorizationRequired("Google Drive esta pendiente de configuracion.")
    credentials = load_drive_credentials(token_file)
    service = build_drive_service(credentials)
    account_email = get_drive_account_email(service)
    resolved_folder_id = ensure_drive_folder(service, folder_id=folder_id, folder_name=folder_name)
    if not folder_id or folder_id != resolved_folder_id:
        update_env(project_path, {"GOOGLE_DRIVE_BACKUP_FOLDER_ID": resolved_folder_id}, backup_existing=True)
    return GoogleDriveStatus(
        configured=True,
        credentials_present=google_drive_paths().credentials.exists(),
        token_present=Path(token_file).exists(),
        enabled=env_values.get("GOOGLE_DRIVE_BACKUP_ENABLED", "").lower() in {"1", "true", "yes", "on"},
        folder_id=resolved_folder_id,
        folder_name=folder_name,
        account_email=account_email,
        message="Conexion con Google Drive correcta.",
    )


def google_drive_status_from_env(project_path: Path) -> GoogleDriveStatus:
    env_values = read_env(project_path)
    paths = google_drive_paths(prepare_acl=False)
    token_file = env_values.get("GOOGLE_DRIVE_TOKEN_FILE", "")
    enabled = env_values.get("GOOGLE_DRIVE_BACKUP_ENABLED", "").lower() in {"1", "true", "yes", "on"}
    token_present = Path(token_file).exists() if token_file else paths.token.exists()
    configured = bool(enabled and token_file and token_present and env_values.get("GOOGLE_DRIVE_BACKUP_FOLDER_ID"))
    return GoogleDriveStatus(
        configured=configured,
        credentials_present=paths.credentials.exists(),
        token_present=token_present,
        enabled=enabled,
        folder_id=env_values.get("GOOGLE_DRIVE_BACKUP_FOLDER_ID", ""),
        folder_name=env_values.get("GOOGLE_DRIVE_BACKUP_FOLDER_NAME", DRIVE_FOLDER_NAME) or DRIVE_FOLDER_NAME,
        message="Google Drive configurado." if configured else "Google Drive pendiente de configuracion.",
    )


def ensure_drive_folder(service, folder_id: str = "", folder_name: str = DRIVE_FOLDER_NAME) -> str:
    if folder_id and drive_folder_exists(service, folder_id):
        return folder_id
    query = (
        "trashed = false and mimeType = 'application/vnd.google-apps.folder' and "
        f"name = '{_escape_drive_query(folder_name)}'"
    )
    response = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id,name)", pageSize=1)
        .execute(num_retries=0)
    )
    files = response.get("files", [])
    if files:
        return files[0]["id"]
    created = (
        service.files()
        .create(body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}, fields="id")
        .execute(num_retries=0)
    )
    return created["id"]


def drive_folder_exists(service, folder_id: str) -> bool:
    try:
        response = (
            service.files()
            .get(fileId=folder_id, fields="id,name,mimeType,trashed")
            .execute(num_retries=0)
        )
    except Exception:
        return False
    return bool(
        response.get("id")
        and not response.get("trashed")
        and response.get("mimeType") == "application/vnd.google-apps.folder"
    )


def get_drive_account_email(service) -> str | None:
    try:
        response = service.about().get(fields="user(emailAddress)").execute(num_retries=0)
    except Exception:
        return None
    email = ((response.get("user") or {}).get("emailAddress") or "").strip()
    return email or None


def google_drive_enabled_env_values(token_path: Path, folder_id: str) -> dict[str, str]:
    return {
        "GOOGLE_DRIVE_BACKUP_ENABLED": "True",
        "GOOGLE_DRIVE_TOKEN_FILE": str(token_path.resolve()),
        "GOOGLE_DRIVE_BACKUP_FOLDER_ID": folder_id,
        "GOOGLE_DRIVE_BACKUP_FOLDER_NAME": DRIVE_FOLDER_NAME,
        "GOOGLE_DRIVE_TIMEOUT_SECONDS": DRIVE_TIMEOUT_SECONDS,
    }


def google_drive_disabled_env_values() -> dict[str, str]:
    return {
        "GOOGLE_DRIVE_BACKUP_ENABLED": "False",
        "GOOGLE_DRIVE_TOKEN_FILE": "",
        "GOOGLE_DRIVE_BACKUP_FOLDER_ID": "",
        "GOOGLE_DRIVE_BACKUP_FOLDER_NAME": DRIVE_FOLDER_NAME,
        "GOOGLE_DRIVE_TIMEOUT_SECONDS": DRIVE_TIMEOUT_SECONDS,
    }


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
        LOGGER.warning("No se pudo ajustar ACL de Google Drive con icacls. Ruta=%s", path)


def _escape_drive_query(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")
