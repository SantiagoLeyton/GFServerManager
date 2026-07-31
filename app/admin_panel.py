from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser
from pathlib import Path
from tkinter import IntVar, StringVar, Tk, Toplevel, messagebox
from tkinter import scrolledtext
from tkinter import ttk

from .config_manager import detect_hostname, detect_local_ipv4, load_config, update_config
from .database_manager import check_connection
from .django_manager import find_static_probe_url
from .environment_manager import env_database_credentials, is_env_valid, read_env
from .logging_config import app_root
from .project_validator import validate_project
from .server_manager import (
    find_process_on_port,
    find_waitress_listener_pid,
    is_waitress_process,
    process_exists,
    process_start_time,
    start_waitress,
    stop_process,
    wait_for_http,
    wait_for_static_file,
)
from .user_manager import change_password, create_user, list_users, set_user_active


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
        self.users_tree: ttk.Treeview | None = None
        self.logs_text: scrolledtext.ScrolledText | None = None

        self._refresh_after_id: str | None = None
        self._logs_after_id: str | None = None
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
        return Path(self.config.get("venv_path", ""))

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
        self.master.title("Gestion Fiduciaria Server Manager")
        self.master.geometry("1120x760")
        self.master.minsize(980, 640)
        self.master.columnconfigure(0, weight=1)
        self.master.rowconfigure(0, weight=1)

        self.grid(sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        style = ttk.Style()
        style.configure("Sidebar.TFrame", background="#17202a")
        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Section.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Card.TFrame", relief="solid", borderwidth=1)

        sidebar = ttk.Frame(self, width=210, style="Sidebar.TFrame", padding=(12, 16))
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)

        ttk.Label(
            sidebar,
            text="Server Manager",
            foreground="white",
            background="#17202a",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w", pady=(0, 18))

        for section in ["Inicio", "Servidor", "Base de datos", "Usuarios", "Configuración", "Logs"]:
            ttk.Button(sidebar, text=section, command=lambda name=section: self._show_section(name)).pack(
                fill="x", pady=4
            )

        ttk.Separator(sidebar).pack(fill="x", pady=14)
        ttk.Button(sidebar, text="Reinstalar", command=self._reinstall).pack(fill="x", pady=4)

        self.content = ttk.Frame(self, padding=18)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(1, weight=1)

    def _show_section(self, name: str) -> None:
        if self._closed or not self._widget_exists(self.content):
            return
        self.current_section = name
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
            "Usuarios": self._build_users,
            "Configuración": self._build_configuration,
            "Logs": self._build_logs,
        }
        builders[name](container)
        self._refresh_status()

    def _clear_dynamic_references(self) -> None:
        self.status_labels.clear()
        self.info_labels.clear()
        self.server_labels.clear()
        self.db_labels.clear()
        self.config_vars.clear()
        self.users_tree = None
        self.logs_text = None

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
            ("Abrir carpeta de logs", lambda: self._open_folder(app_root() / "logs")),
        ]
        for index, (text, command) in enumerate(buttons):
            ttk.Button(actions, text=text, command=command).grid(
                row=1 + index // 3, column=index % 3, sticky="ew", padx=4, pady=4
            )
            actions.columnconfigure(index % 3, weight=1)

    def _build_server(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Proceso Waitress")
        card.grid(row=0, column=0, sticky="nsew")
        for index, label in enumerate(["Estado", "PID", "Host", "Puerto", "Tiempo de ejecución", "Proceso en puerto"]):
            ttk.Label(card, text=label + ":").grid(row=index + 1, column=0, sticky="w", pady=4)
            widget = ttk.Label(card, text=NOT_AVAILABLE)
            widget.grid(row=index + 1, column=1, sticky="w", pady=4)
            self.server_labels[label] = widget

        buttons = ttk.Frame(card)
        buttons.grid(row=8, column=0, columnspan=2, sticky="w", pady=(14, 0))
        ttk.Button(buttons, text="Iniciar", command=self._start_server).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Detener", command=self._stop_server).pack(side="left", padx=8)
        ttk.Button(buttons, text="Reiniciar", command=self._restart_server).pack(side="left", padx=8)

    def _build_database(self, parent: ttk.Frame) -> None:
        card = self._card(parent, "Base de datos")
        card.grid(row=0, column=0, sticky="nsew")
        for index, label in enumerate(["Host", "Puerto", "Base", "Usuario", "Estado de conexión"]):
            ttk.Label(card, text=label + ":").grid(row=index + 1, column=0, sticky="w", pady=4)
            widget = ttk.Label(card, text=NOT_AVAILABLE)
            widget.grid(row=index + 1, column=1, sticky="w", pady=4)
            self.db_labels[label] = widget
        ttk.Button(card, text="Probar conexión", command=self._test_database).grid(
            row=7, column=0, sticky="w", pady=(14, 0)
        )

    def _build_users(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        columns = ("name", "email", "active", "groups")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        self.users_tree = tree
        for key, text, width in [
            ("name", "Nombre", 220),
            ("email", "Correo", 260),
            ("active", "Activo", 80),
            ("groups", "Grupos", 240),
        ]:
            tree.heading(key, text=text)
            tree.column(key, width=width, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        buttons = ttk.Frame(parent)
        buttons.grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Button(buttons, text="Actualizar", command=self._load_users).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Crear usuario", command=self._open_create_user_dialog).pack(side="left", padx=8)
        ttk.Button(buttons, text="Cambiar contraseña", command=self._open_password_dialog).pack(side="left", padx=8)
        ttk.Button(buttons, text="Activar/desactivar", command=self._toggle_user_active).pack(side="left", padx=8)
        self._load_users()

    def _build_configuration(self, parent: ttk.Frame) -> None:
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
        ttk.Button(card, text="Guardar host y puerto", command=self._save_host_port).grid(
            row=7, column=0, sticky="w", pady=(14, 0)
        )

    def _build_logs(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        self.logs_text = scrolledtext.ScrolledText(parent, wrap="word")
        self.logs_text.grid(row=0, column=0, sticky="nsew")
        ttk.Button(parent, text="Abrir carpeta de logs", command=lambda: self._open_folder(app_root() / "logs")).grid(
            row=1, column=0, sticky="w", pady=(12, 0)
        )
        self._refresh_logs()

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
        self.config = load_config() or self.config
        statuses = self._status_snapshot()

        if self.current_section == "Inicio":
            for label, ok in statuses.items():
                self._set_label(self.status_labels.get(label), f"{'●' if ok else '○'} {label}")
            self._set_label(self.info_labels.get("Ruta del proyecto"), self._value_or_na(str(self.project_path)))
            self._set_label(self.info_labels.get("Host"), self._value_or_na(self.host))
            self._set_label(self.info_labels.get("Puerto"), self._value_or_na(str(self.port)))
            self._set_label(self.info_labels.get("Dirección IP"), self._value_or_na(self.config.get("ipv4") or detect_local_ipv4()))
            self._set_label(self.info_labels.get("Hostname"), self._value_or_na(self.config.get("hostname") or detect_hostname()))
            self._set_label(self.info_labels.get("Tiempo desde el último inicio"), self._value_or_na(self._uptime_text()))

        if self.current_section == "Servidor":
            running = is_waitress_process(self.pid, self.project_path)
            self._set_label(self.server_labels.get("Estado"), "Ejecutándose" if running else "Detenido")
            self._set_label(self.server_labels.get("PID"), str(self.pid) if running else NOT_AVAILABLE)
            self._set_label(self.server_labels.get("Host"), self._value_or_na(self.host))
            self._set_label(self.server_labels.get("Puerto"), self._value_or_na(str(self.port)))
            self._set_label(self.server_labels.get("Tiempo de ejecución"), self._value_or_na(self._uptime_text()))
            self._set_label(self.server_labels.get("Proceso en puerto"), self._value_or_na(find_process_on_port(self.port)))
            if self.pid and not running and not process_exists(self.pid):
                self.config = update_config({"pid": None})

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

    def _status_snapshot(self) -> dict[str, bool]:
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
            waitress_ok = is_waitress_process(self.pid, self.project_path)
            app_ok = wait_for_http(self.port, timeout_seconds=1) if waitress_ok else False
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
            check_connection(env_database_credentials(self.project_path))
            if not silent:
                self._set_label(self.db_labels.get("Estado de conexión"), "Conectada")
            return True
        except Exception as exc:
            if silent:
                LOGGER.debug("Base de datos no disponible durante refresco: %s", exc)
            else:
                LOGGER.exception("Fallo la prueba de conexion a base de datos")
                self._set_label(self.db_labels.get("Estado de conexión"), "Sin conexión")
                messagebox.showerror("Base de datos", str(exc))
            return False

    def _test_database(self) -> None:
        if self._database_ok(silent=False):
            messagebox.showinfo("Base de datos", "Conexión comprobada correctamente.")

    def _start_server(self) -> None:
        self._run_background(self._start_server_worker, "Servidor")

    def _start_server_worker(self) -> None:
        if is_waitress_process(self.pid, self.project_path):
            self._info("Servidor", "Waitress ya está ejecutándose.")
            return
        if self.pid and process_exists(self.pid):
            raise RuntimeError("El PID almacenado existe, pero no corresponde al Waitress de Gestion Fiduciaria.")
        static_url = find_static_probe_url(self.project_path, self.venv_path)
        process = start_waitress(self.project_path, self.venv_path, self.port, host=self.host)
        if not wait_for_http(self.port):
            raise RuntimeError("Waitress inició, pero la aplicación no respondió.")
        if not wait_for_static_file(self.port, static_url):
            raise RuntimeError("La aplicación respondió, pero no sirvió archivos estáticos.")
        listener_pid = find_waitress_listener_pid(self.port, self.project_path) or process.pid
        self.config = update_config({"pid": listener_pid, "ipv4": detect_local_ipv4(), "hostname": detect_hostname()})
        self._info("Servidor", "Servidor iniciado correctamente.")

    def _stop_server(self) -> None:
        self._run_background(self._stop_server_worker, "Servidor")

    def _stop_server_worker(self) -> None:
        if not process_exists(self.pid):
            self.config = update_config({"pid": None})
            self._info("Servidor", "No hay un proceso Waitress activo registrado.")
            return
        if not is_waitress_process(self.pid, self.project_path):
            raise RuntimeError("El PID almacenado existe, pero no corresponde al Waitress de Gestion Fiduciaria.")
        if not stop_process(self.pid):
            raise RuntimeError("No se pudo detener el proceso Waitress dentro del tiempo esperado.")
        self.config = update_config({"pid": None})
        self._info("Servidor", "Servidor detenido correctamente.")

    def _restart_server(self) -> None:
        self._run_background(self._restart_server_worker, "Servidor")

    def _restart_server_worker(self) -> None:
        if process_exists(self.pid):
            if not is_waitress_process(self.pid, self.project_path):
                raise RuntimeError("El PID almacenado existe, pero no corresponde al Waitress de Gestion Fiduciaria.")
            if not stop_process(self.pid):
                raise RuntimeError("No se pudo detener el proceso Waitress para reiniciar.")
            self.config = update_config({"pid": None})
        self._start_server_worker()

    def _load_users(self) -> None:
        if not self._widget_exists(self.users_tree):
            return
        try:
            self.users_tree.delete(*self.users_tree.get_children())
            for user in list_users(self.project_path, self.venv_path):
                groups = ", ".join(user.get("groups") or [])
                active = "Sí" if user.get("is_active") else "No"
                self.users_tree.insert(
                    "",
                    "end",
                    iid=str(user["id"]),
                    values=(user.get("name", ""), user.get("email", ""), active, groups),
                    tags=("active" if user.get("is_active") else "inactive",),
                )
        except Exception as exc:
            LOGGER.exception("Fallo la lectura de usuarios")
            messagebox.showerror("Usuarios", str(exc))

    def _selected_user_id(self) -> int | None:
        if not self._widget_exists(self.users_tree):
            return None
        selection = self.users_tree.selection()
        return int(selection[0]) if selection else None

    def _open_create_user_dialog(self) -> None:
        dialog = _UserDialog(self.master, "Crear usuario")
        self.master.wait_window(dialog)
        if not dialog.result:
            return
        self._run_background(lambda: self._create_user_worker(dialog.result), "Usuarios")

    def _create_user_worker(self, data: dict[str, str]) -> None:
        create_user(self.project_path, self.venv_path, data)
        self._after_ui(self._load_users)
        self._info("Usuarios", "Usuario creado correctamente.")

    def _open_password_dialog(self) -> None:
        user_id = self._selected_user_id()
        if not user_id:
            messagebox.showwarning("Usuarios", "Seleccione un usuario.")
            return
        dialog = _PasswordDialog(self.master)
        self.master.wait_window(dialog)
        if not dialog.result:
            return
        self._run_background(lambda: self._change_password_worker(user_id, dialog.result), "Usuarios")

    def _change_password_worker(self, user_id: int, password: str) -> None:
        change_password(self.project_path, self.venv_path, user_id, password)
        self._info("Usuarios", "Contraseña actualizada correctamente.")

    def _toggle_user_active(self) -> None:
        user_id = self._selected_user_id()
        if not user_id or not self._widget_exists(self.users_tree):
            messagebox.showwarning("Usuarios", "Seleccione un usuario.")
            return
        values = self.users_tree.item(str(user_id), "values")
        is_active = values[2] == "Sí"
        action = "desactivar" if is_active else "activar"
        if not messagebox.askyesno("Usuarios", f"¿Desea {action} este usuario?"):
            return
        self._run_background(lambda: self._toggle_user_worker(user_id, not is_active), "Usuarios")

    def _toggle_user_worker(self, user_id: int, active: bool) -> None:
        set_user_active(self.project_path, self.venv_path, user_id, active)
        self._after_ui(self._load_users)
        self._info("Usuarios", "Estado actualizado correctamente.")

    def _save_host_port(self) -> None:
        try:
            host = str(self.config_vars["Host"].get()).strip()
            port = int(self.config_vars["Puerto"].get())
            if not host:
                raise ValueError("El host no puede estar vacío.")
            if port < 1024 or port > 65535:
                raise ValueError("El puerto debe estar entre 1024 y 65535.")
            self.config = update_config({"host": host, "port": port})
            messagebox.showinfo("Configuración", "Host y puerto guardados. Reinicie el servidor para aplicar cambios.")
        except Exception as exc:
            messagebox.showerror("Configuración", str(exc))

    def _open_application(self) -> None:
        webbrowser.open(f"http://127.0.0.1:{self.port}/")

    def _open_folder(self, path: Path) -> None:
        if path.name == "logs":
            path.mkdir(parents=True, exist_ok=True)
        if path.exists():
            os.startfile(path)
        else:
            messagebox.showerror("Carpeta", f"No existe: {path}")

    def _refresh_logs(self) -> None:
        if not self._widget_exists(self.logs_text):
            return
        log_path = app_root() / "logs" / "server_manager.log"
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        except Exception:
            LOGGER.exception("No se pudo leer el archivo de logs")
            content = NOT_AVAILABLE
        self.logs_text.configure(state="normal")
        self.logs_text.delete("1.0", "end")
        self.logs_text.insert("end", content)
        self.logs_text.see("end")

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

    def _uptime_text(self) -> str:
        started = process_start_time(self.pid)
        if not started:
            return NOT_AVAILABLE
        seconds = int(time.time() - started)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _run_background(self, target, title: str) -> None:
        def runner() -> None:
            try:
                target()
            except Exception as exc:
                LOGGER.exception("Fallo en accion del panel")
                self._after_ui(lambda: messagebox.showerror(title, str(exc)))
            finally:
                self._after_ui(self._refresh_status)

        threading.Thread(target=runner, daemon=True).start()

    def _info(self, title: str, message: str) -> None:
        self._after_ui(lambda: messagebox.showinfo(title, message))

    def _reinstall(self) -> None:
        if not messagebox.askyesno("Reinstalar", "¿Desea abrir el asistente de instalación?"):
            return
        if self.on_reinstall:
            self._dispose()
            self.on_reinstall()

    def _confirm_exit(self) -> None:
        if process_exists(self.pid):
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
                if not is_waitress_process(self.pid, self.project_path):
                    messagebox.showerror("Salir", "El PID almacenado no corresponde al Waitress de Gestion Fiduciaria.")
                    return
                if not stop_process(self.pid):
                    messagebox.showerror("Salir", "No se pudo detener el proceso Waitress.")
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


class _UserDialog(Toplevel):
    def __init__(self, master: Tk, title: str) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.result: dict[str, str] | None = None
        self.vars = {
            "first_name": StringVar(),
            "last_name": StringVar(),
            "username": StringVar(),
            "email": StringVar(),
            "password": StringVar(),
            "password2": StringVar(),
        }
        self.role = StringVar(value="commercial")

        body = ttk.Frame(self, padding=14)
        body.grid(sticky="nsew")
        fields = [
            ("Nombres", "first_name", False),
            ("Apellidos", "last_name", False),
            ("Usuario", "username", False),
            ("Correo", "email", False),
            ("Contraseña", "password", True),
            ("Confirmar contraseña", "password2", True),
        ]
        for index, (label, key, secret) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=index, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=self.vars[key], show="*" if secret else "", width=34).grid(
                row=index, column=1, pady=4
            )
        ttk.Label(body, text="Rol").grid(row=6, column=0, sticky="w", pady=4)
        ttk.Combobox(
            body,
            textvariable=self.role,
            values=("commercial", "accounting_admin"),
            state="readonly",
            width=31,
        ).grid(row=6, column=1, pady=4)
        buttons = ttk.Frame(body)
        buttons.grid(row=7, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Crear", command=self._accept).pack(side="right")
        self.grab_set()

    def _accept(self) -> None:
        data = {key: var.get().strip() for key, var in self.vars.items()}
        if not data["username"] or not data["email"] or not data["password"]:
            messagebox.showerror("Usuarios", "Usuario, correo y contraseña son obligatorios.")
            return
        if data["password"] != data.pop("password2"):
            messagebox.showerror("Usuarios", "Las contraseñas no coinciden.")
            return
        role = self.role.get()
        data["role"] = role
        data["group"] = "Contabilidad" if role == "accounting_admin" else "Comercial"
        self.result = data
        self.destroy()


class _PasswordDialog(Toplevel):
    def __init__(self, master: Tk) -> None:
        super().__init__(master)
        self.title("Cambiar contraseña")
        self.resizable(False, False)
        self.result: str | None = None
        self.password = StringVar()
        self.password2 = StringVar()

        body = ttk.Frame(self, padding=14)
        body.grid(sticky="nsew")
        ttk.Label(body, text="Nueva contraseña").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.password, show="*", width=34).grid(row=0, column=1, pady=4)
        ttk.Label(body, text="Confirmar contraseña").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.password2, show="*", width=34).grid(row=1, column=1, pady=4)
        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Guardar", command=self._accept).pack(side="right")
        self.grab_set()

    def _accept(self) -> None:
        if not self.password.get():
            messagebox.showerror("Usuarios", "Digite la nueva contraseña.")
            return
        if self.password.get() != self.password2.get():
            messagebox.showerror("Usuarios", "Las contraseñas no coinciden.")
            return
        self.result = self.password.get()
        self.destroy()


def run(on_reinstall=None) -> None:
    root = Tk()
    AdminPanel(root, on_reinstall=on_reinstall)
    root.mainloop()
