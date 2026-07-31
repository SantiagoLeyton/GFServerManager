from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .django_manager import django_shell


LOGGER = logging.getLogger(__name__)


@dataclass
class InitialUser:
    label: str
    username: str
    email: str
    password: str
    role: str


def create_or_update_initial_users(project_path: Path, venv: Path, users: list[InitialUser], update_existing: bool) -> str:
    LOGGER.info("Creando o actualizando usuarios iniciales: %s", ", ".join(user.email for user in users))
    payload = json.dumps(
        {
            "update_existing": update_existing,
            "users": [user.__dict__ for user in users],
        }
    )
    code = r"""
import json
import sys
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password

payload = json.loads(sys.stdin.read())
User = get_user_model()
messages = []

for item in payload["users"]:
    password = item.pop("password")
    label = item.pop("label")
    group_name = label
    username = item["username"]
    email = item["email"].lower()
    role = item["role"]
    validate_password(password)
    group, _ = Group.objects.get_or_create(name=group_name)
    user = User.objects.filter(email__iexact=email).first() or User.objects.filter(username__iexact=username).first()
    if user:
        user.email = email
        user.username = username
        user.role = role
        user.is_active = True
        if payload["update_existing"]:
            user.set_password(password)
            password_text = "con contrasena actualizada"
        else:
            password_text = "sin cambiar contrasena"
        user.full_clean()
        user.save()
        user.groups.add(group)
        messages.append(f"{label}: usuario existente actualizado ({password_text}).")
    else:
        user = User.objects.create_user(username=username, email=email, password=password, role=role)
        user.groups.add(group)
        messages.append(f"{label}: usuario creado.")

print("\n".join(messages))
"""
    result = django_shell(project_path, venv, code, input_text=payload)
    return result.stdout.strip()


def list_users(project_path: Path, venv: Path) -> list[dict[str, Any]]:
    code = r"""
import json
from django.contrib.auth import get_user_model

User = get_user_model()
rows = []
for user in User.objects.prefetch_related("groups").order_by("username"):
    rows.append({
        "id": user.pk,
        "name": user.get_full_name() or user.username,
        "username": user.get_username(),
        "email": user.email,
        "is_active": user.is_active,
        "groups": [group.name for group in user.groups.all()],
        "role": getattr(user, "role", ""),
    })
print(json.dumps(rows, ensure_ascii=False))
"""
    result = django_shell(project_path, venv, code)
    output = result.stdout.strip().splitlines()
    return json.loads(output[-1]) if output else []


def create_user(project_path: Path, venv: Path, data: dict[str, str]) -> str:
    LOGGER.info("Creando usuario %s", data.get("email", "sin-correo"))
    payload = json.dumps(data)
    code = r"""
import json
import sys
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password

data = json.loads(sys.stdin.read())
User = get_user_model()
password = data["password"]
validate_password(password)
group_name = data["group"]
role = data["role"]
user = User.objects.create_user(
    username=data["username"],
    email=data["email"].lower(),
    password=password,
    first_name=data.get("first_name", ""),
    last_name=data.get("last_name", ""),
    role=role,
)
if group_name:
    group, _ = Group.objects.get_or_create(name=group_name)
    user.groups.add(group)
print("Usuario creado.")
"""
    result = django_shell(project_path, venv, code, input_text=payload)
    return result.stdout.strip()


def change_password(project_path: Path, venv: Path, user_id: int, password: str) -> str:
    LOGGER.info("Cambiando contrasena de usuario id=%s", user_id)
    payload = json.dumps({"user_id": user_id, "password": password})
    code = r"""
import json
import sys
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

data = json.loads(sys.stdin.read())
User = get_user_model()
user = User.objects.get(pk=data["user_id"])
validate_password(data["password"], user=user)
user.set_password(data["password"])
user.save(update_fields=["password"])
print("Contrasena actualizada.")
"""
    result = django_shell(project_path, venv, code, input_text=payload)
    return result.stdout.strip()


def set_user_active(project_path: Path, venv: Path, user_id: int, active: bool) -> str:
    LOGGER.info("Actualizando estado de usuario id=%s active=%s", user_id, active)
    payload = json.dumps({"user_id": user_id, "active": active})
    code = r"""
import json
import sys
from django.contrib.auth import get_user_model

data = json.loads(sys.stdin.read())
User = get_user_model()
user = User.objects.get(pk=data["user_id"])
user.is_active = bool(data["active"])
user.save(update_fields=["is_active"])
print("Estado actualizado.")
"""
    result = django_shell(project_path, venv, code, input_text=payload)
    return result.stdout.strip()
