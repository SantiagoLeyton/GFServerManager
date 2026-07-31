from __future__ import annotations

from datetime import datetime
from tkinter import Toplevel, messagebox
from tkinter import ttk

from .metadata import app_icon_path


COLORS = {
    "bg": "#f5f7fb",
    "surface": "#ffffff",
    "sidebar": "#17202a",
    "sidebar_hover": "#22303d",
    "sidebar_active": "#2f80ed",
    "text": "#1f2933",
    "muted": "#5f6b7a",
    "border": "#d8dee8",
    "ok": "#178a4c",
    "warning": "#b7791f",
    "error": "#c53030",
    "info": "#2f80ed",
}


def apply_app_icon(window) -> None:
    icon = app_icon_path()
    if icon.exists():
        try:
            window.iconbitmap(default=str(icon))
        except Exception:
            pass


def configure_styles() -> None:
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(".", font=("Segoe UI", 10), foreground=COLORS["text"])
    style.configure("App.TFrame", background=COLORS["bg"])
    style.configure("Content.TFrame", background=COLORS["bg"])
    style.configure("Sidebar.TFrame", background=COLORS["sidebar"])
    style.configure("SidebarTitle.TLabel", background=COLORS["sidebar"], foreground="#ffffff", font=("Segoe UI", 14, "bold"))
    style.configure("SidebarSubtle.TLabel", background=COLORS["sidebar"], foreground="#b7c1cc", font=("Segoe UI", 9))
    style.configure("Nav.TLabel", background=COLORS["sidebar"], foreground="#dbe4ee", padding=(12, 9), font=("Segoe UI", 10))
    style.configure("NavHover.TLabel", background=COLORS["sidebar_hover"], foreground="#ffffff", padding=(12, 9), font=("Segoe UI", 10))
    style.configure("NavActive.TLabel", background=COLORS["sidebar_active"], foreground="#ffffff", padding=(12, 9), font=("Segoe UI", 10, "bold"))
    style.configure("Title.TLabel", background=COLORS["bg"], font=("Segoe UI", 18, "bold"), foreground=COLORS["text"])
    style.configure("Subtitle.TLabel", background=COLORS["bg"], font=("Segoe UI", 10), foreground=COLORS["muted"])
    style.configure("Section.TLabel", background=COLORS["surface"], font=("Segoe UI", 12, "bold"), foreground=COLORS["text"])
    style.configure("Card.TFrame", background=COLORS["surface"], relief="solid", borderwidth=1)
    style.configure("CardTitle.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI", 9, "bold"))
    style.configure("CardValue.TLabel", background=COLORS["surface"], foreground=COLORS["text"], font=("Segoe UI", 17, "bold"))
    style.configure("CardDetail.TLabel", background=COLORS["surface"], foreground=COLORS["muted"], font=("Segoe UI", 9))
    style.configure("Ok.TLabel", foreground=COLORS["ok"])
    style.configure("Warning.TLabel", foreground=COLORS["warning"])
    style.configure("Error.TLabel", foreground=COLORS["error"])
    style.configure("Muted.TLabel", foreground=COLORS["muted"])
    style.configure("StatusBar.TFrame", background="#e8edf5")
    style.configure("StatusBar.TLabel", background="#e8edf5", foreground=COLORS["muted"], font=("Segoe UI", 9))
    style.configure("Primary.TButton", padding=(12, 7), font=("Segoe UI", 10, "bold"))
    style.configure("TButton", padding=(10, 6))
    style.configure("Treeview", rowheight=28, font=("Segoe UI", 10))
    style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))


class Tooltip:
    def __init__(self, widget, text: str, delay_ms: int = 450) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._tip: Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _show(self) -> None:
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self._tip = Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        ttk.Label(self._tip, text=self.text, padding=(10, 6), wraplength=280).pack()

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None

    def _cancel(self) -> None:
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None


def attach_tooltip(widget, text: str):
    Tooltip(widget, text)
    return widget


class Dialogs:
    @staticmethod
    def info(title: str, message: str) -> None:
        messagebox.showinfo(title, message)

    @staticmethod
    def success(title: str, message: str) -> None:
        messagebox.showinfo(title, message)

    @staticmethod
    def warning(title: str, message: str) -> None:
        messagebox.showwarning(title, message)

    @staticmethod
    def error(title: str, message: str) -> None:
        messagebox.showerror(title, message)

    @staticmethod
    def confirm(title: str, message: str) -> bool:
        return bool(messagebox.askyesno(title, message))


def timestamp_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
