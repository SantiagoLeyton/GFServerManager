from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from app.environment_manager import read_env, update_env
from app.google_drive_config import (
    DRIVE_FOLDER_NAME,
    GoogleDriveAuthorizationRequired,
    GoogleDriveConfigError,
    configure_google_drive,
    configure_google_drive_later,
    drive_folder_exists,
    ensure_drive_folder,
    google_drive_disabled_env_values,
    google_drive_enabled_env_values,
    google_drive_paths,
    google_drive_status_from_env,
    load_drive_credentials,
    persist_authorized_credentials,
    test_google_drive_connection,
    validate_credentials_file,
)
from app.diagnostics import DiagnosticItem, _check_google_drive


def _credentials_file(path: Path, installed: bool = True) -> Path:
    data = {
        "installed" if installed else "web": {
            "client_id": "client-id.apps.googleusercontent.com",
            "client_secret": "secret-value",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _Execute:
    def __init__(self, value=None, exc: Exception | None = None):
        self.value = value or {}
        self.exc = exc

    def execute(self, num_retries=0):
        if self.exc:
            raise self.exc
        return self.value


class _Files:
    def __init__(self, service):
        self.service = service

    def list(self, **_kwargs):
        return _Execute({"files": list(self.service.files_list)})

    def create(self, body, fields):
        self.service.created.append(body)
        return _Execute({"id": self.service.created_id})

    def get(self, fileId, fields):
        if self.service.get_raises:
            return _Execute(exc=RuntimeError("revoked"))
        if fileId == self.service.existing_id:
            return _Execute(
                {
                    "id": fileId,
                    "name": DRIVE_FOLDER_NAME,
                    "mimeType": "application/vnd.google-apps.folder",
                    "trashed": False,
                }
            )
        return _Execute(exc=RuntimeError("missing"))


class _About:
    def __init__(self, email):
        self.email = email

    def get(self, fields):
        return _Execute({"user": {"emailAddress": self.email}})


class _Service:
    def __init__(self, files=None, created_id="created-folder", existing_id="existing-folder", get_raises=False):
        self.files_list = files or []
        self.created_id = created_id
        self.existing_id = existing_id
        self.get_raises = get_raises
        self.created = []

    def files(self):
        return _Files(self)

    def about(self):
        return _About("drive.user@example.com")


class _Credentials:
    def __init__(self, valid=True, expired=False, refresh_token="refresh-token"):
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self.refreshed = False

    def refresh(self, _request):
        self.valid = True
        self.expired = False
        self.refreshed = True

    def to_json(self):
        return json.dumps({"token": "access-token", "refresh_token": self.refresh_token})


class GoogleDriveConfigTests(unittest.TestCase):
    def test_credentials_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data = validate_credentials_file(_credentials_file(Path(temp_dir) / "credentials.json"))
            self.assertIn("installed", data)

    def test_credentials_invalid_and_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(GoogleDriveConfigError, "escritorio"):
                validate_credentials_file(_credentials_file(Path(temp_dir) / "web.json", installed=False))
            corrupt = Path(temp_dir) / "credentials.json"
            corrupt.write_text("{bad", encoding="utf-8")
            with self.assertRaisesRegex(GoogleDriveConfigError, "JSON valido"):
                validate_credentials_file(corrupt)

    def test_persist_authorized_credentials_writes_token_and_reuses_existing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token = Path(temp_dir) / "token.json"
            service = _Service(files=[{"id": "folder-existing", "name": DRIVE_FOLDER_NAME}])
            with mock.patch("app.google_drive_config.build_drive_service", return_value=service):
                result = persist_authorized_credentials(_Credentials(), token)
            self.assertEqual(result.folder_id, "folder-existing")
            self.assertTrue(token.exists())
            self.assertNotIn("secret-value", token.read_text(encoding="utf-8"))

    def test_oauth_cancelled_returns_controlled_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            credentials = _credentials_file(Path(temp_dir) / "credentials.json")
            fake_flow_class = mock.Mock()
            fake_flow_class.from_client_secrets_file.return_value.run_local_server.side_effect = RuntimeError("cancelled")
            module = types.SimpleNamespace(InstalledAppFlow=fake_flow_class)
            with mock.patch.dict(sys.modules, {"google_auth_oauthlib.flow": module}):
                from app.google_drive_config import authorize_google_drive

                with self.assertRaisesRegex(GoogleDriveConfigError, "cancelada"):
                    authorize_google_drive(credentials, Path(temp_dir) / "token.json")

    def test_oauth_success_uses_installed_app_flow_and_drive_file_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            credentials = _credentials_file(Path(temp_dir) / "credentials.json")
            token = Path(temp_dir) / "token.json"
            fake_flow = mock.Mock()
            fake_flow.run_local_server.return_value = _Credentials()
            fake_flow_class = mock.Mock()
            fake_flow_class.from_client_secrets_file.return_value = fake_flow
            module = types.SimpleNamespace(InstalledAppFlow=fake_flow_class)
            with mock.patch.dict(sys.modules, {"google_auth_oauthlib.flow": module}):
                with mock.patch("app.google_drive_config.build_drive_service", return_value=_Service()):
                    from app.google_drive_config import authorize_google_drive

                    result = authorize_google_drive(credentials, token)

            self.assertTrue(token.exists())
            self.assertEqual(result.folder_id, "created-folder")
            fake_flow_class.from_client_secrets_file.assert_called_once()
            self.assertEqual(
                fake_flow_class.from_client_secrets_file.call_args.kwargs["scopes"],
                ["https://www.googleapis.com/auth/drive.file"],
            )
            fake_flow.run_local_server.assert_called_once_with(port=0, prompt="consent")

    def test_folder_missing_is_created_and_existing_is_reused(self) -> None:
        existing_service = _Service(files=[{"id": "folder-id", "name": DRIVE_FOLDER_NAME}])
        self.assertEqual(ensure_drive_folder(existing_service), "folder-id")
        self.assertEqual(existing_service.created, [])

        missing_service = _Service(files=[], created_id="new-folder")
        self.assertEqual(ensure_drive_folder(missing_service), "new-folder")
        self.assertEqual(missing_service.created[0]["name"], DRIVE_FOLDER_NAME)

    def test_drive_folder_exists_validates_id(self) -> None:
        service = _Service(existing_id="folder-id")
        self.assertTrue(drive_folder_exists(service, "folder-id"))
        self.assertFalse(drive_folder_exists(service, "other-id"))

    def test_env_enabled_disabled_and_preserves_smtp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            update_env(project, {"EMAIL_HOST_USER": "smtp@gmail.com", "EMAIL_HOST_PASSWORD": "smtp-secret"}, backup_existing=False)
            update_env(project, google_drive_enabled_env_values(Path(temp_dir) / "token.json", "folder-id"))
            values = read_env(project)
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_ENABLED"], "True")
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_FOLDER_ID"], "folder-id")
            self.assertEqual(values["EMAIL_HOST_USER"], "smtp@gmail.com")
            self.assertEqual(values["EMAIL_HOST_PASSWORD"], "smtp-secret")
            update_env(project, google_drive_disabled_env_values())
            self.assertEqual(read_env(project)["GOOGLE_DRIVE_BACKUP_ENABLED"], "False")

    def test_configure_google_drive_with_new_credentials_replaces_token_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            old_token = root / "GoogleDrive" / "token.json"
            old_credentials = root / "GoogleDrive" / "credentials.json"
            old_token.parent.mkdir()
            old_token.write_text("old-token", encoding="utf-8")
            old_credentials.write_text("old-credentials", encoding="utf-8")
            new_credentials = _credentials_file(root / "new_credentials.json")
            paths = mock.Mock(root=old_token.parent, credentials=old_credentials, token=old_token)

            with mock.patch("app.google_drive_config.google_drive_paths", return_value=paths):
                with mock.patch("app.google_drive_config._prepare_windows_acl"):
                    with mock.patch("app.google_drive_config.authorize_google_drive") as authorize:
                        temp_token = old_token.with_suffix(".new.json")
                        temp_token.write_text('{"token": "new-token"}', encoding="utf-8")
                        authorize.return_value = mock.Mock(
                            token_path=temp_token,
                            folder_id="folder-id",
                            folder_name=DRIVE_FOLDER_NAME,
                            account_email="drive@example.com",
                        )
                        result = configure_google_drive(project, new_credentials)

            self.assertEqual(result.token_path, old_token.resolve())
            self.assertEqual(result.account_email, "drive@example.com")
            self.assertIn("new-token", old_token.read_text(encoding="utf-8"))
            self.assertIn("client-id", old_credentials.read_text(encoding="utf-8"))
            values = read_env(project)
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_ENABLED"], "True")
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_FOLDER_ID"], "folder-id")

    def test_configure_google_drive_failure_preserves_previous_token_and_smtp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            token = root / "GoogleDrive" / "token.json"
            credentials = root / "GoogleDrive" / "credentials.json"
            token.parent.mkdir()
            token.write_text("old-token", encoding="utf-8")
            credentials.write_text("old-credentials", encoding="utf-8")
            update_env(project, {"EMAIL_HOST_USER": "smtp@gmail.com", "EMAIL_HOST_PASSWORD": "smtp-secret"}, backup_existing=False)
            new_credentials = _credentials_file(root / "new_credentials.json")
            paths = mock.Mock(root=token.parent, credentials=credentials, token=token)

            with mock.patch("app.google_drive_config.google_drive_paths", return_value=paths):
                with mock.patch("app.google_drive_config.authorize_google_drive", side_effect=GoogleDriveConfigError("cancelada")):
                    with self.assertRaises(GoogleDriveConfigError):
                        configure_google_drive(project, new_credentials)

            self.assertEqual(token.read_text(encoding="utf-8"), "old-token")
            self.assertEqual(credentials.read_text(encoding="utf-8"), "old-credentials")
            values = read_env(project)
            self.assertEqual(values["EMAIL_HOST_USER"], "smtp@gmail.com")
            self.assertEqual(values["EMAIL_HOST_PASSWORD"], "smtp-secret")

    def test_configure_later_sets_safe_disabled_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            configure_google_drive_later(project)
            values = read_env(project)
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_ENABLED"], "False")
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_FOLDER_NAME"], DRIVE_FOLDER_NAME)

    def test_load_credentials_refreshes_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token = Path(temp_dir) / "token.json"
            token.write_text("{}", encoding="utf-8")
            credentials = _Credentials(valid=False, expired=True)
            credentials_module = types.SimpleNamespace(
                Credentials=types.SimpleNamespace(from_authorized_user_file=mock.Mock(return_value=credentials))
            )
            request_module = types.SimpleNamespace(Request=mock.Mock(return_value=object()))
            with mock.patch.dict(
                sys.modules,
                {
                    "google.oauth2.credentials": credentials_module,
                    "google.auth.transport.requests": request_module,
                },
            ):
                loaded = load_drive_credentials(token)
            self.assertIs(loaded, credentials)
            self.assertIn("access-token", token.read_text(encoding="utf-8"))

    def test_load_credentials_revoked_requires_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            token = Path(temp_dir) / "token.json"
            token.write_text("{}", encoding="utf-8")
            credentials = _Credentials(valid=False, expired=False, refresh_token=None)
            credentials_module = types.SimpleNamespace(
                Credentials=types.SimpleNamespace(from_authorized_user_file=mock.Mock(return_value=credentials))
            )
            request_module = types.SimpleNamespace(Request=mock.Mock(return_value=object()))
            with mock.patch.dict(
                sys.modules,
                {
                    "google.oauth2.credentials": credentials_module,
                    "google.auth.transport.requests": request_module,
                },
            ):
                with self.assertRaises(GoogleDriveAuthorizationRequired):
                    load_drive_credentials(token)

    def test_test_google_drive_connection_updates_missing_folder_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            token = Path(temp_dir) / "token.json"
            token.write_text("{}", encoding="utf-8")
            update_env(
                project,
                {
                    "GOOGLE_DRIVE_BACKUP_ENABLED": "True",
                    "GOOGLE_DRIVE_TOKEN_FILE": str(token),
                    "GOOGLE_DRIVE_BACKUP_FOLDER_ID": "",
                    "GOOGLE_DRIVE_BACKUP_FOLDER_NAME": DRIVE_FOLDER_NAME,
                    "GOOGLE_DRIVE_TIMEOUT_SECONDS": "20",
                },
                backup_existing=False,
            )
            with mock.patch("app.google_drive_config.load_drive_credentials", return_value=_Credentials()):
                with mock.patch("app.google_drive_config.build_drive_service", return_value=_Service(created_id="created-folder")):
                    with mock.patch("app.google_drive_config.google_drive_paths") as paths:
                        paths.return_value = mock.Mock(credentials=Path(temp_dir) / "credentials.json", token=token)
                        status = test_google_drive_connection(project)
            self.assertEqual(status.folder_id, "created-folder")
            self.assertEqual(read_env(project)["GOOGLE_DRIVE_BACKUP_FOLDER_ID"], "created-folder")

    def test_persistent_paths_are_outside_repository_and_under_programdata_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("app.google_drive_config.app_data_dir", return_value=Path(temp_dir) / "GFServerManager"):
                with mock.patch("app.google_drive_config._prepare_windows_acl"):
                    paths = google_drive_paths()
            self.assertEqual(paths.credentials.name, "credentials.json")
            self.assertEqual(paths.token.name, "token.json")
            self.assertIn("GoogleDrive", str(paths.root))

    def test_status_from_env_does_not_prepare_acl_or_create_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir) / "project"
            project.mkdir()
            app_data = Path(temp_dir) / "ProgramData"
            update_env(project, google_drive_disabled_env_values(), backup_existing=False)
            with mock.patch("app.google_drive_config.app_data_dir", return_value=app_data):
                with mock.patch("app.google_drive_config._prepare_windows_acl") as prepare_acl:
                    status = google_drive_status_from_env(project)

            self.assertFalse(status.configured)
            self.assertFalse((app_data / "GoogleDrive").exists())
            prepare_acl.assert_not_called()

    def test_google_drive_diagnostic_reports_pending_and_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            update_env(project, google_drive_disabled_env_values(), backup_existing=False)
            pending: list[DiagnosticItem] = []
            _check_google_drive(pending, project)
            self.assertEqual(pending[0].name, "Google Drive")
            self.assertEqual(pending[0].level, "warning")

            ok: list[DiagnosticItem] = []
            with mock.patch("app.diagnostics.google_drive_status_from_env") as status:
                status.return_value = mock.Mock(
                    enabled=True,
                    credentials_present=True,
                    token_present=True,
                    folder_id="folder-id",
                )
                with mock.patch("app.diagnostics.test_google_drive_connection") as connection:
                    connection.return_value = mock.Mock(
                        message="Conexion con Google Drive correcta.",
                        folder_name=DRIVE_FOLDER_NAME,
                    )
                    _check_google_drive(ok, project)
            self.assertTrue(all(item.level == "ok" for item in ok))


if __name__ == "__main__":
    unittest.main()
