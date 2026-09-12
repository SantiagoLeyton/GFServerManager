from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import admin_panel
from app.deployment_config import (
    DeploymentConfigError,
    backup_env_values,
    detect_postgres_tools,
    gmail_env_values,
    prepare_backup_directory,
    validate_backup_directory,
    validate_gmail_settings,
)
from app.environment_manager import read_env, update_env
from app.user_manager import InitialUser, create_or_update_initial_users


class DeploymentConfigTests(unittest.TestCase):
    def test_selector_uses_native_windows_folder_dialog(self) -> None:
        self.assertIn("askdirectory", admin_panel.AdminPanel._browse_backup_folder.__code__.co_names)

    def test_gmail_test_uses_background_worker(self) -> None:
        self.assertIn("_run_background", admin_panel.AdminPanel._test_gmail_configuration.__code__.co_names)
        self.assertNotIn("test_gmail_smtp", admin_panel.AdminPanel._test_gmail_configuration.__code__.co_names)

    def test_valid_backup_folder_write_read_delete_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = validate_backup_directory(Path(temp_dir) / "Backups")
            self.assertTrue(path.is_dir())
            self.assertEqual(list(path.glob(".gfsm_*.tmp")), [])

    def test_missing_backup_folder_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "new" / "nested" / "backups"
            result = validate_backup_directory(path)
            self.assertTrue(result.is_dir())

    def test_file_path_is_rejected_as_backup_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "not-a-folder.txt"
            file_path.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(DeploymentConfigError, "no corresponde a una carpeta"):
                validate_backup_directory(file_path)

    def test_unusable_backup_folder_returns_friendly_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("app.deployment_config.tempfile.NamedTemporaryFile", side_effect=PermissionError):
                with self.assertRaisesRegex(DeploymentConfigError, "Verifique los permisos"):
                    validate_backup_directory(temp_dir)

    @unittest.skipUnless(os.name == "nt", "ACL preparation is Windows-specific")
    def test_prepare_backup_folder_grants_system_without_everyone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_result = mock.Mock(returncode=0)
            with mock.patch("app.deployment_config.run_hidden", return_value=fake_result) as run_hidden:
                prepare_backup_directory(temp_dir)
            command = run_hidden.call_args.args[0]
            self.assertIn("*S-1-5-18:(OI)(CI)M", command)
            self.assertIn("*S-1-5-32-544:(OI)(CI)M", command)
            self.assertNotIn("Everyone", " ".join(command))

    def test_update_backup_storage_path_preserves_other_env_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            env_path = project / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "DB_PASSWORD=keep-db",
                        "DJANGO_SECRET_KEY=keep-secret",
                        "BACKUP_STORAGE_PATH=C:\\Old",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            update_env(project, {"BACKUP_STORAGE_PATH": r"D:\GestionFiduciaria\Backups"}, backup_existing=True)
            values = read_env(project)
            self.assertEqual(values["BACKUP_STORAGE_PATH"], r"D:\GestionFiduciaria\Backups")
            self.assertEqual(values["DB_PASSWORD"], "keep-db")
            self.assertEqual(values["DJANGO_SECRET_KEY"], "keep-secret")
            self.assertEqual(env_path.read_text(encoding="utf-8").count("BACKUP_STORAGE_PATH="), 1)

    def test_pg_dump_and_pg_restore_detected_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            bin_dir = project / "pgbin"
            bin_dir.mkdir()
            suffix = ".exe" if os.name == "nt" else ""
            pg_dump = bin_dir / f"pg_dump{suffix}"
            pg_restore = bin_dir / f"pg_restore{suffix}"
            pg_dump.write_text("", encoding="utf-8")
            pg_restore.write_text("", encoding="utf-8")
            update_env(
                project,
                {
                    "BACKUP_PG_DUMP_PATH": str(pg_dump),
                    "BACKUP_PG_RESTORE_PATH": str(pg_restore),
                },
                backup_existing=False,
            )
            tools = detect_postgres_tools(project)
            self.assertEqual(tools.pg_dump, pg_dump.resolve())
            self.assertEqual(tools.pg_restore, pg_restore.resolve())

    def test_missing_postgresql_tools_returns_controlled_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            update_env(
                project,
                {
                    "BACKUP_PG_DUMP_PATH": str(project / "missing_pg_dump.exe"),
                    "BACKUP_PG_RESTORE_PATH": str(project / "missing_pg_restore.exe"),
                },
                backup_existing=False,
            )
            with mock.patch("app.deployment_config.shutil.which", return_value=None):
                with mock.patch("app.deployment_config._postgres_program_files_candidates", return_value=[]):
                    with self.assertRaisesRegex(DeploymentConfigError, "No fue posible detectar"):
                        detect_postgres_tools(project)

    def test_backup_pg_env_values_use_detected_paths(self) -> None:
        tools = mock.Mock(pg_dump=Path(r"C:\PostgreSQL\bin\pg_dump.exe"), pg_restore=Path(r"C:\PostgreSQL\bin\pg_restore.exe"))
        values = backup_env_values(Path(r"D:\Backups"), tools)
        self.assertEqual(values["BACKUP_STORAGE_PATH"], r"D:\Backups")
        self.assertIn("pg_dump", values["BACKUP_PG_DUMP_PATH"])
        self.assertIn("pg_restore", values["BACKUP_PG_RESTORE_PATH"])

    def test_gmail_password_is_saved_with_special_characters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            password = 'abcd efgh "ij#kl"'
            update_env(project, gmail_env_values("correo.empresa@gmail.com", password), backup_existing=False)
            values = read_env(project)
            self.assertEqual(values["EMAIL_HOST_PASSWORD"], password)
            self.assertEqual(values["EMAIL_HOST_USER"], "correo.empresa@gmail.com")
            self.assertEqual(values["DEFAULT_FROM_EMAIL"], "correo.empresa@gmail.com")
            self.assertEqual(values["SERVER_EMAIL"], "correo.empresa@gmail.com")

    def test_gmail_config_does_not_overwrite_unrelated_env_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            update_env(project, {"DB_PASSWORD": "keep-db", "DJANGO_SECRET_KEY": "keep-secret"}, backup_existing=False)
            update_env(project, gmail_env_values("correo.empresa@gmail.com", "gmail-secret"), backup_existing=True)
            values = read_env(project)
            self.assertEqual(values["DB_PASSWORD"], "keep-db")
            self.assertEqual(values["DJANGO_SECRET_KEY"], "keep-secret")
            self.assertEqual(values["EMAIL_HOST_PASSWORD"], "gmail-secret")

    def test_gmail_validation_requires_password_and_coherent_tls_ssl(self) -> None:
        with self.assertRaisesRegex(DeploymentConfigError, "contraseña de aplicación"):
            validate_gmail_settings(gmail_env_values("correo.empresa@gmail.com", ""))
        with self.assertRaisesRegex(DeploymentConfigError, "formato valido"):
            gmail_env_values("correo-invalido", "secret")
        values = gmail_env_values("correo.empresa@gmail.com", "secret")
        values["EMAIL_USE_SSL"] = "True"
        with self.assertRaisesRegex(DeploymentConfigError, "TLS y SSL"):
            validate_gmail_settings(values)

    def test_gmail_password_is_not_logged_by_env_helpers(self) -> None:
        logger = logging.getLogger("app.deployment_config")
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertNoLogs(logger, level="INFO"):
                update_env(Path(temp_dir), gmail_env_values("correo.empresa@gmail.com", "super-secret-value"), backup_existing=False)

    def test_gmail_account_can_be_changed_without_hardcoded_address(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            update_env(project, gmail_env_values("correo-a@gmail.com", "secret-a"), backup_existing=False)
            update_env(project, gmail_env_values("correo-b@gmail.com", "secret-b"), backup_existing=True)
            values = read_env(project)
            self.assertEqual(values["EMAIL_HOST_USER"], "correo-b@gmail.com")
            self.assertEqual(values["EMAIL_HOST_PASSWORD"], "secret-b")
            self.assertEqual(values["DEFAULT_FROM_EMAIL"], "correo-b@gmail.com")
            self.assertEqual(values["SERVER_EMAIL"], "correo-b@gmail.com")

    def test_gmail_smtp_sends_test_message_and_hides_password_in_error(self) -> None:
        from app.deployment_config import test_gmail_smtp

        values = gmail_env_values("correo.empresa@gmail.com", "super-secret-value")
        smtp_client = mock.Mock()
        smtp_context = mock.Mock()
        smtp_context.__enter__ = mock.Mock(return_value=smtp_client)
        smtp_context.__exit__ = mock.Mock(return_value=False)
        with mock.patch("app.deployment_config.smtplib.SMTP", return_value=smtp_context) as smtp_class:
            test_gmail_smtp(values, recipient="destino@gmail.com")
        smtp_class.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
        smtp_client.starttls.assert_called_once()
        smtp_client.login.assert_called_once_with("correo.empresa@gmail.com", "super-secret-value")
        smtp_client.send_message.assert_called_once()

        failing_client = mock.Mock()
        failing_client.login.side_effect = OSError("network")
        failing_context = mock.Mock()
        failing_context.__enter__ = mock.Mock(return_value=failing_client)
        failing_context.__exit__ = mock.Mock(return_value=False)
        with mock.patch("app.deployment_config.smtplib.SMTP", return_value=failing_context):
            with self.assertRaises(DeploymentConfigError) as raised:
                test_gmail_smtp(values, recipient="destino@gmail.com")
        self.assertNotIn("super-secret-value", str(raised.exception))

    def test_gmail_change_does_not_modify_drive_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project = Path(temp_dir)
            update_env(
                project,
                {
                    "GOOGLE_DRIVE_BACKUP_ENABLED": "True",
                    "GOOGLE_DRIVE_TOKEN_FILE": r"C:\ProgramData\GestionFiduciaria\GoogleDrive\token.json",
                    "GOOGLE_DRIVE_BACKUP_FOLDER_ID": "folder-id",
                },
                backup_existing=False,
            )
            update_env(project, gmail_env_values("correo.empresa@gmail.com", "smtp-secret"), backup_existing=True)
            values = read_env(project)
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_ENABLED"], "True")
            self.assertEqual(values["GOOGLE_DRIVE_BACKUP_FOLDER_ID"], "folder-id")

    def test_account_management_helpers_removed_but_bootstrap_remains(self) -> None:
        import app.user_manager as user_manager

        self.assertTrue(callable(create_or_update_initial_users))
        self.assertEqual(InitialUser.__annotations__["role"], "str")
        for name in ["list_users", "create_user", "change_password", "set_user_active"]:
            self.assertFalse(hasattr(user_manager, name))


if __name__ == "__main__":
    unittest.main()
