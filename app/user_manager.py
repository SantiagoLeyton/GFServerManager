from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

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
