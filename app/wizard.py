from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import scrolledtext
from tkinter import ttk

from .config_manager import build_config, detect_hostname, detect_local_ipv4, save_config
from .database_manager import (
    AdminDatabaseCredentials,
    DatabaseCredentials,
    check_connection,
    ensure_database,
)
from .django_manager import collectstatic, ensure_virtualenv, install_dependencies, migrate
from .environment_manager import build_env_values, write_env
from .project_validator import validate_project
from .requirements_checker import check_requirements
from .server_manager import start_waitress, wait_for_http
from .user_manager import InitialUser, create_or_update_initial_users


LOGGER = logging.getLogger(__name__)


class InstallWizard(ttk.Frame):
    def __init__(self, master: Tk) -> None:
        super().__init__(master, padding=16)
        self.master = master
        self.queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.confirm_queue: queue.Queue[bool] = queue.Queue()
        self.running = False

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

        self._build()
        self._poll_queue()
        self._run_requirement_check()

    def _build(self) -> None:
        self.master.title("Gestion Fiduciaria Server Manager - Instalacion")
        self.master.geometry("900x720")
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
        ttk.Button(self.project_tab, text="Examinar", command=self._browse_project).grid(row=1, column=2, padx=(8, 0))
        ttk.Button(self.project_tab, text="Validar proyecto", command=self._validate_project_clicked).grid(row=2, column=0, sticky="w")

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
            ("Host", self.db_host, False),
            ("Puerto", self.db_port, False),
            ("Base de datos", self.db_name, False),
            ("Usuario/owner", self.db_user, False),
            ("Contrasena usuario/owner", self.db_password, True),
            ("Admin PostgreSQL", self.admin_user, False),
            ("Contrasena admin", self.admin_password, True),
            ("Owner a crear/usar", self.owner_user, False),
            ("Contrasena owner", self.owner_password, True),
        ]
        for index, (label, variable, secret) in enumerate(fields, start=2):
            ttk.Label(self.database_tab, text=label).grid(row=index, column=0, sticky="w", pady=4)
            ttk.Entry(self.database_tab, textvariable=variable, show="*" if secret else "").grid(
                row=index, column=1, sticky="ew", pady=4
            )

        ttk.Button(self.database_tab, text="Probar conexion", command=self._test_database_clicked).grid(
            row=12, column=0, sticky="w", pady=(12, 0)
        )

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

        self.output = scrolledtext.ScrolledText(self.run_tab, height=26, wrap="word")
        self.output.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        self.run_tab.rowconfigure(3, weight=1)

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
            credentials = self._database_credentials()
            check_connection(credentials)
            messagebox.showinfo("Base de datos", "Conexion comprobada correctamente.")
        except Exception as exc:
            messagebox.showerror("Base de datos", str(exc))

    def _install_clicked(self) -> None:
        if self.running:
            return
        try:
            self._validate_inputs()
        except Exception as exc:
            messagebox.showerror("Validacion", str(exc))
            return
        self.running = True
        self.install_button.configure(state="disabled")
        thread = threading.Thread(target=self._install_worker, daemon=True)
        thread.start()

    def _install_worker(self) -> None:
        try:
            project_path = Path(self.project_path.get()).resolve()
            port = int(self.port.get())
            self._log("Validando proyecto...")
            inspection = validate_project(project_path)
            if not inspection.valid:
                raise RuntimeError("\n".join(inspection.errors))

            credentials = self._database_credentials()
            if self.db_mode.get() == "create":
                self._log("Preparando rol/base de datos...")
                admin = AdminDatabaseCredentials(
                    host=self.db_host.get().strip(),
                    port=int(self.db_port.get()),
                    user=self.admin_user.get().strip(),
                    password=self.admin_password.get(),
                )
                messages = ensure_database(
                    admin,
                    self.db_name.get().strip(),
                    self.owner_user.get().strip(),
                    self.owner_password.get(),
                )
                for message in messages:
                    self._log(message)
                credentials = DatabaseCredentials(
                    host=self.db_host.get().strip(),
                    port=int(self.db_port.get()),
                    database=self.db_name.get().strip(),
                    user=self.owner_user.get().strip(),
                    password=self.owner_password.get(),
                )

            self._log("Comprobando conexion a PostgreSQL...")
            check_connection(credentials)

            env_path = project_path / ".env"
            backup_existing = False
            if env_path.exists():
                self._ask_env_confirmation()
                if not self.confirm_queue.get():
                    raise RuntimeError("Instalacion cancelada para no sobrescribir .env.")
                backup_existing = True

            self._log("Generando .env sin registrar secretos...")
            env_values = build_env_values(credentials, port)
            env_result = write_env(project_path, env_values, backup_existing=backup_existing)
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
            self._log("Creando o actualizando usuarios iniciales...")
            user_output = create_or_update_initial_users(
                project_path,
                venv,
                self._initial_users(),
                update_existing=self.update_existing_passwords.get(),
            )
            if user_output:
                self._log(user_output)

            self._log("Iniciando Waitress...")
            process = start_waitress(project_path, venv, port)
            self._log("Verificando respuesta HTTP local...")
            if not wait_for_http(port):
                raise RuntimeError("Waitress inicio, pero la aplicacion no respondio en el tiempo esperado.")

            config = build_config(project_path, venv, port, pid=process.pid)
            save_config(config)
            self._log("")
            self._log("Instalacion finalizada correctamente.")
            self._log(f"URL por IP: http://{config.ipv4}:{port}")
            self._log(f"URL por hostname: http://{config.hostname}:{port}")
            self._log("Recuerde permitir el puerto en el Firewall de Windows para acceso desde otros equipos.")
        except Exception as exc:
            LOGGER.exception("Fallo la instalacion de Fase 1")
            self._log(f"ERROR: {exc}")
        finally:
            self.queue.put(("done", ""))

    def _ask_env_confirmation(self) -> None:
        def ask() -> None:
            accepted = messagebox.askyesno(
                ".env existente",
                "Ya existe un archivo .env. Se creara un respaldo con fecha y hora antes de sobrescribirlo. Desea continuar?",
            )
            self.confirm_queue.put(accepted)

        self.master.after(0, ask)

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
        if self.db_mode.get() == "existing" and not self.db_password.get():
            raise ValueError("Digite la contrasena de la base de datos.")
        if self.db_mode.get() == "create" and (not self.admin_password.get() or not self.owner_password.get()):
            raise ValueError("Digite las contrasenas de PostgreSQL necesarias.")

    def _database_credentials(self) -> DatabaseCredentials:
        return DatabaseCredentials(
            host=self.db_host.get().strip(),
            port=int(self.db_port.get()),
            database=self.db_name.get().strip(),
            user=self.db_user.get().strip(),
            password=self.db_password.get(),
        )

    def _initial_users(self) -> list[InitialUser]:
        return [
            InitialUser(
                label="Contabilidad",
                username=_username_from_email(self.accounting_email.get()),
                email=self.accounting_email.get().strip().lower(),
                password=self.accounting_password.get(),
                role="accounting_admin",
            ),
            InitialUser(
                label="Comercial",
                username=_username_from_email(self.commercial_email.get()),
                email=self.commercial_email.get().strip().lower(),
                password=self.commercial_password.get(),
                role="commercial",
            ),
        ]

    def _poll_queue(self) -> None:
        try:
            while True:
                item_type, payload = self.queue.get_nowait()
                if item_type == "log":
                    self.output.insert("end", payload + "\n")
                    self.output.see("end")
                elif item_type == "done":
                    self.running = False
                    self.install_button.configure(state="normal")
        except queue.Empty:
            pass
        self.master.after(150, self._poll_queue)

    def _log(self, message: str) -> None:
        LOGGER.info(_sanitize(message))
        self.queue.put(("log", message))

    def _write_project_status(self, text: str) -> None:
        self.project_status.delete("1.0", "end")
        self.project_status.insert("end", text)


def _username_from_email(email: str) -> str:
    local = email.strip().lower().split("@", 1)[0]
    return "".join(char for char in local if char.isalnum() or char in "._-") or "usuario"


def _sanitize(message: str) -> str:
    lower = message.lower()
    if "password" in lower or "contrasena" in lower or "secret" in lower:
        return "[mensaje sensible omitido]"
    return message


def run() -> None:
    root = Tk()
    InstallWizard(root)
    root.mainloop()
