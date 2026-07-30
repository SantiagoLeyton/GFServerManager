# Gestion Fiduciaria Server Manager

Aplicacion de escritorio para Windows que guia la instalacion inicial de Gestion Fiduciaria en el servidor interno.

## Fase 1

Incluye solamente el asistente de instalacion inicial:

- valida que la carpeta seleccionada sea el proyecto Gestion Fiduciaria esperado;
- comprueba o crea la base de datos PostgreSQL sin eliminar roles ni bases;
- genera `.env` con las variables reales del proyecto Django;
- prepara `.venv`, instala dependencias, ejecuta migraciones y `collectstatic`;
- crea las cuentas iniciales de Contabilidad y Comercial usando `get_user_model()`;
- inicia Waitress en `0.0.0.0` y verifica `http://127.0.0.1:PUERTO/`;
- guarda configuracion no sensible en `data/server_manager.json`;
- escribe registros en `logs/server_manager.log` sin credenciales.

No incluye panel administrativo, instalador, empaquetado, autoarranque, firewall automatico, copias de seguridad ni actualizacion automatica.

## Uso

Instalar dependencias del Server Manager:

```powershell
python -m pip install -r requirements.txt
```

Ejecutar:

```powershell
python main.py
```

El puerto predeterminado es `8000`. Para acceso desde otros equipos, el Firewall de Windows debe permitir conexiones entrantes a ese puerto.

