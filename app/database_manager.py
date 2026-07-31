from __future__ import annotations

import logging
import re
import time
import traceback
from dataclasses import dataclass

import psycopg2
from psycopg2 import OperationalError
from psycopg2 import sql


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
LOGGER = logging.getLogger(__name__)


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


class DatabaseSetupError(RuntimeError):
    pass


def validate_identifier(identifier: str) -> bool:
    return bool(IDENTIFIER_RE.fullmatch(identifier or ""))


def safe_exception_message(exc: BaseException) -> str:
    try:
        message = str(exc)
    except UnicodeDecodeError:
        return "El mensaje tecnico de PostgreSQL no pudo interpretarse por la codificacion de Windows."
    return message or exc.__class__.__name__


def log_database_exception(stage: str, exc: BaseException) -> None:
    LOGGER.error(
        "Fallo PostgreSQL etapa=%s tipo=%s mensaje=%s\nTraceback:\n%s",
        stage,
        type(exc).__name__,
        safe_exception_message(exc),
        "".join(traceback.format_tb(exc.__traceback__)),
    )


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
        return _connection_result_from_operational_error(exc, elapsed)
    except UnicodeDecodeError:
        elapsed = int((time.perf_counter() - started) * 1000)
        return DatabaseCheckResult(
            False,
            "error_codificacion",
            "PostgreSQL devolvio un error, pero su mensaje no pudo interpretarse correctamente.",
            elapsed,
        )
    except Exception:
        elapsed = int((time.perf_counter() - started) * 1000)
        return DatabaseCheckResult(False, "error_inesperado", "Error inesperado al probar la conexion.", elapsed)
    elapsed = int((time.perf_counter() - started) * 1000)
    return DatabaseCheckResult(True, "ok", "Conexion correcta.", elapsed)


def require_connection(credentials: DatabaseCredentials, admin: bool = False) -> None:
    result = test_connection(credentials)
    if result.ok:
        return
    raise DatabaseSetupError(_friendly_connection_message(result, admin=admin))


def ensure_database(admin: AdminDatabaseCredentials, db_name: str, owner: str, owner_password: str) -> list[str]:
    if not validate_identifier(db_name):
        raise ValueError("El nombre de la base de datos no es un identificador SQL valido.")
    if not validate_identifier(owner):
        raise ValueError("El owner no es un identificador SQL valido.")

    admin_credentials = DatabaseCredentials(
        host=admin.host,
        port=admin.port,
        database=admin.database,
        user=admin.user,
        password=admin.password,
    )
    require_connection(admin_credentials, admin=True)

    messages: list[str] = []
    try:
        _ensure_owner_role(admin, owner, owner_password, messages)
        db_exists = _database_exists(admin, db_name)
        if db_exists:
            messages.append(f"La base de datos {db_name} ya existia.")
        else:
            _create_database(admin, db_name, owner)
            messages.append(f"La base de datos {db_name} fue creada correctamente.")
        _verify_database_owner(admin, db_name, owner, messages)
    except OperationalError as exc:
        log_database_exception("preparar_rol_base", exc)
        result = _connection_result_from_operational_error(exc, 0)
        raise DatabaseSetupError(_friendly_connection_message(result, admin=True)) from None
    except UnicodeDecodeError as exc:
        log_database_exception("preparar_rol_base", exc)
        raise DatabaseSetupError("No fue posible interpretar la respuesta de PostgreSQL.") from None
    return messages


def _ensure_owner_role(admin: AdminDatabaseCredentials, owner: str, owner_password: str, messages: list[str]) -> None:
    with psycopg2.connect(
        host=admin.host,
        port=admin.port,
        dbname=admin.database,
        user=admin.user,
        password=admin.password,
        connect_timeout=5,
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (owner,))
            role_exists = cursor.fetchone() is not None
            if role_exists:
                messages.append(f"El rol {owner} ya existia.")
                cursor.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(owner)),
                    (owner_password,),
                )
                messages.append("Contrasena del rol actualizada correctamente.")
            else:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(owner)),
                    (owner_password,),
                )
                messages.append(f"El rol {owner} se creo correctamente.")


def _database_exists(admin: AdminDatabaseCredentials, db_name: str) -> bool:
    with psycopg2.connect(
        host=admin.host,
        port=admin.port,
        dbname=admin.database,
        user=admin.user,
        password=admin.password,
        connect_timeout=5,
    ) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,))
            return cursor.fetchone() is not None


def _create_database(admin: AdminDatabaseCredentials, db_name: str, owner: str) -> None:
    conn = psycopg2.connect(
        host=admin.host,
        port=admin.port,
        dbname=admin.database,
        user=admin.user,
        password=admin.password,
        connect_timeout=5,
    )
    try:
        # PostgreSQL forbids CREATE DATABASE inside a transaction block, so this command uses
        # a dedicated administrative connection with autocommit enabled and no "with conn:" block.
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db_name), sql.Identifier(owner))
            )
    finally:
        conn.close()


def _verify_database_owner(admin: AdminDatabaseCredentials, db_name: str, owner: str, messages: list[str]) -> None:
    conn = psycopg2.connect(
        host=admin.host,
        port=admin.port,
        dbname=admin.database,
        user=admin.user,
        password=admin.password,
        connect_timeout=5,
    )
    try:
        conn.autocommit = True
        with conn.cursor() as cursor:
            cursor.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(db_name), sql.Identifier(owner))
            )
            cursor.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                    sql.Identifier(db_name), sql.Identifier(owner)
                )
            )
        messages.append("Propietario de la base de datos verificado.")
        messages.append("Permisos verificados para el owner.")
    finally:
        conn.close()


def _connection_result_from_operational_error(exc: OperationalError, elapsed_ms: int) -> DatabaseCheckResult:
    message = safe_exception_message(exc).lower()
    if "codificacion de windows" in message:
        return DatabaseCheckResult(
            False,
            "error_codificacion",
            "PostgreSQL devolvio un error, pero su mensaje no pudo interpretarse correctamente.",
            elapsed_ms,
        )
    if "password authentication failed" in message or "authentication failed" in message:
        return DatabaseCheckResult(False, "credenciales_invalidas", "Credenciales incorrectas.", elapsed_ms)
    if "does not exist" in message and "database" in message:
        return DatabaseCheckResult(False, "base_inexistente", "La base de datos no existe.", elapsed_ms)
    if "timeout expired" in message or "timed out" in message or "timeout" in message:
        return DatabaseCheckResult(False, "timeout", "La conexion supero el tiempo de espera.", elapsed_ms)
    if "connection refused" in message or "could not connect" in message or "no connection could be made" in message:
        return DatabaseCheckResult(
            False,
            "servidor_apagado",
            "PostgreSQL no responde en el host/puerto configurado.",
            elapsed_ms,
        )
    return DatabaseCheckResult(False, "error_operacional", "No se pudo conectar a PostgreSQL.", elapsed_ms)


def _friendly_connection_message(result: DatabaseCheckResult, admin: bool = False) -> str:
    if admin and result.status in {"credenciales_invalidas", "error_codificacion"}:
        return "No fue posible autenticarse como administrador PostgreSQL."
    if result.status == "credenciales_invalidas":
        return "No fue posible autenticarse con el usuario de la base de datos."
    if result.status == "base_inexistente":
        return "La base de datos indicada no existe."
    return result.message
