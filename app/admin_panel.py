from __future__ import annotations

import logging
import os
import threading
import webbrowser
from pathlib import Path
from tkinter import Canvas, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import scrolledtext
from tkinter import ttk

from .backup_task import install_backup_task, query_backup_task, run_backup_check_now
from .config_manager import detect_hostname, detect_local_ipv4, load_config, update_config
from .database_manager import test_connection
from .deployment_config import (
    backup_env_values,
    default_backup_storage_path,
    detect_postgres_tools,
    gmail_env_values,
    prepare_backup_directory,
    test_gmail_smtp,
    validate_gmail_settings,
)
from .diagnostics import DiagnosticReport, export_diagnostics, level_icon, run_diagnostics
from .django_manager import find_static_probe_url, project_python_executable, venv_path as project_venv_path
from .environment_manager import env_database_credentials, is_env_valid, read_env, update_env
from .google_drive_config import (
    configure_google_drive,
    configure_google_drive_later,
    google_drive_status_from_env,
    test_google_drive_connection,
    validate_credentials_file,
)
from .logging_config import logs_dir
from .metadata import APP_AUTHOR, APP_DESCRIPTION, APP_LICENSE, APP_TECHNOLOGIES, APP_YEAR, PRODUCT_NAME
from .project_validator import validate_project
from .server_manager import (
    check_http_health,
    get_server_status,
    is_waitress_process,
    process_exists,
    start_waitress,
    stop_process,
    tail_waitress_log,
    wait_for_port_release,
    wait_for_static_file,
    waitress_log_path,
)
from .startup_task import install_startup_task, query_startup_task, remove_startup_task
from .ui_components import COLORS, Dialogs, apply_app_icon, attach_tooltip, configure_styles, timestamp_text


LOGGER = logging.getLogger(__name__)
NOT_AVAILABLE = "No disponible"


