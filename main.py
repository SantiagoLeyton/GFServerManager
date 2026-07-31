import logging

from app.logging_config import configure_logging
from app.admin_panel import AdminPanel
from app.config_manager import is_config_complete, load_config, try_reconstruct_config
from app.wizard import InstallWizard
from tkinter import Tk


LOGGER = logging.getLogger(__name__)


def launch() -> None:
    LOGGER.info("Inicio de Gestion Fiduciaria Server Manager")
    root = Tk()

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


if __name__ == "__main__":
    configure_logging()
    launch()
