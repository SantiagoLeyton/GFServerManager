from __future__ import annotations

import re
import time
from dataclasses import dataclass

import psycopg2
from psycopg2 import OperationalError
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


@dataclass
class DatabaseCheckResult:
    ok: bool
    status: str
    message: str
    elapsed_ms: int | None = None


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


def test_connection(credentials: DatabaseCredentials) -> DatabaseCheckResult:
    started = time.perf_counter()
    try:
        check_connection(credentials)
    except OperationalError as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        message = str(exc).lower()
        if "password authentication failed" in message or "authentication failed" in message:
            return DatabaseCheckResult(False, "credenciales_invalidas", "Credenciales incorrectas.", elapsed)
        if "does not exist" in message and "database" in message:
            return DatabaseCheckResult(False, "base_inexistente", "La base de datos no existe.", elapsed)
        if "timeout expired" in message or "timed out" in message or "timeout" in message:
            return DatabaseCheckResult(False, "timeout", "La conexion supero el tiempo de espera.", elapsed)
        if "connection refused" in message or "could not connect" in message or "no connection could be made" in message:
            return DatabaseCheckResult(False, "servidor_apagado", "PostgreSQL no responde en el host/puerto configurado.", elapsed)
        return DatabaseCheckResult(False, "error_operacional", "No se pudo conectar a PostgreSQL.", elapsed)
    except Exception:
        elapsed = int((time.perf_counter() - started) * 1000)
        if _looks_like_decoding_error():
            return _classify_decoding_failure(credentials, elapsed)
        return DatabaseCheckResult(False, "error_inesperado", "Error inesperado al probar la conexion.", elapsed)
    elapsed = int((time.perf_counter() - started) * 1000)
    return DatabaseCheckResult(True, "ok", "Conexion correcta.", elapsed)


def _looks_like_decoding_error() -> bool:
    import sys

    exc = sys.exc_info()[1]
    return isinstance(exc, UnicodeDecodeError)


def _classify_decoding_failure(credentials: DatabaseCredentials, elapsed_ms: int) -> DatabaseCheckResult:
    try:
        with psycopg2.connect(
            host=credentials.host,
            port=credentials.port,
            dbname="postgres",
            user=credentials.user,
            password=credentials.password,
            connect_timeout=5,
        ):
            return DatabaseCheckResult(False, "base_inexistente", "La base de datos no existe.", elapsed_ms)
    except UnicodeDecodeError:
        return DatabaseCheckResult(False, "credenciales_invalidas", "Credenciales incorrectas.", elapsed_ms)
    except OperationalError as exc:
        message = str(exc).lower()
        if "connection refused" in message or "could not connect" in message or "no connection could be made" in message:
            return DatabaseCheckResult(False, "servidor_apagado", "PostgreSQL no responde en el host/puerto configurado.", elapsed_ms)
        if "timeout" in message or "timed out" in message:
            return DatabaseCheckResult(False, "timeout", "La conexion supero el tiempo de espera.", elapsed_ms)
        return DatabaseCheckResult(False, "credenciales_invalidas", "Credenciales incorrectas.", elapsed_ms)
    except Exception:
        return DatabaseCheckResult(False, "error_inesperado", "Error inesperado al probar la conexion.", elapsed_ms)


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
