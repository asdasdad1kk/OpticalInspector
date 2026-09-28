# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
)


# ============================================================
# RUTAS
# ============================================================

BACKEND_DIR = Path(SPECPATH).resolve()

ENTRY_SCRIPT = BACKEND_DIR / "main.py"

FRONTEND_DIST = (
    BACKEND_DIR.parent
    / "frontend"
    / "dist"
)


# ============================================================
# VALIDACIONES
# ============================================================

if not ENTRY_SCRIPT.is_file():
    raise FileNotFoundError(
        f"main.py no encontrado:\n{ENTRY_SCRIPT}"
    )

if not (FRONTEND_DIST / "index.html").is_file():
    raise FileNotFoundError(
        f"Frontend build no encontrado:\n{FRONTEND_DIST}\n"
        f"Ejecuta npm run build primero."
    )


# ============================================================
# HELPER
# ============================================================

def collect_package(package_name):
    try:
        return collect_all(package_name)
    except Exception:
        try:
            return (
                collect_data_files(package_name),
                [],
                collect_submodules(package_name),
            )
        except Exception:
            return [], [], []


# ============================================================
# DATAS
# ============================================================

datas = [
    (
        str(FRONTEND_DIST),
        "frontend/dist",
    ),
]


# ============================================================
# BINARIOS
# ============================================================

binaries = []


# ============================================================
# HIDDEN IMPORTS
# ============================================================

hiddenimports = [

    # FastAPI
    "fastapi",
    "fastapi.responses",
    "fastapi.staticfiles",
    "fastapi.middleware.cors",

    # Starlette
    "starlette",
    "starlette.responses",
    "starlette.staticfiles",
    "starlette.websockets",
    "starlette.middleware",
    "starlette.middleware.cors",

    # Uploads
    "multipart",
    "multipart.multipart",
    "python_multipart",
    "python_multipart.multipart",

    # Uvicorn
    "uvicorn",
    "uvicorn.main",
    "uvicorn.server",
    "uvicorn.config",
    "uvicorn.logging",

    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",

    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",

    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",

    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",

    # H11
    "h11",

    # Websockets
    "websockets",
    "websockets.server",
    "websockets.client",
    "websockets.legacy",
    "websockets.legacy.server",
    "websockets.legacy.client",

    # Pydantic
    "pydantic",
    "pydantic_core",

    # SQL
    "sqlmodel",
    "sqlalchemy",
    "sqlalchemy.orm",
    "sqlalchemy.dialects.sqlite",

    "sqlite3",
    "_sqlite3",

    # Vision
    "cv2",
    "numpy",
    "ezdxf",
    "pyrealsense2",

    # PyStray
    "pystray",
    "pystray._win32",
    "pystray._util",
    "pystray._util.win32",

    # Pillow
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "PIL.ImageTk",
]
    

# ============================================================
# PAQUETES DINAMICOS
# ============================================================

PACKAGES = (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "sqlmodel",
    "sqlalchemy",
    "uvicorn",
    "websockets",
    "h11",
    "ezdxf",
    "pyrealsense2",
    "pystray",
    "PIL",
)

for package in PACKAGES:

    package_datas, package_binaries, package_hiddenimports = (
        collect_package(package)
    )

    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


# ============================================================
# DATOS OPENCV
# ============================================================

try:
    datas += collect_data_files("cv2")
except Exception:
    pass


# ============================================================
# ELIMINAR DUPLICADOS
# ============================================================

datas = list(
    dict.fromkeys(
        tuple(x)
        for x in datas
    )
)

binaries = list(
    dict.fromkeys(
        tuple(x)
        for x in binaries
    )
)

hiddenimports = list(
    dict.fromkeys(hiddenimports)
)


# ============================================================
# ANALYSIS
# ============================================================

a = Analysis(
    [str(ENTRY_SCRIPT)],

    pathex=[
        str(BACKEND_DIR),
    ],

    binaries=binaries,

    datas=datas,

    hiddenimports=hiddenimports,

    hookspath=[],

    hooksconfig={},

    runtime_hooks=[],

    excludes=[
        "tkinter",
        "matplotlib",
        "pytest",
        "IPython",
        "jupyter",
        "notebook",
    ],

    noarchive=False,

    optimize=1,
)


# ============================================================
# PYZ
# ============================================================

pyz = PYZ(
    a.pure,
)


# ============================================================
# EXE
# ============================================================

exe = EXE(
    pyz,

    a.scripts,

    a.binaries,

    a.datas,

    [],

    name="OpticalInspector",

    debug=False,

    bootloader_ignore_signals=False,

    strip=False,

    upx=False,

    upx_exclude=[],

    runtime_tmpdir=None,

    console=False,

    disable_windowed_traceback=True,

    argv_emulation=False,

    target_arch=None,

    codesign_identity=None,

    entitlements_file=None,
)