class AdminPanel(ttk.Frame):
    def __init__(self, master: Tk, on_reinstall=None) -> None:
        super().__init__(master)
        self.master = master
        self.on_reinstall = on_reinstall
        self.config = load_config() or {}
        self.current_section = "Inicio"

        self.status_labels: dict[str, ttk.Label] = {}
        self.info_labels: dict[str, ttk.Label] = {}
        self.server_labels: dict[str, ttk.Label] = {}
        self.db_labels: dict[str, ttk.Label] = {}
        self.config_vars: dict[str, StringVar | IntVar] = {}
        self.nav_items: dict[str, ttk.Label] = {}
        self.server_cards: dict[str, dict[str, ttk.Label]] = {}
        self.status_bar_labels: dict[str, ttk.Label] = {}
        self.operation_message = StringVar(value="Listo")
        self.status_general = StringVar(value="Sistema sin actualizar")
        self.status_server = StringVar(value="Servidor sin actualizar")
        self.status_updated = StringVar(value="Ultima actualizacion: No disponible")
        self.operation_progress: ttk.Progressbar | None = None
        self._diagnostics_signature: tuple[object, ...] | None = None
        self.logs_text: scrolledtext.ScrolledText | None = None
        self.diagnostics_tree: ttk.Treeview | None = None
        self.diagnostics_report: DiagnosticReport | None = None
        self.startup_task_status = StringVar(value=NOT_AVAILABLE)
        self.backup_task_status = StringVar(value=NOT_AVAILABLE)
        self.google_drive_status = StringVar(value=NOT_AVAILABLE)
        self.google_drive_account = StringVar(value=NOT_AVAILABLE)
        self.google_drive_credentials_path = StringVar()
        self.smtp_test_recipient = StringVar()

        self._refresh_after_id: str | None = None
        self._logs_after_id: str | None = None
        self._status_refresh_running = False
        self._status_refresh_pending = False
        self._last_statuses: dict[str, bool] | None = None
        self._last_server_status = None
        self._backup_task_status_running = False
        self._startup_task_status_running = False
        self._diagnostics_running = False
        self._closed = False

        self._build()
        self.master.protocol("WM_DELETE_WINDOW", self._confirm_exit)
        self._show_section("Inicio")
        self._schedule_refresh()
        self._schedule_logs()

    @property
    def project_path(self) -> Path:
        return Path(self.config.get("project_path", ""))

    @property
    def venv_path(self) -> Path:
        return project_venv_path(self.project_path)

    @property
    def port(self) -> int:
        try:
            return int(self.config.get("port", 8000))
        except (TypeError, ValueError):
            LOGGER.exception("Puerto invalido en configuracion local")
            return 8000

    @property
    def host(self) -> str:
        return str(self.config.get("host", "0.0.0.0") or "0.0.0.0")

    @property
    def wsgi_module(self) -> str:
        return str(self.config.get("wsgi_module", "config.wsgi:application") or "config.wsgi:application")

    @property
    def pid(self) -> int | None:
        value = self.config.get("pid")
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            LOGGER.exception("PID invalido en configuracion local")
            return None

    def _build(self) -> None:
        self.master.title(PRODUCT_NAME)
        apply_app_icon(self.master)
        configure_styles()
        self.master.geometry("1120x760")
        self.master.minsize(980, 640)
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.configure(style="App.TFrame")
        self.grid(sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.rowconfigure(1, weight=0)

        sidebar = ttk.Frame(self, width=230, style="Sidebar.TFrame", padding=(12, 18))
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        ttk.Label(sidebar, text="Gestion Fiduciaria", style="SidebarTitle.TLabel").pack(anchor="w")
        ttk.Label(sidebar, text="Server Manager", style="SidebarSubtle.TLabel").pack(anchor="w", pady=(2, 22))

        main_sections = ["Inicio", "Servidor", "Base de datos", "Configuración"]
        support_sections = ["Logs", "Diagnóstico", "Acerca de"]
        self._build_nav_group(sidebar, main_sections)
        ttk.Separator(sidebar).pack(fill="x", pady=14)
        self._build_nav_group(sidebar, support_sections)

        ttk.Separator(sidebar).pack(fill="x", pady=14)
        reinstall = ttk.Button(sidebar, text="Reinstalar", command=self._reinstall)
        reinstall.pack(fill="x", pady=(2, 0))
        attach_tooltip(reinstall, "Abre el asistente para configurar nuevamente la instalacion.")

        ttk.Frame(sidebar, style="Sidebar.TFrame").pack(fill="both", expand=True)
        ttk.Label(sidebar, text="Administracion local", style="SidebarSubtle.TLabel").pack(anchor="w", pady=(14, 0))

        self.content = ttk.Frame(self, padding=22, style="Content.TFrame")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

        self._build_status_bar()

    def _build_nav_group(self, parent: ttk.Frame, sections: list[str]) -> None:
        for section in sections:
            item = ttk.Label(parent, text=f"  {section}", style="Nav.TLabel", cursor="hand2")
            item.pack(fill="x", pady=2)
            item.bind("<Button-1>", lambda _event, name=section: self._show_section(name), add="+")
            item.bind("<Return>", lambda _event, name=section: self._show_section(name), add="+")
            item.bind("<Enter>", lambda _event, label=item, name=section: self._nav_hover(label, name, True), add="+")
            item.bind("<Leave>", lambda _event, label=item, name=section: self._nav_hover(label, name, False), add="+")
            item.configure(takefocus=True)
            self.nav_items[section] = item

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self, style="StatusBar.TFrame", padding=(14, 6))
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        values = [
            ("general", self.status_general),
            ("server", self.status_server),
            ("updated", self.status_updated),
            ("operation", self.operation_message),
        ]
        for column, (key, variable) in enumerate(values):
            label = ttk.Label(bar, textvariable=variable, style="StatusBar.TLabel")
            label.grid(row=0, column=column, sticky="w", padx=(0, 18))
            self.status_bar_labels[key] = label
        self.operation_progress = ttk.Progressbar(bar, mode="indeterminate", length=120)
        self.operation_progress.grid(row=0, column=len(values), sticky="e")
        bar.columnconfigure(len(values) - 1, weight=1)

    def _nav_hover(self, widget: ttk.Label, section: str, hovering: bool) -> None:
        if section == self.current_section:
            return
        widget.configure(style="NavHover.TLabel" if hovering else "Nav.TLabel")

    def _update_nav_state(self) -> None:
        for section, widget in self.nav_items.items():
            if self._widget_exists(widget):
                widget.configure(
                    text=f"| {section}" if section == self.current_section else f"  {section}",
                    style="NavActive.TLabel" if section == self.current_section else "Nav.TLabel",
                )

    def _show_section(self, name: str) -> None:
        if self._closed or not self._widget_exists(self.content):
            return
        self.current_section = name
        self._update_nav_state()
        self._clear_dynamic_references()
        for child in self.content.winfo_children():
            child.destroy()

        ttk.Label(self.content, text=name, style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 14))
        container = ttk.Frame(self.content)
        container.grid(row=1, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        builders = {
            "Inicio": self._build_home,
            "Servidor": self._build_server,
            "Base de datos": self._build_database,
            "Configuración": self._build_configuration,
            "Logs": self._build_logs,
            "Diagnóstico": self._build_diagnostics,
            "Acerca de": self._build_about,
        }
        if name == "Configuración":
            scrollable, canvas = self._build_scrollable_container(container)
            builders[name](scrollable)
            self._bind_mousewheel_to_scrollable(scrollable, canvas)
            self._refresh_scrollable_region(scrollable, canvas)
            canvas.after_idle(lambda: self._reset_scrollable_view(scrollable, canvas))
        else:
            builders[name](container)
        self._apply_cached_status()
        self._refresh_status()

    def _clear_dynamic_references(self) -> None:
        self.status_labels.clear()
        self.info_labels.clear()
        self.server_labels.clear()
        self.server_cards.clear()
        self.db_labels.clear()
        self.config_vars.clear()
        self.logs_text = None
        self.diagnostics_tree = None

    def _build_scrollable_container(self, parent: ttk.Frame) -> tuple[ttk.Frame, Canvas]:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        canvas = Canvas(parent, borderwidth=0, highlightthickness=0, background=COLORS["bg"])
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas, style="Content.TFrame")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        inner.columnconfigure(0, weight=1)

        def sync_scroll_region(_event=None) -> None:
            if self._widget_exists(canvas):
                canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_inner_width(event) -> None:
            if self._widget_exists(canvas):
                canvas.itemconfigure(window_id, width=event.width)
                sync_scroll_region()

        inner.bind("<Configure>", sync_scroll_region, add="+")
        canvas.bind("<Configure>", sync_inner_width, add="+")
        return inner, canvas

    def _bind_mousewheel_to_scrollable(self, root_widget, canvas: Canvas) -> None:
        def on_mousewheel(event) -> str:
            if self._widget_exists(canvas):
                steps = -1 * int(event.delta / 120) if event.delta else 0
                if steps:
                    canvas.yview_scroll(steps, "units")
            return "break"

        for widget in self._iter_widget_tree(root_widget):
            try:
                widget.bind("<MouseWheel>", on_mousewheel, add="+")
            except Exception:
                LOGGER.debug("No se pudo enlazar rueda del mouse en widget desplazable", exc_info=True)
        try:
            canvas.bind("<MouseWheel>", on_mousewheel, add="+")
        except Exception:
            LOGGER.debug("No se pudo enlazar rueda del mouse en canvas desplazable", exc_info=True)

    def _iter_widget_tree(self, root_widget):
        yield root_widget
        try:
            children = root_widget.winfo_children()
        except Exception:
            children = []
        for child in children:
            yield from self._iter_widget_tree(child)

    def _refresh_scrollable_region(self, scrollable_frame, canvas: Canvas) -> None:
        try:
            scrollable_frame.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception:
            LOGGER.debug("No se pudo actualizar region desplazable", exc_info=True)

    def _reset_scrollable_view(self, scrollable_frame, canvas: Canvas) -> None:
        if not self._widget_exists(canvas):
            return
        self._refresh_scrollable_region(scrollable_frame, canvas)
        try:
            canvas.yview_moveto(0)
        except Exception:
            LOGGER.debug("No se pudo reiniciar posicion de scroll", exc_info=True)

    def _build_home(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(1, weight=1)

        status_card = self._card(parent, "Estado del sistema")
        status_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 12))
        for index, label in enumerate(
            [
                "Proyecto configurado",
                "Archivo .env válido",
                "Base de datos conectada",
                "Waitress ejecutándose",
                "Aplicación disponible",
            ]
        ):
            widget = ttk.Label(status_card, text=f"○ {label}")
            widget.grid(row=index + 1, column=0, sticky="w", pady=3)
            self.status_labels[label] = widget

        info_card = self._card(parent, "Información del servidor")
        info_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 12))
        for index, label in enumerate(
            [
                "Ruta del proyecto",
                "Host",
                "Puerto",
                "Dirección IP",
                "Hostname",
                "Tiempo desde el último inicio",
            ]
        ):
            ttk.Label(info_card, text=label + ":").grid(row=index + 1, column=0, sticky="nw", pady=3)
            widget = ttk.Label(info_card, text=NOT_AVAILABLE, wraplength=360)
            widget.grid(row=index + 1, column=1, sticky="w", pady=3)
            self.info_labels[label] = widget

        actions = self._card(parent, "Acciones rápidas")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew")
        buttons = [
            ("Abrir aplicación en el navegador", self._open_application),
            ("Reiniciar servidor", self._restart_server),
            ("Detener servidor", self._stop_server),
            ("Iniciar servidor", self._start_server),
            ("Abrir carpeta del proyecto", lambda: self._open_folder(self.project_path)),
            ("Abrir carpeta de logs", lambda: self._open_folder(logs_dir())),
        ]
        for index, (text, command) in enumerate(buttons):
            button = ttk.Button(actions, text=text, command=command)
            button.grid(
                row=1 + index // 3, column=index % 3, sticky="ew", padx=4, pady=4
            )
            attach_tooltip(button, text)
            actions.columnconfigure(index % 3, weight=1)

    def _build_server(self, parent: ttk.Frame) -> None:
        parent.columnconfigure((0, 1, 2), weight=1)
        cards = [
            ("Estado", "Estado actual del servicio Waitress"),
            ("Puerto", "Disponibilidad del puerto configurado"),
            ("PID", "Identificador del proceso activo"),
            ("Tiempo de ejecución", "Tiempo desde el ultimo inicio"),
            ("URL", "Direccion local de la aplicacion"),
            ("Proceso en puerto", "Proceso que atiende el puerto"),
        ]
        for index, (title, detail) in enumerate(cards):
            card = ttk.Frame(parent, padding=14, style="Card.TFrame")
            card.grid(row=index // 3, column=index % 3, sticky="nsew", padx=6, pady=6)
            ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor="w")
            value = ttk.Label(card, text=NOT_AVAILABLE, style="CardValue.TLabel", wraplength=260)
            value.pack(anchor="w", pady=(8, 2))
            detail_label = ttk.Label(card, text=detail, style="CardDetail.TLabel", wraplength=260)
            detail_label.pack(anchor="w")
            self.server_labels[title] = value
            self.server_cards[title] = {"value": value, "detail": detail_label}

        buttons = ttk.Frame(parent, style="Content.TFrame")
        buttons.grid(row=3, column=0, columnspan=3, sticky="w", pady=(18, 0))
        start = ttk.Button(buttons, text="Iniciar", command=self._start_server, style="Primary.TButton")
        stop = ttk.Button(buttons, text="Detener", command=self._stop_server)
        restart = ttk.Button(buttons, text="Reiniciar", command=self._restart_server)
        start.pack(side="left", padx=(0, 8))
        stop.pack(side="left", padx=8)
        restart.pack(side="left", padx=8)
        attach_tooltip(start, "Inicia Waitress si no existe un proceso activo.")
        attach_tooltip(stop, "Detiene solo el proceso Waitress iniciado por Server Manager.")
        attach_tooltip(restart, "Detiene Waitress y lo inicia nuevamente con la configuracion actual.")

    def _build_database(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Base de datos")
        card.grid(row=0, column=0, sticky="nsew")
        for index, label in enumerate(["Host", "Puerto", "Base", "Usuario", "Estado de conexión"]):
            ttk.Label(card, text=label + ":").grid(row=index + 1, column=0, sticky="w", pady=4)
            widget = ttk.Label(card, text=NOT_AVAILABLE)
            widget.grid(row=index + 1, column=1, sticky="w", pady=4)
            self.db_labels[label] = widget
        test_button = ttk.Button(card, text="Probar conexión", command=self._test_database)
        test_button.grid(
            row=7, column=0, sticky="w", pady=(14, 0)
        )
        attach_tooltip(test_button, "Comprueba la conexion con PostgreSQL usando el archivo .env.")

    def _build_configuration(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        card = self._card(parent, "Configuración local")
        card.grid(row=0, column=0, sticky="nsew")
        values = [
            ("Ruta del proyecto", StringVar(value=str(self.project_path))),
            ("Ruta del entorno virtual", StringVar(value=str(self.venv_path))),
            ("Host", StringVar(value=self.host)),
            ("Puerto", IntVar(value=self.port)),
            ("Archivo .env", StringVar(value=str(self.project_path / ".env"))),
        ]
        for index, (label, var) in enumerate(values):
            ttk.Label(card, text=label + ":").grid(row=index + 1, column=0, sticky="w", pady=4)
            state = "normal" if label in {"Host", "Puerto"} else "readonly"
            entry = ttk.Entry(card, textvariable=var, width=80, state=state)
            entry.grid(row=index + 1, column=1, sticky="ew", pady=4)
            self.config_vars[label] = var
        card.columnconfigure(1, weight=1)
        save_button = ttk.Button(card, text="Guardar host y puerto", command=self._save_host_port)
        save_button.grid(
            row=7, column=0, sticky="w", pady=(14, 0)
        )
        attach_tooltip(save_button, "Guarda solo el host y puerto del Server Manager.")

        env_values = self._safe_read_env()
        backup_card = self._card(parent, "Copias de seguridad")
        backup_card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        backup_path = StringVar(value=env_values.get("BACKUP_STORAGE_PATH") or str(default_backup_storage_path()))
        self.config_vars["BACKUP_STORAGE_PATH"] = backup_path
        ttk.Label(backup_card, text="Ruta:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(backup_card, textvariable=backup_path, width=80).grid(row=1, column=1, sticky="ew", pady=4)
        browse = ttk.Button(backup_card, text="Seleccionar", command=self._browse_backup_folder)
        browse.grid(row=1, column=2, sticky="w", padx=(8, 0), pady=4)
        ttk.Label(backup_card, text="pg_dump:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Label(backup_card, text=env_values.get("BACKUP_PG_DUMP_PATH", NOT_AVAILABLE), wraplength=620).grid(
            row=2, column=1, columnspan=2, sticky="w", pady=4
        )
        ttk.Label(backup_card, text="pg_restore:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Label(backup_card, text=env_values.get("BACKUP_PG_RESTORE_PATH", NOT_AVAILABLE), wraplength=620).grid(
            row=3, column=1, columnspan=2, sticky="w", pady=4
        )
        save_backups = ttk.Button(backup_card, text="Guardar backups", command=self._save_backup_configuration)
        save_backups.grid(row=4, column=0, sticky="w", pady=(12, 0))
        attach_tooltip(browse, "Seleccione la carpeta fisica para almacenar los ZIP de backup.")
        attach_tooltip(save_backups, "Valida carpeta, detecta PostgreSQL y actualiza .env.")
        backup_card.columnconfigure(1, weight=1)

        task_card = self._card(parent, "Automatizacion de backups")
        task_card.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(task_card, text="Estado:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(task_card, textvariable=self.backup_task_status, wraplength=640).grid(
            row=1, column=1, sticky="w", pady=4
        )
        task_buttons = ttk.Frame(task_card)
        task_buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        refresh_backup_task = ttk.Button(task_buttons, text="Actualizar estado", command=self._refresh_backup_task_status)
        enable_backup_task = ttk.Button(task_buttons, text="Activar/Reparar", command=self._enable_backup_task)
        run_backup_check = ttk.Button(task_buttons, text="Ejecutar comprobacion de backup", command=self._run_backup_check)
        refresh_backup_task.pack(side="left", padx=(0, 8))
        enable_backup_task.pack(side="left", padx=8)
        run_backup_check.pack(side="left", padx=8)
        attach_tooltip(refresh_backup_task, "Consulta la tarea programada de comprobacion de backups.")
        attach_tooltip(enable_backup_task, "Crea o repara la tarea programada de backups.")
        attach_tooltip(run_backup_check, "Ejecuta run_daily_backup_check respetando las reglas normales de PagosFiducia.")
        task_card.columnconfigure(1, weight=1)
        self._refresh_backup_task_status_async()

        drive_card = self._card(parent, "Google Drive")
        drive_card.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        drive_status = google_drive_status_from_env(self.project_path)
        self.google_drive_status.set(drive_status.message)
        self.google_drive_account.set(drive_status.account_email or "No disponible")
        ttk.Label(drive_card, text="Cuenta Google autorizada:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(drive_card, textvariable=self.google_drive_account).grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(drive_card, text="Credenciales OAuth:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Label(drive_card, text="Configuradas" if drive_status.credentials_present else "No configuradas").grid(
            row=2, column=1, sticky="w", pady=4
        )
        ttk.Label(drive_card, text="Autorizacion:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Label(drive_card, text="Activa" if drive_status.configured else "Pendiente").grid(
            row=3, column=1, sticky="w", pady=4
        )
        ttk.Label(drive_card, text="Carpeta:").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Label(drive_card, text=drive_status.folder_name).grid(row=4, column=1, sticky="w", pady=4)
        ttk.Label(drive_card, text="Conexion:").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Label(drive_card, textvariable=self.google_drive_status, wraplength=620).grid(
            row=5, column=1, sticky="w", pady=4
        )
        ttk.Label(drive_card, text="credentials.json:").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Entry(drive_card, textvariable=self.google_drive_credentials_path, width=72).grid(
            row=6, column=1, sticky="ew", pady=4
        )
        browse_drive = ttk.Button(drive_card, text="Seleccionar", command=self._browse_google_drive_credentials)
        browse_drive.grid(row=6, column=2, padx=(8, 0), pady=4)
        drive_buttons = ttk.Frame(drive_card)
        drive_buttons.grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))
        authorize_drive = ttk.Button(drive_buttons, text="Autorizar nuevamente", command=self._authorize_google_drive)
        test_drive = ttk.Button(drive_buttons, text="Probar Google Drive", command=self._test_google_drive)
        configure_later = ttk.Button(drive_buttons, text="Configurar despues", command=self._configure_google_drive_later)
        authorize_drive.pack(side="left", padx=(0, 8))
        test_drive.pack(side="left", padx=8)
        configure_later.pack(side="left", padx=8)
        attach_tooltip(browse_drive, "Seleccione credentials.json OAuth de tipo Desktop App.")
        attach_tooltip(authorize_drive, "Abre OAuth oficial para autorizar la cuenta Google seleccionada.")
        attach_tooltip(test_drive, "Valida token, Drive API y carpeta remota sin subir backups.")
        attach_tooltip(configure_later, "Desactiva Drive y mantiene backups locales.")
        drive_card.columnconfigure(1, weight=1)

        mail_card = self._card(parent, "Correo Gmail")
        mail_card.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        gmail_email = StringVar(value=env_values.get("EMAIL_HOST_USER", ""))
        gmail_password = StringVar()
        self.config_vars["EMAIL_HOST_USER"] = gmail_email
        self.config_vars["EMAIL_HOST_PASSWORD"] = gmail_password
        ttk.Label(mail_card, text="Correo remitente:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(mail_card, textvariable=gmail_email, width=40).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Label(mail_card, text="Configurada:").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Label(mail_card, text="Si" if env_values.get("EMAIL_HOST_PASSWORD") else "No").grid(
            row=2, column=1, sticky="w", pady=4
        )
        ttk.Label(mail_card, text="Contrasena de aplicacion:").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(mail_card, textvariable=gmail_password, show="*", width=40).grid(
            row=3, column=1, sticky="ew", pady=4
        )
        ttk.Label(mail_card, text="Destinatario prueba:").grid(row=4, column=0, sticky="w", pady=4)
        ttk.Entry(mail_card, textvariable=self.smtp_test_recipient, width=40).grid(
            row=4, column=1, sticky="ew", pady=4
        )
        mail_buttons = ttk.Frame(mail_card)
        mail_buttons.grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))
        save_mail = ttk.Button(mail_buttons, text="Guardar correo", command=self._save_gmail_configuration)
        test_mail = ttk.Button(mail_buttons, text="Probar correo", command=self._test_gmail_configuration)
        save_mail.pack(side="left", padx=(0, 8))
        test_mail.pack(side="left", padx=8)
        attach_tooltip(save_mail, "Guarda la configuracion SMTP de Gmail en .env sin mostrar la credencial.")
        attach_tooltip(test_mail, "Prueba autenticacion SMTP usando la contrasena ingresada o configurada.")
        mail_card.columnconfigure(1, weight=1)

        startup_card = self._card(parent, "Inicio automatico con Windows")
        startup_card.grid(row=5, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(startup_card, text="Estado:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(startup_card, textvariable=self.startup_task_status).grid(row=1, column=1, sticky="w", pady=4)
        startup_buttons = ttk.Frame(startup_card)
        startup_buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))
        refresh = ttk.Button(startup_buttons, text="Actualizar estado", command=self._refresh_startup_task_status)
        enable = ttk.Button(startup_buttons, text="Activar", command=self._enable_startup_task)
        disable = ttk.Button(startup_buttons, text="Desactivar", command=self._disable_startup_task)
        refresh.pack(side="left", padx=(0, 8))
        enable.pack(side="left", padx=8)
        disable.pack(side="left", padx=8)
        attach_tooltip(refresh, "Consulta la tarea programada de inicio automatico.")
        attach_tooltip(enable, "Crea la tarea programada de Windows para iniciar Waitress al arrancar.")
        attach_tooltip(disable, "Elimina la tarea programada de inicio automatico.")
        startup_card.columnconfigure(1, weight=1)
        self._refresh_startup_task_status_async()

    def _build_logs(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.logs_text = scrolledtext.ScrolledText(parent, wrap="word")
        self.logs_text.grid(row=0, column=0, sticky="nsew")
        logs_button = ttk.Button(parent, text="Abrir carpeta de logs", command=lambda: self._open_folder(logs_dir()))
        logs_button.grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        attach_tooltip(logs_button, "Abre la carpeta donde se guardan los registros del Server Manager.")
        self._refresh_logs()

    def _build_diagnostics(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        columns = ("status", "item", "message")
        self.diagnostics_tree = ttk.Treeview(parent, columns=columns, show="headings")
        self.diagnostics_tree.heading("status", text="Estado")
        self.diagnostics_tree.heading("item", text="Elemento")
        self.diagnostics_tree.heading("message", text="Resultado")
        self.diagnostics_tree.column("status", width=90, anchor="center")
        self.diagnostics_tree.column("item", width=180, anchor="w")
        self.diagnostics_tree.column("message", width=650, anchor="w")
        self.diagnostics_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.diagnostics_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.diagnostics_tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(parent)
        buttons.grid(row=1, column=0, sticky="w", pady=(12, 0))
        run_button = ttk.Button(buttons, text="Ejecutar diagnóstico", command=self._run_diagnostics_clicked)
        export_button = ttk.Button(buttons, text="Exportar diagnóstico", command=self._export_diagnostics_clicked)
        run_button.pack(side="left", padx=(0, 8))
        export_button.pack(side="left", padx=8)
        attach_tooltip(run_button, "Revisa la instalacion, el servidor, PostgreSQL y archivos locales.")
        attach_tooltip(export_button, "Guarda el resultado del diagnostico en un archivo de texto.")
        self._run_diagnostics_clicked()

    def _build_about(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        card = self._card(parent, PRODUCT_NAME)
        card.grid(row=0, column=0, sticky="ew")
        rows = [
            ("Descripcion", APP_DESCRIPTION),
            ("Autor", APP_AUTHOR),
            ("Anio", APP_YEAR),
            ("Tecnologias", ", ".join(APP_TECHNOLOGIES)),
            ("Licencia", APP_LICENSE),
        ]
        for index, (label, value) in enumerate(rows, start=1):
            ttk.Label(card, text=f"{label}:", style="CardTitle.TLabel").grid(row=index, column=0, sticky="nw", pady=5)
            ttk.Label(card, text=value, style="CardDetail.TLabel", wraplength=760).grid(
                row=index, column=1, sticky="w", pady=5
            )
        card.columnconfigure(1, weight=1)

        icon_card = self._card(parent, "Icono de aplicacion")
        icon_card.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(
            icon_card,
            text="La ruta assets/app.ico queda preparada para ventana principal y futura compilacion con PyInstaller.",
            style="CardDetail.TLabel",
            wraplength=760,
        ).grid(row=1, column=0, sticky="w")

    def _card(self, parent: ttk.Frame, title: str) -> ttk.Frame:
        frame = ttk.Frame(parent, padding=14, style="Card.TFrame")
        ttk.Label(frame, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        return frame

    def _schedule_refresh(self) -> None:
        if self._closed or self._refresh_after_id is not None or not self._widget_exists(self.master):
            return
        self._refresh_after_id = self.master.after(5000, self._refresh_loop)

    def _refresh_loop(self) -> None:
        self._refresh_after_id = None
        if self._closed or not self._widget_exists(self):
            return
        try:
            self._refresh_status()
        except Exception:
            LOGGER.exception("Fallo inesperado durante el refresco automatico del panel")
        self._schedule_refresh()

    def _refresh_status(self) -> None:
        if self._closed or not self._widget_exists(self):
            return
        if self._status_refresh_running:
            self._status_refresh_pending = True
            return
        self._status_refresh_running = True
        config_snapshot = load_config() or self.config

        def worker() -> None:
            try:
                server_status = get_server_status(config_snapshot)
                config = self._reconcile_config_with_status(config_snapshot, server_status)
                statuses = self._status_snapshot(server_status)
                self._after_ui(lambda: self._apply_status_result(config, server_status, statuses, None))
            except Exception as exc:
                LOGGER.exception("No se pudo refrescar estado del panel")
                self._after_ui(lambda exc=exc: self._apply_status_result(config_snapshot, None, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_cached_status(self) -> None:
        if self._last_statuses is not None and self._last_server_status is not None:
            self._apply_status_to_current_section(self._last_statuses, self._last_server_status)

    def _apply_status_result(self, config, server_status, statuses, error) -> None:
        self._status_refresh_running = False
        if self._closed or not self._widget_exists(self):
            return
        if error is None and server_status is not None and statuses is not None:
            self.config = config
            self._last_server_status = server_status
            self._last_statuses = statuses
            self._apply_status_to_current_section(statuses, server_status)
        if self._status_refresh_pending:
            self._status_refresh_pending = False
            self._refresh_status()

    def _apply_status_to_current_section(self, statuses: dict[str, bool], server_status) -> None:
        if self.current_section == "Inicio":
            for label, ok in statuses.items():
                self._set_indicator_label(self.status_labels.get(label), label, "ok" if ok else "warning")
            self._set_label(self.info_labels.get("Ruta del proyecto"), self._value_or_na(str(self.project_path)))
            self._set_label(self.info_labels.get("Host"), self._value_or_na(self.host))
            self._set_label(self.info_labels.get("Puerto"), self._value_or_na(str(self.port)))
            self._set_label(self.info_labels.get("Dirección IP"), self._value_or_na(self.config.get("ipv4") or detect_local_ipv4()))
            self._set_label(self.info_labels.get("Hostname"), self._value_or_na(self.config.get("hostname") or detect_hostname()))
            self._set_label(self.info_labels.get("Tiempo desde el último inicio"), self._value_or_na(server_status.uptime))

        if self.current_section == "Servidor":
            state_detail = (
                server_status.http_message
                if server_status.running
                else "Servicio sin proceso activo"
            )
            self._set_server_card("Estado", server_status.state, state_detail)
            self._set_server_card("Puerto", server_status.port_message, f"Host configurado: {server_status.host}")
            self._set_server_card("PID", str(server_status.pid) if server_status.pid else NOT_AVAILABLE, server_status.process_message)
            self._set_server_card("Tiempo de ejecución", self._value_or_na(server_status.uptime), "Se actualiza automaticamente")
            self._set_server_card("URL", f"http://127.0.0.1:{server_status.port}/", "Direccion local para abrir la aplicacion")
            self._set_server_card("Proceso en puerto", server_status.process_message, "Debe coincidir con Gestion Fiduciaria")

        if self.current_section == "Base de datos":
            env = self._safe_read_env()
            self._set_label(self.db_labels.get("Host"), self._value_or_na(env.get("DB_HOST")))
            self._set_label(self.db_labels.get("Puerto"), self._value_or_na(env.get("DB_PORT")))
            self._set_label(self.db_labels.get("Base"), self._value_or_na(env.get("DB_NAME")))
            self._set_label(self.db_labels.get("Usuario"), self._value_or_na(env.get("DB_USER")))
            self._set_label(
                self.db_labels.get("Estado de conexión"),
                "Conectada" if statuses["Base de datos conectada"] else "Sin conexión",
            )

        self._update_status_bar(statuses, server_status)

    def _status_snapshot(self, server_status=None) -> dict[str, bool]:
        project_ok = False
        env_ok = False
        db_ok = False
        waitress_ok = False
        app_ok = False
        try:
            project_ok = validate_project(self.project_path).valid
        except Exception:
            LOGGER.exception("No se pudo validar el proyecto configurado")
        try:
            env_ok = is_env_valid(self.project_path)
        except Exception:
            LOGGER.exception("No se pudo validar el archivo .env")
        try:
            db_ok = self._database_ok(silent=True)
        except Exception:
            LOGGER.exception("No se pudo comprobar la base de datos")
        try:
            server_status = server_status or get_server_status(self.config)
            waitress_ok = server_status.running
            app_ok = server_status.app_ok if waitress_ok else False
        except Exception:
            LOGGER.exception("No se pudo comprobar el estado de Waitress")
        return {
            "Proyecto configurado": project_ok,
            "Archivo .env válido": env_ok,
            "Base de datos conectada": db_ok,
            "Waitress ejecutándose": waitress_ok,
            "Aplicación disponible": app_ok,
        }

    def _database_ok(self, silent: bool = False) -> bool:
        try:
            result = test_connection(env_database_credentials(self.project_path))
            if not result.ok:
                if not silent:
                    self._set_label(self.db_labels.get("Estado de conexión"), f"{result.message} ({result.elapsed_ms} ms)")
                    Dialogs.error("Base de datos", result.message)
                return False
            if not silent:
                self._set_label(self.db_labels.get("Estado de conexión"), f"Conectada ({result.elapsed_ms} ms)")
            return True
        except Exception as exc:
            if silent:
                LOGGER.debug("Base de datos no disponible durante refresco: %s", exc)
            else:
                LOGGER.exception("Fallo la prueba de conexion a base de datos")
                self._set_label(self.db_labels.get("Estado de conexión"), "Sin conexión")
                Dialogs.error("Base de datos", str(exc))
            return False

    def _test_database(self) -> None:
        if self._database_ok(silent=False):
            Dialogs.success("Base de datos", "Conexión comprobada correctamente.")

    def _start_server(self) -> None:
        self._run_background(self._start_server_worker, "Servidor")

    def _start_server_worker(self) -> None:
        server_status = get_server_status(self.config)
        if server_status.running:
            self.config = update_config({"pid": server_status.pid, "ipv4": detect_local_ipv4(), "hostname": detect_hostname()})
            LOGGER.info("Waitress ya estaba ejecutandose; se reutiliza PID %s", server_status.pid)
            self._info("Servidor", f"Waitress ya está ejecutándose con PID {server_status.pid}.")
            return
        if server_status.conflict_message:
            raise RuntimeError(server_status.conflict_message)
        if self.pid and process_exists(self.pid):
            raise RuntimeError("El PID almacenado existe, pero no corresponde al Waitress de Gestion Fiduciaria.")
        self._validate_server_start_preconditions()
        static_url = find_static_probe_url(self.project_path, self.venv_path)
        process = start_waitress(self.project_path, self.venv_path, self.port, host=self.host, wsgi_module=self.wsgi_module)
        if self._process_exited(process):
            self.config = update_config({"pid": None})
            raise RuntimeError(self._startup_failure_message("Waitress finalizo inmediatamente al arrancar."))
        health = check_http_health(self.port)
        if self._process_exited(process):
            self.config = update_config({"pid": None})
            raise RuntimeError(self._startup_failure_message("Waitress finalizo antes de responder correctamente."))
        if not health.responding:
            self.config = update_config({"pid": None})
            raise RuntimeError(self._startup_failure_message("Waitress inicio, pero la aplicacion no respondio."))
        if not health.ok:
            server_status = get_server_status(self.config)
            pid = server_status.pid or process.pid
            self.config = update_config({"pid": pid, "ipv4": detect_local_ipv4(), "hostname": detect_hostname()})
            raise RuntimeError(self._startup_failure_message(f"Waitress responde, pero Django devolvio {health.message}"))
        if not wait_for_static_file(self.port, static_url):
            raise RuntimeError("La aplicación respondió, pero no sirvió archivos estáticos.")
        server_status = get_server_status(self.config)
        pid = server_status.pid or process.pid
        self.config = update_config({"pid": pid, "ipv4": detect_local_ipv4(), "hostname": detect_hostname()})
        LOGGER.info("Waitress iniciado con PID %s en puerto %s", pid, self.port)
        self._info("Servidor", "Servidor iniciado correctamente.")

    def _stop_server(self) -> None:
        self._run_background(self._stop_server_worker, "Servidor")

    def _stop_server_worker(self) -> None:
        server_status = get_server_status(self.config)
        target_pid = server_status.pid or self.pid
        if not process_exists(target_pid):
            self.config = update_config({"pid": None})
            self._info("Servidor", "No hay un proceso Waitress activo registrado.")
            return
        if not self._is_managed_waitress(target_pid):
            raise RuntimeError("El PID almacenado existe, pero no corresponde al Waitress de Gestion Fiduciaria.")
        if not stop_process(target_pid):
            raise RuntimeError("No se pudo detener el proceso Waitress dentro del tiempo esperado.")
        if not wait_for_port_release(self.port):
            raise RuntimeError("Waitress se detuvo, pero el puerto no quedo libre dentro del tiempo esperado.")
        self.config = update_config({"pid": None})
        LOGGER.info("Waitress detenido")
        self._info("Servidor", "Servidor detenido correctamente.")

    def _restart_server(self) -> None:
        self._run_background(self._restart_server_worker, "Servidor")

    def _restart_server_worker(self) -> None:
        server_status = get_server_status(self.config)
        target_pid = server_status.pid or self.pid
        if process_exists(target_pid):
            if not self._is_managed_waitress(target_pid):
                raise RuntimeError("El PID almacenado existe, pero no corresponde al Waitress de Gestion Fiduciaria.")
            if not stop_process(target_pid):
                raise RuntimeError("No se pudo detener el proceso Waitress para reiniciar.")
            if not wait_for_port_release(self.port):
                raise RuntimeError("El puerto no quedo libre para reiniciar Waitress.")
            self.config = update_config({"pid": None})
        LOGGER.info("Reiniciando Waitress")
        self._start_server_worker()

    def _validate_server_start_preconditions(self) -> None:
        inspection = validate_project(self.project_path)
        if not inspection.valid:
            raise RuntimeError("Proyecto de Gestion Fiduciaria invalido: " + "; ".join(inspection.errors))
        python = project_python_executable(self.project_path)
        if not python.exists():
            raise RuntimeError(f"No existe python.exe del entorno virtual: {python}")
        if not (self.project_path / ".env").exists():
            raise RuntimeError("No existe el archivo .env de Gestion Fiduciaria.")
        if not is_env_valid(self.project_path):
            raise RuntimeError("El archivo .env no contiene la configuracion minima requerida.")
        if not self.wsgi_module or ":" not in self.wsgi_module:
            raise RuntimeError("El modulo WSGI configurado no es valido.")
        if not self._database_ok(silent=True):
            raise RuntimeError("PostgreSQL no esta disponible con la configuracion actual.")

    def _process_exited(self, process) -> bool:
        poll = getattr(process, "poll", None)
        if not callable(poll):
            return False
        try:
            return poll() is not None
        except Exception:
            LOGGER.debug("No se pudo consultar poll() de Waitress", exc_info=True)
            return False

    def _startup_failure_message(self, message: str) -> str:
        log_tail = tail_waitress_log()
        if log_tail:
            return f"{message}\n\nUltimas lineas de log Waitress:\n{log_tail}"
        return f"{message}\n\nLog Waitress: {waitress_log_path()}"

    def _save_host_port(self) -> None:
        try:
            host = str(self.config_vars["Host"].get()).strip()
            port = int(self.config_vars["Puerto"].get())
            if not host:
                raise ValueError("El host no puede estar vacío.")
            if port < 1024 or port > 65535:
                raise ValueError("El puerto debe estar entre 1024 y 65535.")
            self.config = update_config({"host": host, "port": port})
            LOGGER.info("Configuracion actualizada: host=%s port=%s", host, port)
            Dialogs.success("Configuración", "Host y puerto guardados. Reinicie el servidor para aplicar cambios.")
        except Exception as exc:
            Dialogs.error("Configuración", str(exc))

    def _browse_backup_folder(self) -> None:
        selected = filedialog.askdirectory(title="Seleccione la carpeta donde se almacenaran las copias de seguridad")
        if selected and "BACKUP_STORAGE_PATH" in self.config_vars:
            self.config_vars["BACKUP_STORAGE_PATH"].set(selected)

    def _save_backup_configuration(self) -> None:
        try:
            backup_path = prepare_backup_directory(str(self.config_vars["BACKUP_STORAGE_PATH"].get()))
            tools = detect_postgres_tools(self.project_path)
            update_env(self.project_path, backup_env_values(backup_path, tools), backup_existing=True)
            LOGGER.info("Configuracion de backups actualizada. storage=%s pg_dump=%s pg_restore=%s", backup_path, tools.pg_dump, tools.pg_restore)
            Dialogs.success("Copias de seguridad", "Configuracion de backups guardada correctamente.")
            self._show_section("Configuración")
        except Exception as exc:
            LOGGER.exception("No se pudo guardar configuracion de backups")
            Dialogs.error("Copias de seguridad", str(exc))

    def _refresh_backup_task_status(self) -> None:
        self._refresh_backup_task_status_async(show_errors=True)

    def _refresh_backup_task_status_async(self, show_errors: bool = False) -> None:
        if self._backup_task_status_running:
            return
        self._backup_task_status_running = True
        self.backup_task_status.set("Consultando automatizacion de backups...")

        def worker() -> None:
            try:
                status = query_backup_task()
                self._after_ui(lambda: self._apply_backup_task_status(status.message))
            except Exception as exc:
                LOGGER.exception("No se pudo consultar la tarea de backups")
                self._after_ui(lambda: self._apply_backup_task_status("No se pudo consultar la automatizacion de backups."))
                if show_errors:
                    self._after_ui(lambda exc=exc: Dialogs.error("Automatizacion de backups", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_backup_task_status(self, message: str) -> None:
        self._backup_task_status_running = False
        if self._widget_exists(self):
            self.backup_task_status.set(message)

    def _refresh_backup_task_status_sync(self) -> None:
        try:
            status = query_backup_task()
            self.backup_task_status.set(status.message)
        except Exception:
            LOGGER.exception("No se pudo consultar la tarea de backups")
            self.backup_task_status.set("No se pudo consultar la automatizacion de backups.")

    def _enable_backup_task(self) -> None:
        try:
            status = install_backup_task(self.project_path, self.venv_path)
            self.backup_task_status.set(status.message)
            Dialogs.success("Automatizacion de backups", "Automatizacion de backups activada.")
        except Exception as exc:
            LOGGER.exception("No se pudo activar la automatizacion de backups")
            self.backup_task_status.set("No se pudo activar la automatizacion de backups.")
            Dialogs.error("Automatizacion de backups", str(exc))

    def _run_backup_check(self) -> None:
        self._run_background(self._run_backup_check_worker, "Backups")

    def _run_backup_check_worker(self) -> None:
        output = run_backup_check_now(self.project_path, self.venv_path)
        self._info("Backups", output)

    def _browse_google_drive_credentials(self) -> None:
        selected = filedialog.askopenfilename(
            title="Seleccionar credentials.json de Google Drive",
            filetypes=[("JSON", "*.json"), ("Todos los archivos", "*.*")],
        )
        if not selected:
            return
        try:
            validate_credentials_file(selected)
            self.google_drive_credentials_path.set(selected)
            self.google_drive_status.set("Credenciales OAuth validas. Autorice Google Drive para completar.")
        except Exception as exc:
            Dialogs.error("Google Drive", str(exc))

    def _authorize_google_drive(self) -> None:
        credentials_path = self.google_drive_credentials_path.get().strip() or None
        self._run_background(lambda: self._authorize_google_drive_worker(credentials_path), "Google Drive")

    def _authorize_google_drive_worker(self, credentials_path: str | None) -> None:
        if credentials_path:
            validate_credentials_file(credentials_path)
        result = configure_google_drive(self.project_path, credentials_path)
        self._after_ui(lambda: self.google_drive_status.set("Google Drive configurado."))
        if result.account_email:
            self._after_ui(lambda: self.google_drive_account.set(result.account_email or "No disponible"))
        email_text = f"\nCuenta detectada: {result.account_email}" if result.account_email else ""
        self._info(
            "Google Drive",
            "Google Drive autorizado correctamente.\n"
            f"Carpeta: {result.folder_name}{email_text}",
        )

    def _test_google_drive(self) -> None:
        self._run_background(self._test_google_drive_worker, "Google Drive")

    def _test_google_drive_worker(self) -> None:
        status = test_google_drive_connection(self.project_path)
        self._after_ui(lambda: self.google_drive_status.set(status.message))
        if status.account_email:
            self._after_ui(lambda: self.google_drive_account.set(status.account_email or "No disponible"))
        email_text = f"\nCuenta detectada: {status.account_email}" if status.account_email else ""
        self._info("Google Drive", f"{status.message}\nCarpeta: {status.folder_name}{email_text}")

    def _configure_google_drive_later(self) -> None:
        try:
            configure_google_drive_later(self.project_path)
            self.google_drive_status.set("Google Drive pendiente de configuracion; backups locales activos.")
            Dialogs.success("Google Drive", "Google Drive quedo desactivado. Los backups locales siguen activos.")
            self._show_section("Configuración")
        except Exception as exc:
            LOGGER.exception("No se pudo desactivar Google Drive")
            Dialogs.error("Google Drive", str(exc))

    def _save_gmail_configuration(self) -> None:
        try:
            env_values = self._safe_read_env()
            email = str(self.config_vars["EMAIL_HOST_USER"].get()).strip()
            password = str(self.config_vars["EMAIL_HOST_PASSWORD"].get())
            if not password:
                password = env_values.get("EMAIL_HOST_PASSWORD", "")
            updates = gmail_env_values(email, password)
            validate_gmail_settings({**env_values, **updates})
            update_env(self.project_path, updates, backup_existing=True)
            LOGGER.info("Configuracion SMTP Gmail actualizada.")
            Dialogs.success("Correo Gmail", "Configuracion de correo guardada correctamente.")
            self._show_section("Configuración")
        except Exception as exc:
            LOGGER.exception("No se pudo guardar configuracion SMTP Gmail")
            Dialogs.error("Correo Gmail", str(exc))

    def _test_gmail_configuration(self) -> None:
        env_values = self._safe_read_env()
        typed_password = str(self.config_vars["EMAIL_HOST_PASSWORD"].get())
        email = str(self.config_vars["EMAIL_HOST_USER"].get()).strip() or env_values.get("EMAIL_HOST_USER", "")
        recipient = self.smtp_test_recipient.get().strip() or email
        values = {**env_values, **gmail_env_values(email, typed_password or env_values.get("EMAIL_HOST_PASSWORD"))}
        self._run_background(lambda: self._test_gmail_configuration_worker(values, recipient), "Correo Gmail")

    def _test_gmail_configuration_worker(self, values: dict[str, str], recipient: str) -> None:
        test_gmail_smtp(values, recipient=recipient)
        self._info("Correo Gmail", "Correo enviado correctamente.")

    def _refresh_startup_task_status(self) -> None:
        self._refresh_startup_task_status_async(show_errors=True)

    def _refresh_startup_task_status_async(self, show_errors: bool = False) -> None:
        if self._startup_task_status_running:
            return
        self._startup_task_status_running = True
        self.startup_task_status.set("Consultando inicio automatico...")

        def worker() -> None:
            try:
                status = query_startup_task()
                self._after_ui(lambda: self._apply_startup_task_status(status.message))
            except Exception as exc:
                LOGGER.exception("No se pudo consultar la tarea de inicio automatico")
                self._after_ui(lambda: self._apply_startup_task_status("No se pudo consultar el inicio automatico."))
                if show_errors:
                    self._after_ui(lambda exc=exc: Dialogs.error("Inicio automatico", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_startup_task_status(self, message: str) -> None:
        self._startup_task_status_running = False
        if self._widget_exists(self):
            self.startup_task_status.set(message)

    def _refresh_startup_task_status_sync(self) -> None:
        try:
            status = query_startup_task()
            self.startup_task_status.set(status.message)
        except Exception as exc:
            LOGGER.exception("No se pudo consultar la tarea de inicio automatico")
            self.startup_task_status.set("No se pudo consultar el inicio automatico.")
            Dialogs.error("Inicio automatico", str(exc))

    def _enable_startup_task(self) -> None:
        try:
            status = install_startup_task(
                self.project_path,
                self.venv_path,
                host=self.host,
                port=self.port,
                wsgi_module=self.wsgi_module,
            )
            self.startup_task_status.set(status.message)
            Dialogs.success("Inicio automatico", "Inicio automatico con Windows activado.")
        except Exception as exc:
            LOGGER.exception("No se pudo activar el inicio automatico")
            self.startup_task_status.set("No se pudo activar el inicio automatico.")
            Dialogs.error("Inicio automatico", str(exc))

    def _disable_startup_task(self) -> None:
        try:
            status = remove_startup_task()
            self.startup_task_status.set(status.message)
            Dialogs.success("Inicio automatico", "Inicio automatico con Windows desactivado.")
        except Exception as exc:
            LOGGER.exception("No se pudo desactivar el inicio automatico")
            self.startup_task_status.set("No se pudo desactivar el inicio automatico.")
            Dialogs.error("Inicio automatico", str(exc))

    def _open_application(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def _open_folder(self, path: Path) -> None:
        if path.name == "logs":
            path.mkdir(parents=True, exist_ok=True)
        if path.exists():
            os.startfile(path)
        else:
            Dialogs.error("Carpeta", f"No existe: {path}")

    def _refresh_logs(self) -> None:
        if not self._widget_exists(self.logs_text):
            return
        log_path = logs_dir() / "server_manager.log"
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        except Exception:
            LOGGER.exception("No se pudo leer el archivo de logs")
            content = NOT_AVAILABLE
        self.logs_text.configure(state="normal")
        self.logs_text.delete("1.0", "end")
        self.logs_text.insert("end", content)
        self.logs_text.see("end")

    def _run_diagnostics_clicked(self, force: bool = True, signature: tuple[object, ...] | None = None) -> None:
        if self._diagnostics_running:
            return
        signature = signature or (
            self._diagnostics_signature_for(self._last_server_status)
            if self._last_server_status is not None
            else ("pending", self.config.get("project_path"), str(self.venv_path))
        )
        if not force and signature == self._diagnostics_signature:
            return
        self._set_busy(True, "Ejecutando diagnostico...")
        self._diagnostics_running = True
        config_snapshot = load_config()

        def worker() -> None:
            try:
                report = run_diagnostics(config_snapshot)
                self._after_ui(lambda: self._apply_diagnostics_report(report, signature, None))
            except Exception as exc:
                LOGGER.exception("No se pudo ejecutar diagnostico")
                self._after_ui(lambda exc=exc: self._apply_diagnostics_report(None, signature, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_diagnostics_report(self, report: DiagnosticReport | None, signature: tuple[object, ...], error) -> None:
        self._diagnostics_running = False
        if not self._widget_exists(self.diagnostics_tree):
            self._set_busy(False, "Listo")
            return
        if error is not None or report is None:
            self._set_busy(False, "No se pudo ejecutar diagnostico")
            Dialogs.error("Diagnostico", "No se pudo ejecutar el diagnostico.")
            return
        self.diagnostics_report = report
        self._diagnostics_signature = signature
        self.diagnostics_tree.delete(*self.diagnostics_tree.get_children())
        for item in self.diagnostics_report.items:
            self.diagnostics_tree.insert("", "end", values=(level_icon(item.level), item.name, item.message))
        LOGGER.info("Diagnostico ejecutado: %s", self.diagnostics_report.overall_message)
        self._set_busy(False, "Diagnostico actualizado")

    def _diagnostics_signature_for(self, server_status) -> tuple[object, ...]:
        return (
            server_status.state,
            server_status.pid,
            server_status.port,
            server_status.listening,
            self.config.get("project_path"),
            str(self.venv_path),
            self.config.get("installation_status"),
        )

    def _export_diagnostics_clicked(self) -> None:
        try:
            self._set_busy(True, "Exportando diagnostico...")
            report = self.diagnostics_report or run_diagnostics(load_config())
            path = export_diagnostics(report)
            Dialogs.success("Diagnóstico", f"Diagnóstico exportado en:\n{path}")
        except Exception as exc:
            LOGGER.exception("No se pudo exportar diagnostico")
            Dialogs.error("Diagnóstico", str(exc))
        finally:
            self._set_busy(False, "Listo")

    def _schedule_logs(self) -> None:
        if self._closed or self._logs_after_id is not None or not self._widget_exists(self.master):
            return
        self._logs_after_id = self.master.after(3000, self._logs_loop)

    def _logs_loop(self) -> None:
        self._logs_after_id = None
        if self._closed or not self._widget_exists(self):
            return
        if self.current_section == "Logs":
            try:
                self._refresh_logs()
            except Exception:
                LOGGER.exception("Fallo inesperado durante el refresco de logs")
        self._schedule_logs()

    def _run_background(self, target, title: str) -> None:
        def runner() -> None:
            try:
                self._after_ui(lambda: self._set_busy(True, f"{title}: operacion en curso..."))
                target()
            except Exception as exc:
                LOGGER.exception("Fallo en accion del panel")
                self._after_ui(lambda: Dialogs.error(title, str(exc)))
            finally:
                self._after_ui(lambda: self._set_busy(False, "Listo"))
                self._after_ui(self._refresh_status)

        threading.Thread(target=runner, daemon=True).start()

    def _info(self, title: str, message: str) -> None:
        self._after_ui(lambda: Dialogs.success(title, message))

    def _reinstall(self) -> None:
        if not Dialogs.confirm("Reinstalar", "¿Desea abrir el asistente de instalación?"):
            return
        if self.on_reinstall:
            self._dispose()
            self.on_reinstall()

    def _confirm_exit(self) -> None:
        server_status = get_server_status(self.config)
        target_pid = server_status.pid or self.pid
        if process_exists(target_pid):
            answer = messagebox.askyesnocancel(
                "Salir",
                "El servidor continuará ejecutándose en segundo plano.\n\n"
                "Sí: mantener servidor ejecutándose y salir.\n"
                "No: detener servidor y salir.\n"
                "Cancelar: volver al panel.",
            )
            if answer is None:
                return
            if answer is False:
                if not self._is_managed_waitress(target_pid):
                    Dialogs.error("Salir", "El PID almacenado no corresponde al Waitress de Gestion Fiduciaria.")
                    return
                if not stop_process(target_pid):
                    Dialogs.error("Salir", "No se pudo detener el proceso Waitress.")
                    return
                if not wait_for_port_release(self.port):
                    Dialogs.error("Salir", "Waitress se detuvo, pero el puerto no quedo libre.")
                    return
                update_config({"pid": None})
        self._dispose()
        self.master.destroy()

    def _dispose(self) -> None:
        if self._closed:
            return
        self._closed = True
        for after_id in [self._refresh_after_id, self._logs_after_id]:
            if after_id and self._widget_exists(self.master):
                try:
                    self.master.after_cancel(after_id)
                except Exception:
                    LOGGER.debug("No se pudo cancelar callback after %s", after_id, exc_info=True)
        self._refresh_after_id = None
        self._logs_after_id = None
        self._clear_dynamic_references()

    def destroy(self) -> None:
        self._dispose()
        super().destroy()

    def _after_ui(self, callback) -> None:
        if not self._closed and self._widget_exists(self.master):
            self.master.after(0, callback)

    def _set_label(self, widget: ttk.Label | None, text: str) -> None:
        if self._widget_exists(widget):
            widget.configure(text=text)

    def _set_indicator_label(self, widget: ttk.Label | None, text: str, level: str) -> None:
        if not self._widget_exists(widget):
            return
        prefix = {"ok": "OK", "warning": "!", "error": "X"}.get(level, "?")
        style = {"ok": "Ok.TLabel", "warning": "Warning.TLabel", "error": "Error.TLabel"}.get(level, "Muted.TLabel")
        widget.configure(text=f"{prefix}  {text}", style=style)

    def _set_server_card(self, name: str, value: str, detail: str) -> None:
        self._set_label(self.server_labels.get(name), self._value_or_na(value))
        detail_label = self.server_cards.get(name, {}).get("detail")
        self._set_label(detail_label, self._value_or_na(detail))

    def _update_status_bar(self, statuses: dict[str, bool], server_status) -> None:
        general_ok = all(statuses.values())
        if general_ok:
            general_text = "Sistema correcto"
        elif statuses.get("Proyecto configurado") and statuses.get("Archivo .env válido"):
            general_text = "Sistema con advertencias"
        else:
            general_text = "Sistema requiere atencion"
        self.status_general.set(general_text)
        self.status_server.set(server_status.state)
        self.status_updated.set(f"Ultima actualizacion: {timestamp_text()}")

    def _set_busy(self, running: bool, message: str) -> None:
        self.operation_message.set(message)
        if self._widget_exists(self.operation_progress):
            if running:
                self.operation_progress.start(12)
            else:
                self.operation_progress.stop()

    def _widget_exists(self, widget) -> bool:
        if widget is None:
            return False
        try:
            return bool(widget.winfo_exists())
        except Exception:
            return False

    def _safe_read_env(self) -> dict[str, str]:
        try:
            return read_env(self.project_path)
        except Exception:
            LOGGER.exception("No se pudo leer el archivo .env para mostrar informacion")
            return {}

    def _value_or_na(self, value) -> str:
        if value is None:
            return NOT_AVAILABLE
        text = str(value).strip()
        return text if text and text != "-" else NOT_AVAILABLE

    def _reconciled_config(self) -> dict:
        config = load_config() or self.config
        return self._reconcile_config_with_status(config, get_server_status(config))

    def _reconcile_config_with_status(self, config: dict, status) -> dict:
        try:
            stored_pid = int(config.get("pid")) if config.get("pid") else None
        except (TypeError, ValueError):
            stored_pid = None
        project_path = Path(config.get("project_path", ""))
        if status.pid and stored_pid != status.pid:
            LOGGER.warning("PID almacenado corregido de %s a %s", stored_pid, status.pid)
            return update_config({"pid": status.pid})
        if stored_pid and not status.pid and not process_exists(stored_pid):
            LOGGER.warning("PID almacenado %s ya no existe; se limpia configuracion", stored_pid)
            return update_config({"pid": None})
        if stored_pid and not status.pid and not self._is_managed_waitress(stored_pid):
            LOGGER.error("PID almacenado %s no corresponde a Waitress del proyecto", stored_pid)
        return config

    def _is_managed_waitress(self, pid: int | None) -> bool:
        return is_waitress_process(
            pid,
            self.project_path,
            port=self.port,
            wsgi_module=self.wsgi_module,
            venv_path=self.venv_path,
        )
