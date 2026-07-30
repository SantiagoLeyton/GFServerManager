from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg2
from psycopg2 import sql


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@dataclass
class DatabaseCredentials:
    host: str
    port: int
    database: str
    user: str
    password: str


@dataclass
class AdminDatabaseCredentials:
    host: str
    port: int
    user: str
    password: str
    database: str = "postgres"


def validate_identifier(identifier: str) -> bool:
    return bool(IDENTIFIER_RE.fullmatch(identifier or ""))


def check_connection(credentials: DatabaseCredentials) -> None:
    with psycopg2.connect(
        host=credentials.host,
        port=credentials.port,
        dbname=credentials.database,
        user=credentials.user,
        password=credentials.password,
        connect_timeout=5,
    ):
        return


def ensure_database(admin: AdminDatabaseCredentials, db_name: str, owner: str, owner_password: str) -> list[str]:
    if not validate_identifier(db_name):
        raise ValueError("El nombre de la base de datos no es un identificador SQL valido.")
    if not validate_identifier(owner):
        raise ValueError("El owner no es un identificador SQL valido.")

    messages: list[str] = []
    with psycopg2.connect(
        host=admin.host,
        port=admin.port,
        dbname=admin.database,
        user=admin.user,
        password=admin.password,
        connect_timeout=5,
    ) as conn:
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (owner,))
            role_exists = cursor.fetchone() is not None
            if role_exists:
                messages.append(f"El rol {owner} ya existe; se usara sin modificar la contrasena.")
            else:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(owner)),
                    (owner_password,),
                )
                messages.append(f"Rol {owner} creado.")

            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            db_exists = cursor.fetchone() is not None
            if db_exists:
                messages.append(f"La base de datos {db_name} ya existe; no se modifico.")
            else:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db_name), sql.Identifier(owner))
                )
                messages.append(f"Base de datos {db_name} creada.")

            cursor.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                    sql.Identifier(db_name), sql.Identifier(owner)
                )
            )
            messages.append("Permisos verificados para el owner.")
    return messages
