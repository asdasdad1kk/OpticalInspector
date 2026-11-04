from __future__ import annotations

from contextlib import asynccontextmanager
from multiprocessing import freeze_support
from pathlib import Path
from threading import Thread
from time import sleep

import os
import socket
import sys
import webbrowser

import pystray
import uvicorn

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw

from core import config as app_config
from core.db import create_database
from core.runtime import setup_logging, start_backup_scheduler, stop_backup_scheduler

from modules.camera.router import router as camera_router
from modules.camera.service import camera_service
from modules.database.router import router as database_router
from modules.config.router import router as config_router
from modules.inspect.router import router as inspect_router
from modules.reference.router import router as reference_router
from modules.upload.router import router as upload_router
import os
import sys

if getattr(sys, "frozen", False):

    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")

    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

# ============================================================
# CONFIGURACION
# ============================================================

APP_NAME = "Optical Inspector"
APP_HOST = app_config.HOST          # OI_HOST en el .env
APP_PORT = app_config.PORT          # OI_PORT en el .env

# Una IP 0.0.0.0 no sirve para abrir el navegador local.
BROWSER_HOST = "127.0.0.1" if APP_HOST in ("0.0.0.0", "::") else APP_HOST
APP_HOME_URL = f"http://{BROWSER_HOST}:{APP_PORT}/inspect"


# ============================================================
# RUTAS
#
# Desarrollo:
# OPTICAL INSPECTOR WEB/
# ├── backend/
# │   └── main.py
# └── frontend/
#     └── dist/
#
# PyInstaller onefile:
# sys._MEIPASS/
# └── frontend/
#     └── dist/
# ============================================================

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys._MEIPASS).resolve()
    EXECUTABLE_DIR = Path(sys.executable).resolve().parent
else:
    BACKEND_DIR = Path(__file__).resolve().parent
    BUNDLE_DIR = BACKEND_DIR.parent
    EXECUTABLE_DIR = BACKEND_DIR


FRONTEND_DIST = BUNDLE_DIR / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
FRONTEND_ASSETS = FRONTEND_DIST / "assets"


# ============================================================
# ESTADO GLOBAL
# ============================================================

server: uvicorn.Server | None = None
server_thread: Thread | None = None
tray_icon: pystray.Icon | None = None
closing = False


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    create_database()
    start_backup_scheduler()

    yield

    stop_backup_scheduler()
    try:
        camera_service.stop()
    except Exception:
        pass


# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="Optical Inspector API",
    lifespan=lifespan,
)


# ============================================================
# CORS
#
# En el ejecutable, React y FastAPI usan el mismo origen.
# Se conserva para permitir desarrollo con Vite.
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ROUTERS
#
# Se registran antes del fallback de React.
# ============================================================

app.include_router(
    upload_router,
    prefix="/api",
)

app.include_router(
    inspect_router,
    prefix="/api",
)

app.include_router(
    reference_router,
    prefix="/api",
)

app.include_router(
    config_router,
    prefix="/api",
)

app.include_router(
    camera_router,
    prefix="/api",
)

app.include_router(
    database_router,
    prefix="/api",
)


# ============================================================
# ARCHIVOS ESTATICOS DE VITE
# ============================================================

if FRONTEND_ASSETS.is_dir():
    app.mount(
        "/assets",
        StaticFiles(
            directory=str(FRONTEND_ASSETS),
        ),
        name="frontend-assets",
    )


# ============================================================
# RESPUESTA DE FRONTEND NO ENCONTRADO
# ============================================================

def frontend_not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "message": "No se encontró el build del frontend.",
            "expected_path": str(FRONTEND_INDEX),
            "solution": (
                "Ejecuta npm run build dentro de la carpeta frontend "
                "y vuelve a generar el ejecutable."
            ),
        },
    )


# ============================================================
# FRONTEND PRINCIPAL
# ============================================================

@app.get(
    "/",
    include_in_schema=False,
)
def serve_frontend_root():
    if not FRONTEND_INDEX.is_file():
        return frontend_not_found()

    return FileResponse(
        path=str(FRONTEND_INDEX),
    )


# ============================================================
# FALLBACK DE REACT ROUTER
# ============================================================

@app.get(
    "/{full_path:path}",
    include_in_schema=False,
)
def serve_frontend_routes(full_path: str):
    if not FRONTEND_INDEX.is_file():
        return frontend_not_found()

    frontend_root = FRONTEND_DIST.resolve()
    requested_file = (FRONTEND_DIST / full_path).resolve()

    try:
        requested_file.relative_to(frontend_root)
    except ValueError:
        return FileResponse(
            path=str(FRONTEND_INDEX),
        )

    if requested_file.is_file():
        return FileResponse(
            path=str(requested_file),
        )

    return FileResponse(
        path=str(FRONTEND_INDEX),
    )


