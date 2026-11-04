"""Levanta el backend con una RealSense simulada y datos temporales (solo para pruebas de interfaz).

Uso (desde backend/):  python tests/run_fake_backend.py [carpeta_de_datos] [puerto]
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
data_dir = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="oi_fake_")
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8010
os.environ["OI_DATA_DIR"] = data_dir
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tests"))

import fake_realsense as fake  # noqa: E402
import uvicorn  # noqa: E402

from modules.camera import service as camera_module  # noqa: E402

camera_module.rs = fake  # la "camara" es la simulada: color + profundidad sinteticas

if __name__ == "__main__":
    print(f"Backend simulado en http://127.0.0.1:{port} (datos en {data_dir})", flush=True)
    uvicorn.run("main:app", host="127.0.0.1", port=port, log_level="warning")
