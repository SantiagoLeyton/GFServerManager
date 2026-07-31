from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog
from tkinter import scrolledtext
from tkinter import ttk

from .config_manager import build_config, save_config
from .database_manager import (
    AdminDatabaseCredentials,
    DatabaseCredentials,
    ensure_database,
    require_connection,
)
from .django_manager import collectstatic, ensure_virtualenv, find_static_probe_url, install_dependencies, migrate
from .environment_manager import build_env_values, write_env
from .metadata import PRODUCT_NAME
from .project_validator import validate_project
from .requirements_checker import check_requirements
from .server_manager import find_waitress_listener_pid, start_waitress, wait_for_http, wait_for_static_file
from .ui_components import Dialogs, apply_app_icon, attach_tooltip, configure_styles
from .user_manager import InitialUser, create_or_update_initial_users


LOGGER = logging.getLogger(__name__)


class InstallWizard(ttk.Frame):
    def __init__(self, master: Tk, on_complete=None, on_cancel=None) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.on_complete = on_complete
        self.on_cancel = on_cancel
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.confirm_queue: queue.Queue[bool] = queue.Queue()
        self.running = False
        self._closed = False
        self._poll_after_id: str | None = None

        self.project_path = StringVar(value=str((Path.cwd().parent / "PagosFiducia").resolve()))
        self.db_mode = StringVar(value="existing")
        self.db_host = StringVar(value="localhost")
        self.db_port = IntVar(value=5432)
        self.db_name = StringVar(value="pagos_fiducia")
        self.db_user = StringVar(value="postgres")
        self.db_password = StringVar()
        self.admin_user = StringVar(value="postgres")
        self.admin_password = StringVar()
        self.owner_user = StringVar(value="pagos_fiducia_owner")
        self.owner_password = StringVar()
        self.port = IntVar(value=8000)
        self.accounting_email = StringVar(value="contabilidad@correo.com")
        self.accounting_password = StringVar()
        self.accounting_password_confirm = StringVar()
        self.commercial_email = StringVar(value="comercial@correo.com")
        self.commercial_password = StringVar()
        self.commercial_password_confirm = StringVar()
        self.update_existing_passwords = BooleanVar(value=False)
        self.operation_message = StringVar(value="Listo para instalar")
        self.operation_progress: ttk.Progressbar | None = None

        self._build()
        self._schedule_poll()
        self._run_requirement_check()

    def _build(self) -> None:
        self.master.title(f"{PRODUCT_NAME} - Instalacion")
        configure_styles()
        apply_app_icon(self.master)
        self.master.geometry("900x720")
        self.master.minsize(860, 640)
        self.grid(sticky="nsew")
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        notebook = ttk.Notebook(self)
        notebook.grid(row=0, column=0, sticky="nsew")

        self.project_tab = ttk.Frame(notebook, padding=12)
        self.database_tab = ttk.Frame(notebook, padding=12)
        self.users_tab = ttk.Frame(notebook, padding=12)
        self.run_tab = ttk.Frame(notebook, padding=12)
        notebook.add(self.project_tab, text="Proyecto")
        notebook.add(self.database_tab, text="Base de datos")
        notebook.add(self.users_tab, text="Usuarios")
        notebook.add(self.run_tab, text="Instalar")

        self._build_project_tab()
        self._build_database_tab()
        self._build_users_tab()
        self._build_run_tab()

    def _build_project_tab(self) -> None:
        self.project_tab.columnconfigure(1, weight=1)
        ttk.Label(self.project_tab, text="Carpeta de Gestion Fiduciaria").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.project_tab, textvariable=self.project_path).grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        browse = ttk.Button(self.project_tab, text="Examinar", command=self._browse_project)
        validate = ttk.Button(self.project_tab, text="Validar proyecto", command=self._validate_project_clicked)
        browse.grid(row=1, column=2, padx=(8, 0))
        validate.grid(row=2, column=0, sticky="w")
        attach_tooltip(browse, "Seleccione la carpeta real del proyecto Django Gestion Fiduciaria.")
        attach_tooltip(validate, "Comprueba que la carpeta contiene la configuracion esperada.")

        self.project_status = scrolledtext.ScrolledText(self.project_tab, height=18, wrap="word")
        self.project_status.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(12, 0))
        self.project_tab.rowconfigure(3, weight=1)

    def _build_database_tab(self) -> None:
        self.database_tab.columnconfigure(1, weight=1)
        ttk.Radiobutton(
            self.database_tab,
            text="La base de datos ya existe",
            variable=self.db_mode,
            value="existing",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(
            self.database_tab,
            text="Crear rol/base de datos si no existen",
            variable=self.db_mode,
            value="create",
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        fields = [
            ("Host (DB_HOST)", self.db_host, False),
            ("Puerto (DB_PORT)", self.db_port, False),
            ("Base de datos (DB_NAME)", self.db_name, False),
            ("Usuario de la base de datos (DB_USER)", self.db_user, False),
            ("Contraseña de la base de datos (DB_PASSWORD)", self.db_password, True),
            ("Administrador PostgreSQL", self.admin_user, False),
            ("Contraseña del administrador", self.admin_password, True),
            ("Nuevo usuario (Owner)", self.owner_user, False),
            ("Contraseña del nuevo usuario", self.owner_password, True),
        ]
        for index, (label, variable, secret) in enumerate(fields, start=2):
            ttk.Label(self.database_tab, text=label).grid(row=index, column=0, sticky="w", pady=4)
            ttk.Entry(self.database_tab, textvariable=variable, show="*" if secret else "").grid(
                row=index, column=1, sticky="ew", pady=4
            )

        test_button = ttk.Button(self.database_tab, text="Probar conexion", command=self._test_database_clicked)
        test_button.grid(
            row=12, column=0, sticky="w", pady=(12, 0)
        )
        attach_tooltip(test_button, "Verifica que los datos permiten conectar con PostgreSQL.")

    def _build_users_tab(self) -> None:
        self.users_tab.columnconfigure(1, weight=1)
        rows = [
            ("Correo Contabilidad", self.accounting_email, False),
            ("Contrasena Contabilidad", self.accounting_password, True),
            ("Confirmar Contabilidad", self.accounting_password_confirm, True),
            ("Correo Comercial", self.commercial_email, False),
            ("Contrasena Comercial", self.commercial_password, True),
            ("Confirmar Comercial", self.commercial_password_confirm, True),
        ]
        for index, (label, variable, secret) in enumerate(rows):
            ttk.Label(self.users_tab, text=label).grid(row=index, column=0, sticky="w", pady=4)
            ttk.Entry(self.users_tab, textvariable=variable, show="*" if secret else "").grid(
                row=index, column=1, sticky="ew", pady=4
            )
        ttk.Checkbutton(
            self.users_tab,
            text="Actualizar contrasenas si los usuarios ya existen",
            variable=self.update_existing_passwords,
        ).grid(row=len(rows), column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_run_tab(self) -> None:
        self.run_tab.columnconfigure(0, weight=1)
        port_frame = ttk.Frame(self.run_tab)
        port_frame.grid(row=0, column=0, sticky="ew")
        ttk.Label(port_frame, text="Puerto Waitress").pack(side="left")
        ttk.Spinbox(port_frame, from_=1024, to=65535, textvariable=self.port, width=8).pack(side="left", padx=8)

        ttk.Label(
            self.run_tab,
            text="El Firewall de Windows debe permitir este puerto para que otros equipos accedan.",
        ).grid(row=1, column=0, sticky="w", pady=(8, 12))

        self.install_button = ttk.Button(self.run_tab, text="Instalar y arrancar", command=self._install_clicked)
        self.install_button.grid(row=2, column=0, sticky="w")
        attach_tooltip(self.install_button, "Ejecuta instalacion, migraciones, archivos estaticos y arranque del servidor.")
        if self.on_cancel:
            cancel = ttk.Button(self.run_tab, text="Cancelar reinstalacion", command=self._cancel_clicked)
            cancel.grid(
                row=2, column=0, sticky="w", padx=(150, 0)
            )
            attach_tooltip(cancel, "Vuelve al panel sin modificar la configuracion actual.")

        status_frame = ttk.Frame(self.run_tab)
        status_frame.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(status_frame, textvariable=self.operation_message, style="Muted.TLabel").pack(side="left")
        self.operation_progress = ttk.Progressbar(status_frame, mode="indeterminate", length=180)
        self.operation_progress.pack(side="right")

        self.output = scrolledtext.ScrolledText(self.run_tab, height=24, wrap="word")
        self.output.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        self.run_tab.rowconfigure(4, weight=1)

    def _browse_project(self) -> None:
        selected = filedialog.askdirectory(title="Seleccionar carpeta de Gestion Fiduciaria")
        if selected:
            self.project_path.set(selected)

    def _run_requirement_check(self) -> None:
        status = check_requirements()
        self._write_project_status("Requisitos del Server Manager:\n" + "\n".join(status.messages) + "\n")

    def _validate_project_clicked(self) -> None:
        inspection = validate_project(self.project_path.get())
        lines = [
            "Validacion de Gestion Fiduciaria",
            f"Resultado: {'correcto' if inspection.valid else 'con errores'}",
            f"WSGI: {inspection.wsgi_module}",
            f"USERNAME_FIELD: {inspection.username_field}",
            f"Campos obligatorios: {', '.join(inspection.required_user_fields)}",
            "Roles reales: Contabilidad=accounting_admin, Comercial=commercial",
            f"Estaticos: {inspection.static_root}",
            f"Media: {inspection.media_root}",
            "Variables .env: " + ", ".join(inspection.env_keys),
        ]
        if inspection.errors:
            lines.append("\nErrores:")
            lines.extend(f"- {error}" for error in inspection.errors)
        self._write_project_status("\n".join(lines) + "\n")

    def _test_database_clicked(self) -> None:
        try:
            self._set_busy(True, "Probando conexion a base de datos...")
            self._validate_database_inputs()
            credentials, messages = self._prepare_database_for_current_mode()
            require_connection(credentials)
            message = "\n".join(messages + ["Conexion validada correctamente."])
            Dialogs.success("Base de datos", message)
        except Exception as exc:
            Dialogs.error("Base de datos", str(exc))
        finally:
            self._set_busy(False, "Listo para instalar")

    def _install_clicked(self) -> None:
        if self.running:
            return
        try:
            self._validate_inputs()
            snapshot = self._installation_snapshot()
            env_path = Path(snapshot["project_path"]) / ".env"
            snapshot["backup_existing"] = False
            if env_path.exists():
                accepted = Dialogs.confirm(
                    ".env existente",
                    "Ya existe un archivo .env. Se creara un respaldo con fecha y hora antes de sobrescribirlo. Desea continuar?",
                )
                if not accepted:
                    Dialogs.info("Instalacion", "Instalacion cancelada para no sobrescribir .env.")
                    return
                snapshot["backup_existing"] = True
        except Exception as exc:
            Dialogs.error("Validacion", str(exc))
            return
        self.running = True
        self._set_busy(True, "Instalacion en curso...")
        self.install_button.configure(state="disabled")
        thread = threading.Thread(target=self._install_worker, args=(snapshot,), daemon=True)
        thread.start()

    def _install_worker(self, snapshot: dict[str, object]) -> None:
        success = False
        try:
            project_path = Path(str(snapshot["project_path"])).resolve()
            port = int(snapshot["port"])
            self._log("Validando proyecto...")
            inspection = validate_project(project_path)
            if not inspection.valid:
                raise RuntimeError("\n".join(inspection.errors))

            credentials, messages = self._prepare_database_for_snapshot(snapshot)
            for message in messages:
                self._log(message)

            self._log("Comprobando conexion a PostgreSQL...")
            require_connection(credentials)

            self._log("Generando .env sin registrar secretos...")
            env_values = build_env_values(credentials, port)
            env_result = write_env(project_path, env_values, backup_existing=bool(snapshot["backup_existing"]))
            if env_result.backup_path:
                self._log(f"Respaldo creado: {env_result.backup_path.name}")

            self._log("Preparando entorno virtual...")
            venv = ensure_virtualenv(project_path)
            self._log("Instalando dependencias del proyecto y Waitress...")
            install_dependencies(project_path, venv)
            self._log("Ejecutando migraciones...")
            migrate(project_path, venv)
            self._log("Recolectando archivos estaticos...")
            collectstatic(project_path, venv)
            static_probe_url = find_static_probe_url(project_path, venv)
            self._log("Creando o actualizando usuarios iniciales...")
            user_output = create_or_update_initial_users(
                project_path,
                venv,
                self._initial_users(snapshot),
                update_existing=bool(snapshot["update_existing_passwords"]),
            )
            if user_output:
                self._log(user_output)

            server_pid = find_waitress_listener_pid(port, project_path)
            if server_pid:
                self._log("Waitress ya esta ejecutandose; se reutilizara el proceso activo.")
            else:
                self._log("Iniciando Waitress...")
                process = start_waitress(project_path, venv, port)
            self._log("Verificando respuesta HTTP local...")
            if not wait_for_http(port):
                raise RuntimeError("Waitress inicio, pero la aplicacion no respondio en el tiempo esperado.")
            self._log("Verificando archivo estatico real...")
            if not wait_for_static_file(port, static_probe_url):
                raise RuntimeError("La aplicacion respondio, pero no se pudo servir un archivo estatico real.")

            server_pid = find_waitress_listener_pid(port, project_path) or server_pid
            if not server_pid:
                raise RuntimeError("No se pudo identificar el PID de Waitress despues del inicio.")
            config = build_config(project_path, venv, port, pid=server_pid)
            save_config(config)
            self._log("")
            self._log("Instalacion finalizada correctamente.")
            LOGGER.info("Instalacion/reinstalacion finalizada correctamente para %s", project_path)
            self._log(f"URL por IP: http://{config.ipv4}:{port}")
            self._log(f"URL por hostname: http://{config.hostname}:{port}")
            self._log("Recuerde permitir el puerto en el Firewall de Windows para acceso desde otros equipos.")
            success = True
        except Exception as exc:
            LOGGER.exception("Fallo la instalacion de Fase 1")
            self._log(f"ERROR: {exc}")
        finally:
            self.queue.put(("done", "success" if success else "failed"))

    def _validate_inputs(self) -> None:
        if not self.project_path.get().strip():
            raise ValueError("Seleccione la carpeta de Gestion Fiduciaria.")
        if int(self.port.get()) < 1024 or int(self.port.get()) > 65535:
            raise ValueError("El puerto debe estar entre 1024 y 65535.")
        for label, value in [
            ("Correo Contabilidad", self.accounting_email.get()),
            ("Correo Comercial", self.commercial_email.get()),
        ]:
            if "@" not in value:
                raise ValueError(f"{label} no parece un correo valido.")
        if self.accounting_password.get() != self.accounting_password_confirm.get():
            raise ValueError("Las contrasenas de Contabilidad no coinciden.")
        if self.commercial_password.get() != self.commercial_password_confirm.get():
            raise ValueError("Las contrasenas de Comercial no coinciden.")
        if not self.accounting_password.get() or not self.commercial_password.get():
            raise ValueError("Digite las contrasenas iniciales.")
        self._validate_database_inputs()

    def _validate_database_inputs(self) -> None:
        if not self.db_name.get().strip():
            raise ValueError("Digite el nombre de la base de datos.")
        if int(self.db_port.get()) < 1 or int(self.db_port.get()) > 65535:
            raise ValueError("El puerto de PostgreSQL debe estar entre 1 y 65535.")
        if self.db_mode.get() == "existing":
            if not self.db_user.get().strip():
                raise ValueError("Digite el usuario de la base de datos.")
            if not self.db_password.get():
                raise ValueError("Digite la contrasena de la base de datos.")
        if self.db_mode.get() == "create":
            if not self.admin_user.get().strip() or not self.owner_user.get().strip() or not self.db_name.get().strip():
                raise ValueError("Digite administrador PostgreSQL, nuevo usuario owner y nombre de base de datos.")
            if not self.admin_password.get() or not self.owner_password.get():
                raise ValueError("Digite las contrasenas de PostgreSQL necesarias.")

    def _database_credentials(self) -> DatabaseCredentials:
        return DatabaseCredentials(
            host=self.db_host.get().strip(),
            port=int(self.db_port.get()),
            database=self.db_name.get().strip(),
            user=self.db_user.get().strip(),
            password=self.db_password.get(),
        )

    def _admin_database_credentials(self) -> AdminDatabaseCredentials:
        return AdminDatabaseCredentials(
            host=self.db_host.get().strip(),
            port=int(self.db_port.get()),
            user=self.admin_user.get().strip(),
            password=self.admin_password.get(),
        )

    def _owner_database_credentials(self) -> DatabaseCredentials:
        return DatabaseCredentials(
            host=self.db_host.get().strip(),
            port=int(self.db_port.get()),
            database=self.db_name.get().strip(),
            user=self.owner_user.get().strip(),
            password=self.owner_password.get(),
        )

    def _prepare_database_for_current_mode(self) -> tuple[DatabaseCredentials, list[str]]:
        if self.db_mode.get() == "existing":
            return self._database_credentials(), []
        admin = self._admin_database_credentials()
        messages = ensure_database(
            admin,
            self.db_name.get().strip(),
            self.owner_user.get().strip(),
            self.owner_password.get(),
        )
        return self._owner_database_credentials(), messages

    def _prepare_database_for_snapshot(self, snapshot: dict[str, object]) -> tuple[DatabaseCredentials, list[str]]:
        if snapshot["db_mode"] == "existing":
            return (
                DatabaseCredentials(
                    host=str(snapshot["db_host"]),
                    port=int(snapshot["db_port"]),
                    database=str(snapshot["db_name"]),
                    user=str(snapshot["db_user"]),
                    password=str(snapshot["db_password"]),
                ),
                [],
            )
        self._log("Preparando rol/base de datos...")
        admin = AdminDatabaseCredentials(
            host=str(snapshot["db_host"]),
            port=int(snapshot["db_port"]),
            user=str(snapshot["admin_user"]),
            password=str(snapshot["admin_password"]),
        )
        messages = ensure_database(
            admin,
            str(snapshot["db_name"]),
            str(snapshot["owner_user"]),
            str(snapshot["owner_password"]),
        )
        return (
            DatabaseCredentials(
                host=str(snapshot["db_host"]),
                port=int(snapshot["db_port"]),
                database=str(snapshot["db_name"]),
                user=str(snapshot["owner_user"]),
                password=str(snapshot["owner_password"]),
            ),
            messages,
        )

    def _installation_snapshot(self) -> dict[str, object]:
        return {
            "project_path": self.project_path.get().strip(),
            "db_mode": self.db_mode.get(),
            "db_host": self.db_host.get().strip(),
            "db_port": int(self.db_port.get()),
            "db_name": self.db_name.get().strip(),
            "db_user": self.db_user.get().strip(),
            "db_password": self.db_password.get(),
            "admin_user": self.admin_user.get().strip(),
            "admin_password": self.admin_password.get(),
            "owner_user": self.owner_user.get().strip(),
            "owner_password": self.owner_password.get(),
            "port": int(self.port.get()),
            "accounting_email": self.accounting_email.get().strip().lower(),
            "accounting_password": self.accounting_password.get(),
            "commercial_email": self.commercial_email.get().strip().lower(),
            "commercial_password": self.commercial_password.get(),
            "update_existing_passwords": self.update_existing_passwords.get(),
        }

    def _initial_users(self, snapshot: dict[str, object]) -> list[InitialUser]:
        return [
            InitialUser(
                label="Contabilidad",
                username=_username_from_email(str(snapshot["accounting_email"])),
                email=str(snapshot["accounting_email"]),
                password=str(snapshot["accounting_password"]),
                role="accounting_admin",
            ),
            InitialUser(
                label="Comercial",
                username=_username_from_email(str(snapshot["commercial_email"])),
                email=str(snapshot["commercial_email"]),
                password=str(snapshot["commercial_password"]),
                role="commercial",
            ),
        ]

    def _schedule_poll(self) -> None:
        if self._closed or self._poll_after_id is not None or not self._widget_exists(self.master):
            return
        self._poll_after_id = self.master.after(150, self._poll_queue)

    def _poll_queue(self) -> None:
        self._poll_after_id = None
        if self._closed or not self._widget_exists(self):
            return
        try:
            while True:
                item_type, payload = self.queue.get_nowait()
                if item_type == "log":
                    self.operation_message.set(payload or "Instalacion en curso...")
                    if self._widget_exists(self.output):
                        self.output.insert("end", payload + "\n")
                        self.output.see("end")
                elif item_type == "done":
                    self.running = False
                    if self._widget_exists(self.install_button):
                        self.install_button.configure(state="normal")
                    if payload == "success":
                        self._set_busy(False, "Instalacion finalizada correctamente")
                        self._finish_success()
                        return
                    self._set_busy(False, "La instalacion no se completo")
        except queue.Empty:
            pass
        self._schedule_poll()

    def _finish_success(self) -> None:
        if self.on_complete:
            self._dispose()
            self.on_complete()

    def _cancel_clicked(self) -> None:
        if self.running:
            Dialogs.warning("Reinstalacion", "No se puede cancelar mientras la instalacion esta en ejecucion.")
            return
        if self.on_cancel:
            self._dispose()
            self.on_cancel()

    def _log(self, message: str) -> None:
        LOGGER.info(_sanitize(message))
        self.queue.put(("log", message))

    def _write_project_status(self, text: str) -> None:
        if self._widget_exists(self.project_status):
            self.project_status.delete("1.0", "end")
            self.project_status.insert("end", text)

    def _set_busy(self, running: bool, message: str) -> None:
        self.operation_message.set(message)
        if self._widget_exists(self.operation_progress):
            if running:
                self.operation_progress.start(12)
            else:
                self.operation_progress.stop()

    def _dispose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_after_id and self._widget_exists(self.master):
            try:
                self.master.after_cancel(self._poll_after_id)
            except Exception:
                LOGGER.debug("No se pudo cancelar callback after del asistente", exc_info=True)
        self._poll_after_id = None

    def destroy(self) -> None:
        self._dispose()
        super().destroy()

    def _widget_exists(self, widget) -> bool:
        if widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except Exception:
            return False


def _username_from_email(email: str) -> str:
    local = email.strip().lower().split("@", 1)[0]
    return "".join(char for char in local if char.isalnum() or char in "._-") or "usuario"


def _sanitize(message: str) -> str:
    lower = message.lower()
    if "password" in lower or "contrasena" in lower or "secret" in lower:
        return "[mensaje sensible omitido]"
    return message
