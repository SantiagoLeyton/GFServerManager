from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


REQUIRED_PATHS = [
    "config",
    "core",
    "docs/technical",
    "fiduciary",
    "real_estate",
    "static",
    "templates",
    "tests",
    "users",
    ".env.example",
    ".gitignore",
    "README.md",
    "manage.py",
    "pytest.ini",
    "requirements.txt",
    "config/wsgi.py",
    "config/settings.py",
    "users/models.py",
]

ENV_KEYS = [
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "DB_CONNECT_TIMEOUT",
    "SESSION_COOKIE_SECURE",
    "CSRF_COOKIE_SECURE",
]


@dataclass
class ProjectInspection:
    valid: bool
    errors: list[str]
    env_keys: list[str]
    wsgi_module: str
    username_field: str
    required_user_fields: list[str]
    role_values: dict[str, str]
    static_root: str
    media_root: str


def validate_project(path: str | Path) -> ProjectInspection:
    root = Path(path)
    errors: list[str] = []
    if not root.exists() or not root.is_dir():
        errors.append("La carpeta seleccionada no existe o no es una carpeta.")
    else:
        for relative in REQUIRED_PATHS:
            if not (root / relative).exists():
                errors.append(f"Falta {relative}")

    wsgi_module = "config.wsgi:application"
    username_field = "username"
    required_user_fields = ["email"]
    role_values = {"Contabilidad": "accounting_admin", "Comercial": "commercial"}
    static_root = "staticfiles"
    media_root = "media"

    if root.exists():
        settings_text = _read(root / "config" / "settings.py")
        models_text = _read(root / "users" / "models.py")
        wsgi_text = _read(root / "config" / "wsgi.py")

        if "WSGI_APPLICATION = \"config.wsgi.application\"" not in settings_text:
            errors.append("WSGI_APPLICATION no coincide con config.wsgi.application.")
        if "DJANGO_SETTINGS_MODULE\", \"config.settings\"" not in wsgi_text:
            errors.append("config/wsgi.py no apunta a config.settings.")
        if "AUTH_USER_MODEL = \"users.User\"" not in settings_text:
            errors.append("AUTH_USER_MODEL no coincide con users.User.")
        if "ACCOUNTING_ADMIN = \"accounting_admin\"" not in models_text:
            errors.append("No se encontro el rol real accounting_admin.")
        if "COMMERCIAL = \"commercial\"" not in models_text:
            errors.append("No se encontro el rol real commercial.")
        if "REQUIRED_FIELDS = [\"email\"]" not in models_text:
            errors.append("Los campos obligatorios del usuario no coinciden con email.")

    return ProjectInspection(
        valid=not errors,
        errors=errors,
        env_keys=ENV_KEYS,
        wsgi_module=wsgi_module,
        username_field=username_field,
        required_user_fields=required_user_fields,
        role_values=role_values,
        static_root=static_root,
        media_root=media_root,
    )


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")

