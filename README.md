# Gestion Fiduciaria Server Manager

Aplicacion de escritorio para Windows que instala y administra localmente el servidor de Gestion Fiduciaria.

Version oficial: `1.0.0`.

## Ejecucion en desarrollo

```powershell
python -m pip install -r requirements.txt
python main.py
```

El punto de entrada unico es `main.py`.

## Datos persistentes

La aplicacion guarda datos modificables fuera del directorio de instalacion:

```text
%PROGRAMDATA%\ConstructoraCentenario\GFServerManager
```

Alli se almacenan:

- `data\server_manager.json`
- `logs\server_manager.log`
- diagnosticos exportados

Si existen datos antiguos en `%LOCALAPPDATA%\ConstructoraCentenario\GFServerManager` o en `data\`/`logs\` dentro del repositorio, se migran de forma segura cuando no exista ya el archivo equivalente en ProgramData.

## Compilacion

Requisitos del equipo de compilacion:

- Windows
- Python compatible con el proyecto
- Acceso a internet o cache local de paquetes pip

Generar la distribucion onedir:

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

## Actualizacion

Para actualizar una instalacion existente:

1. Cierre GFServerManager.
2. Ejecute el nuevo instalador.
3. Instale sobre la misma ruta.

El instalador no elimina la configuracion ni los logs ubicados en `%PROGRAMDATA%\ConstructoraCentenario\GFServerManager`.

## Notas de empaquetado

- PyInstaller usa `GFServerManager.spec`.
- La version del producto vive en `app\metadata.py`.
- El icono esperado es `assets\app.ico`; si no existe, la aplicacion compila sin inventar recursos graficos.
- No se empaquetan `.env`, `server_manager.json`, logs, bases de datos, `.venv` ni el proyecto Django `PagosFiducia`.