# ============================================================
# NAVEGADOR
# ============================================================

def open_application(
    icon: pystray.Icon | None = None,
    item: pystray.MenuItem | None = None,
) -> None:
    if os.getenv("OI_NO_BROWSER"):  # pruebas automatizadas / servicio
        return

    webbrowser.open(
        APP_HOME_URL,
        new=2,
    )


# ============================================================
# ICONO DE LA BANDEJA
#
# Se genera con Pillow para no depender de un archivo .ico.
# ============================================================

def create_tray_image() -> Image.Image:
    size = 64

    image = Image.new(
        mode="RGBA",
        size=(size, size),
        color=(15, 23, 42, 255),
    )

    draw = ImageDraw.Draw(image)

    # Círculo exterior verde.
    draw.ellipse(
        (5, 5, 59, 59),
        fill=(15, 23, 42, 255),
        outline=(34, 197, 94, 255),
        width=5,
    )

    # Forma de ojo.
    draw.ellipse(
        (14, 21, 50, 43),
        fill=(34, 197, 94, 255),
    )

    # Pupila.
    draw.ellipse(
        (25, 23, 39, 41),
        fill=(15, 23, 42, 255),
    )

    # Reflejo.
    draw.ellipse(
        (29, 26, 34, 31),
        fill=(255, 255, 255, 255),
    )

    return image


# ============================================================
# UVICORN
# ============================================================

def run_server() -> None:
    global server

    config = uvicorn.Config(
    app=app,
    host=APP_HOST,
    port=APP_PORT,
    ws="websockets",

    log_level="critical",
    access_log=False,

    use_colors=False,
)

    server = uvicorn.Server(config)

    # Evita que Uvicorn registre señales desde un hilo secundario.
    server.install_signal_handlers = lambda: None

    server.run()


def is_port_available() -> bool:
    with socket.socket(
        socket.AF_INET,
        socket.SOCK_STREAM,
    ) as client:
        client.settimeout(0.25)

        return client.connect_ex(
            (BROWSER_HOST, APP_PORT)
        ) != 0


def wait_for_server() -> None:
    for _ in range(100):
        if closing:
            return

        with socket.socket(
            socket.AF_INET,
            socket.SOCK_STREAM,
        ) as client:
            client.settimeout(0.1)

            if client.connect_ex(
                (BROWSER_HOST, APP_PORT)
            ) == 0:
                open_application()
                return

        sleep(0.1)


def start_server() -> bool:
    global server_thread

    if not is_port_available():
        # Si otra instancia ya está ejecutándose, solamente abre
        # la aplicación existente.
        open_application()
        return False

    server_thread = Thread(
        target=run_server,
        name="OpticalInspectorServer",
        daemon=True,
    )

    server_thread.start()

    browser_thread = Thread(
        target=wait_for_server,
        name="OpticalInspectorBrowser",
        daemon=True,
    )

    browser_thread.start()

    return True


# ============================================================
# CERRAR APLICACION
# ============================================================

def shutdown_application(
    icon: pystray.Icon | None = None,
    item: pystray.MenuItem | None = None,
) -> None:
    global closing

    if closing:
        return

    closing = True

    # Detiene primero el icono para responder inmediatamente
    # al clic del usuario.
    if icon is not None:
        try:
            icon.stop()
        except Exception:
            pass

    def stop_process() -> None:
        global server

        try:
            camera_service.stop()
        except Exception:
            pass

        if server is not None:
            server.should_exit = True

        if (
            server_thread is not None
            and server_thread.is_alive()
        ):
            server_thread.join(timeout=5)

        # Garantiza que no permanezcan hilos de cámara,
        # RealSense o WebSocket ejecutándose.
        os._exit(0)

    Thread(
        target=stop_process,
        name="OpticalInspectorShutdown",
        daemon=True,
    ).start()


# ============================================================
# BANDEJA DEL SISTEMA
# ============================================================

def create_tray_icon():
    menu = pystray.Menu(
        pystray.MenuItem(
            "Abrir",
            open_application,
            default=True,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Cerrar",
            shutdown_application,
        ),
    )

    return pystray.Icon(
        name="OpticalInspector",
        icon=create_tray_image(),
        title=APP_NAME,
        menu=menu,
    )


# ============================================================
# EJECUCION
# ============================================================

def main() -> None:
    global tray_icon

    freeze_support()

    started = start_server()

    # Si ya existe una instancia usando el puerto, se abre esa
    # instancia y este segundo proceso termina.
    if not started:
        return

    tray_icon = create_tray_icon()

    try:
        tray_icon.run()
    finally:
        if not closing:
            shutdown_application(
                tray_icon,
                None,
            )


if __name__ == "__main__":
    main()
