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

No incluye instalador, empaquetado, autoarranque, firewall automatico, copias de seguridad ni actualizacion automatica.

## Fase 2

Cuando existe una instalacion valida en `data/server_manager.json`, la aplicacion abre directamente el panel de administracion diario.

El panel incluye:

- Inicio con estado general y acciones rapidas.
- Servidor con PID, puerto, tiempo de ejecucion e inicio/detencion/reinicio de Waitress.
- Base de datos en modo consulta y prueba de conexion.
- Usuarios con lectura desde Django, creacion, cambio de contrasena y activacion/inactivacion.
- Configuracion local con edicion limitada de host y puerto.
- Logs con lectura automatica de `logs/server_manager.log`.

Cerrar la ventana no detiene Waitress automaticamente. Si el servidor esta activo, se pregunta si se desea mantenerlo en segundo plano, detenerlo o cancelar la salida.

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
