# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Optical Inspector.

Expected structure:

project/
|-- main.spec
|-- app/
|   |-- main.py
|   |-- database/
|   |-- models/
|   `-- modules/
`-- frontend/
    `-- dist/
        |-- index.html
        `-- assets/

Build from the project root:
    pyinstaller --clean --noconfirm main.spec
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules


PROJECT_ROOT = Path(SPECPATH).resolve()
ENTRY_SCRIPT = PROJECT_ROOT / "main.py"
FRONTEND_DIST = PROJECT_ROOT.parent / "frontend" / "dist"

if not ENTRY_SCRIPT.exists():
    raise FileNotFoundError(
        f"Entry script not found: {ENTRY_SCRIPT}\n"
        "Move main.spec to the project root or update ENTRY_SCRIPT."
    )

if not FRONTEND_DIST.exists():
    raise FileNotFoundError(
        f"Frontend build not found: {FRONTEND_DIST}\n"
        "Run 'npm run build' inside frontend before PyInstaller."
    )


def merge_collection(package_name):
    """Collect package data, binaries, and hidden imports safely."""
    try:
        return collect_all(package_name)
    except Exception:
        return [], [], collect_submodules(package_name)


datas = [
    # Inside the executable bundle the React build is available as frontend/dist.
    (str(FRONTEND_DIST), "frontend/dist"),
]

binaries = []
hiddenimports = [
    # FastAPI / Starlette uploads, forms, WebSockets and static files.
    "multipart",
    "python_multipart",
    "starlette.staticfiles",
    "starlette.responses",
    "starlette.websockets",
    "websockets",
    "websockets.legacy",
    "websockets.legacy.server",
    "websockets.server",

    # Uvicorn runtime selected dynamically.
    "uvicorn",
    "uvicorn.config",
    "uvicorn.main",
    "uvicorn.server",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",

    # Database stack.
    "sqlmodel",
    "sqlalchemy",
    "sqlalchemy.dialects.sqlite",
    "sqlite3",

    # Computer vision / DXF.
    "cv2",
    "numpy",
    "ezdxf",
    "pyrealsense2",
]

# Packages that commonly load modules, data files, DLLs, or plugins dynamically.
for package in (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "sqlmodel",
    "sqlalchemy",
    "uvicorn",
    "websockets",
    "ezdxf",
    "pyrealsense2",
):
    package_datas, package_binaries, package_hiddenimports = merge_collection(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

# OpenCV data such as Haar cascades. The cv2 extension itself is normally handled
# by PyInstaller's built-in hook, so only its data files are explicitly added here.
try:
    datas += collect_data_files("cv2")
except Exception:
    pass

# Remove duplicates while preserving order.
datas = list(dict.fromkeys(tuple(item) for item in datas))
binaries = list(dict.fromkeys(tuple(item) for item in binaries))
hiddenimports = list(dict.fromkeys(hiddenimports))


a = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(PROJECT_ROOT)],
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
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
