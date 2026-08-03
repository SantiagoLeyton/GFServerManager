# Gestion Fiduciaria Server Manager

Aplicacion de escritorio para Windows que instala, configura y administra el servidor local de Gestion Fiduciaria.

Version oficial: `1.0.0`.

## Funcionalidad

- Asistente de instalacion para validar el proyecto Django, configurar entorno virtual y ejecutar tareas iniciales.
- Panel de administracion para iniciar, detener y consultar el estado del servidor Waitress.
- Gestion basica de usuarios iniciales y usuarios administrativos del proyecto.
- Diagnosticos exportables de configuracion, entorno, base de datos y servicio.
- Integracion opcional con tarea de inicio de Windows.

## Requisitos

- Windows.
- Python compatible con el proyecto.
- PostgreSQL accesible desde el equipo donde se ejecute el servidor.
- Proyecto Django de Gestion Fiduciaria disponible localmente.
- Inno Setup 6 solo si se va a generar instalador.

## Ejecucion en Desarrollo

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

El punto de entrada de la aplicacion es `main.py`.

## Datos Persistentes

Los datos modificables no deben vivir en el repositorio. La aplicacion los guarda en:

```text
%PROGRAMDATA%\ConstructoraCentenario\GFServerManager
```

Alli se almacenan:

- `data\server_manager.json`
- `logs\server_manager.log`
- diagnosticos exportados

Si existen datos antiguos en `%LOCALAPPDATA%\ConstructoraCentenario\GFServerManager` o en `data\`/`logs\` dentro del repositorio, se migran de forma segura cuando no exista ya el archivo equivalente en ProgramData.

## Compilacion

Instale las dependencias de empaquetado en un entorno limpio:

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

Genere la distribucion onedir:

```powershell
.\build.ps1 -SkipInstaller
```

El ejecutable queda en:

```text
dist\GFServerManager\GFServerManager.exe
```

## Instalador

Para generar tambien el instalador, instale Inno Setup 6 y asegure que `ISCC.exe` este en el `PATH` o en su ruta predeterminada.

```powershell
.\build.ps1
```

El instalador queda en:

```text
installer-output\GFServerManager-Setup-1.0.0.exe
```

Si Inno Setup no esta disponible, el script genera el ejecutable y muestra el paso externo pendiente.

## Estructura del Proyecto

```text
app/                    Codigo fuente de la aplicacion Tkinter
assets/                 Recursos opcionales, como assets/app.ico
installer/              Script de instalacion Inno Setup
packaging/              Metadatos usados por PyInstaller
main.py                 Punto de entrada
build.ps1               Automatizacion de build
GFServerManager.spec    Configuracion de PyInstaller
```

## Preparacion para GitHub

No se deben versionar entornos virtuales, builds, instaladores generados, logs, configuraciones locales, bases de datos ni archivos `.env`. Esos elementos estan cubiertos por `.gitignore`.

Antes de publicar, valide el estado del repositorio:

```powershell
git status --short
python -m compileall main.py app
```

## Notas de Empaquetado

- PyInstaller usa `GFServerManager.spec`.
- La version del producto vive en `app\metadata.py`.
- El icono esperado es `assets\app.ico`; si no existe, la aplicacion compila sin icono personalizado.
- No se empaquetan `.env`, `server_manager.json`, logs, bases de datos, entornos virtuales ni el proyecto Django `PagosFiducia`.
