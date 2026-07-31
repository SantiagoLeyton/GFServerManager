import ctypes
import logging
import os
import sys

from app.logging_config import configure_logging, data_dir, logs_dir, source_root
from app.admin_panel import AdminPanel
from app.config_manager import CONFIG_PATH, is_config_complete, load_config, try_reconstruct_config
from app.metadata import PRODUCT_NAME
from app.ui_components import apply_app_icon, configure_styles
from app.wizard import InstallWizard
from tkinter import Tk


LOGGER = logging.getLogger(__name__)
_MUTEX_HANDLE = None
ERROR_ALREADY_EXISTS = 183


def acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    kernel32 = ctypes.windll.kernel32
    _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\GFServerManager.SingleInstance")
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


if __name__ == "__main__":
    if acquire_single_instance():
        configure_logging()
        launch()
