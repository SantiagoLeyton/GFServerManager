import ctypes
import logging
import os
import sys
from pathlib import Path

from app.logging_config import configure_logging, data_dir, logs_dir, source_root
from app.admin_panel import AdminPanel
from app.config_manager import CONFIG_PATH, is_config_complete, load_config, try_reconstruct_config
from app.metadata import PRODUCT_NAME
from app.server_manager import check_http_health, get_server_status, start_waitress
from app.startup_task import install_startup_task, remove_startup_task
from app.ui_components import apply_app_icon, configure_styles
from app.wizard import InstallWizard
from app.django_manager import venv_path as project_venv_path
from tkinter import Tk


LOGGER = logging.getLogger(__name__)
_MUTEX_HANDLE = None
ERROR_ALREADY_EXISTS = 183
NORMAL_MUTEX = "Local\\GFServerManager.Panel"
STARTUP_MUTEX = "Local\\GFServerManager.Startup"


def acquire_single_instance(name: str = NORMAL_MUTEX) -> bool:
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, name)
    return ctypes.GetLastError() != ERROR_ALREADY_EXISTS


def launch() -> None:
    LOGGER.info("Inicio de Gestion Fiduciaria Server Manager")
    _log_startup_context()
    root = Tk()
    root.title(PRODUCT_NAME)
    configure_styles()
    apply_app_icon(root)

    def clear_root() -> None:
        for child in root.winfo_children():
            child.destroy()

    def show_wizard(allow_cancel: bool = False) -> None:
        clear_root()
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        wizard = InstallWizard(
            root,
            on_complete=show_panel,
            on_cancel=show_panel if allow_cancel else None,
        )
        if allow_cancel:
            root.protocol("WM_DELETE_WINDOW", wizard._cancel_clicked)

    def show_panel() -> None:
        clear_root()
        load_config()
        AdminPanel(root, on_reinstall=lambda: show_wizard(allow_cancel=True))

    config = load_config()
    if config is None:
        config = try_reconstruct_config()
    if is_config_complete(config):
        show_panel()
    else:
        show_wizard()

    try:
        root.mainloop()
    finally:
        LOGGER.info("Cierre de Gestion Fiduciaria Server Manager")


def _log_startup_context() -> None:
    try:
        parent_pid = "No disponible"
        try:
            import psutil

            parent_pid = str(psutil.Process(os.getpid()).ppid())
        except Exception:
            pass
        LOGGER.info(
            "Contexto arranque pid=%s ppid=%s executable=%s argv=%s frozen=%s source_root=%s config_path=%s data_dir=%s logs_dir=%s",
            os.getpid(),
            parent_pid,
            sys.executable,
            sys.argv,
            getattr(sys, "frozen", False),
            source_root(),
            CONFIG_PATH,
            data_dir(),
            logs_dir(),
        )
    except Exception:
        LOGGER.exception("No se pudo registrar contexto de arranque")


def run_startup_mode() -> int:
    LOGGER.info("Inicio automatico solicitado")
    _log_startup_context()
    config = load_config()
    if not is_config_complete(config):
        LOGGER.error("Inicio automatico cancelado: instalacion incompleta o server_manager.json no disponible")
        return 2

    status = get_server_status(config)
    if status.running:
        LOGGER.info("Waitress ya estaba activo pid=%s port=%s", status.pid, status.port)
        return 0
    if status.conflict_message:
        LOGGER.error("Inicio automatico cancelado por conflicto de puerto: %s", status.conflict_message)
        return 3

    try:
        project_path = Path(str(config["project_path"]))
        venv_path = project_venv_path(project_path)
        port = int(config["port"])
        host = str(config.get("host") or "0.0.0.0")
        LOGGER.info("Automatizacion de backups delegada al scheduler interno de Gestion Fiduciaria.")
        process = start_waitress(project_path, venv_path, port, host=host, wsgi_module=str(config.get("wsgi_module") or "config.wsgi:application"))
        health = check_http_health(port, timeout_seconds=30)
        if not health.responding:
            LOGGER.error("Waitress inicio en modo automatico, pero la aplicacion no respondio pid=%s port=%s", process.pid, port)
            return 4
        if not health.ok:
            LOGGER.error("Waitress responde en modo automatico, pero la aplicacion fallo: %s", health.message)
            return 4
        status = get_server_status(config)
        pid = status.pid or process.pid
        from app.config_manager import detect_hostname, detect_local_ipv4, update_config

        update_config({"pid": pid, "ipv4": detect_local_ipv4(), "hostname": detect_hostname()})
        LOGGER.info("Inicio automatico completo pid=%s host=%s port=%s", pid, host, port)
        return 0
    except Exception:
        LOGGER.exception("Fallo el inicio automatico de Waitress")
        return 1


def install_startup_task_mode() -> int:
    LOGGER.info("Instalacion de tarea de inicio automatico solicitada")
    _log_startup_context()
    try:
        config = load_config()
        if not is_config_complete(config):
            LOGGER.error("No se pudo instalar autoarranque: instalacion incompleta o server_manager.json no disponible")
            return 2
        install_startup_task(
            Path(str(config["project_path"])),
            project_venv_path(Path(str(config["project_path"]))),
            host=str(config.get("host") or "0.0.0.0"),
            port=int(config["port"]),
            wsgi_module=str(config.get("wsgi_module") or "config.wsgi:application"),
        )
        return 0
    except Exception:
        LOGGER.exception("No se pudo instalar la tarea de inicio automatico")
        return 1


def remove_startup_task_mode() -> int:
    LOGGER.info("Eliminacion de tarea de inicio automatico solicitada")
    _log_startup_context()
    try:
        remove_startup_task()
        return 0
    except Exception:
        LOGGER.exception("No se pudo eliminar la tarea de inicio automatico")
        return 1


if __name__ == "__main__":
    configure_logging()
    args = set(sys.argv[1:])
    if "--startup" in args:
        if acquire_single_instance(STARTUP_MUTEX):
            raise SystemExit(run_startup_mode())
        LOGGER.info("Inicio automatico omitido: ya hay otra instancia --startup activa")
        raise SystemExit(0)
    if "--install-startup-task" in args:
        raise SystemExit(install_startup_task_mode())
    if "--remove-startup-task" in args:
        raise SystemExit(remove_startup_task_mode())
    if acquire_single_instance(NORMAL_MUTEX):
        launch()
