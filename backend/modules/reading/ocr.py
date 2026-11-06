"""Lectura de texto con Tesseract OCR (opcional).

Tesseract es un ejecutable externo. Se busca, en orden:
  1. `OI_TESSERACT_PATH` del .env (ruta a tesseract.exe o a su carpeta);
  2. la carpeta `tesseract` junto al programa instalado (componente opcional del instalador);
  3. el PATH del sistema y la ruta de instalacion habitual de Windows.
Solo se ejecuta cuando hay una zona de texto que leer: si nadie lo pide, no cuesta nada.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

LANGUAGES = ("spa+eng", "eng", "spa")
# Modos de pagina de Tesseract utiles para zonas recortadas: 7 = una linea, 6 = bloque, 11 = texto disperso.
PSM_CHOICES = (6, 7, 8, 11, 13)
_lock = threading.Lock()


def _candidates() -> list[Path]:
    paths: list[Path] = []
    raw = (os.getenv("OI_TESSERACT_PATH") or "").strip().strip('"')
    if raw:
        path = Path(raw)
        paths.append(path / "tesseract.exe" if path.is_dir() else path)
    app_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[3]
    paths += [app_dir / "tesseract" / "tesseract.exe", app_dir / "installer" / "vendor" / "tesseract_bundle" / "tesseract.exe"]
    found = shutil.which("tesseract")
    if found:
        paths.append(Path(found))
    for base in (os.getenv("ProgramFiles"), os.getenv("LOCALAPPDATA")):
        if base:
            paths.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
    return paths


@lru_cache(maxsize=1)
def locate() -> dict[str, Any]:
    """Ruta, version e idiomas de Tesseract (cacheado; `refresh()` lo vuelve a buscar)."""
    if (os.getenv("OI_TESSERACT_PATH") or "").strip().lower() == "off":
        return {"available": False, "path": None, "version": None, "languages": [],
                "note": "OCR desactivado en el .env (OI_TESSERACT_PATH=off)."}
    for path in _candidates():
        if not path.is_file():
            continue
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            version = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=10, creationflags=flags)
            langs = subprocess.run([str(path), "--list-langs"], capture_output=True, text=True, timeout=10, creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            continue
        first = (version.stdout or version.stderr).strip().splitlines()
        languages = [line.strip() for line in (langs.stdout or "").splitlines()[1:] if line.strip()]
        return {"available": True, "path": str(path), "version": first[0] if first else None, "languages": languages}
    return {"available": False, "path": None, "version": None, "languages": [],
            "note": "Tesseract no esta instalado: instale el componente OCR o indique OI_TESSERACT_PATH en el .env."}


def refresh() -> dict[str, Any]:
    locate.cache_clear()
    return locate()


def _prepare(image: np.ndarray) -> list[np.ndarray]:
    """Variantes para OCR: gris ampliado (letra de ~30 px), binarizado Otsu e invertido si el fondo es oscuro."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height = gray.shape[0]
    factor = float(np.clip(90.0 / max(height, 1), 1.0, 4.0))  # zonas bajas (una linea) se amplian
    if factor > 1.0:
        gray = cv2.resize(gray, None, fx=factor, fy=factor, interpolation=cv2.INTER_CUBIC)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _, binary = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(binary.mean()) < 127:  # texto claro sobre fondo oscuro
        binary = 255 - binary
    pad = lambda img: cv2.copyMakeBorder(img, 12, 12, 12, 12, cv2.BORDER_REPLICATE)
    return [pad(binary), pad(gray)]


def read_text(
    image: np.ndarray, languages: str = "spa+eng", psm: int = 7, timeout: float = 8.0, whitelist: str | None = None,
) -> dict[str, Any]:
    """Texto de una zona: {available, text, confidence (0-100), words}. Prueba variantes y se queda con la mejor."""
    info = locate()
    if not info["available"]:
        return {"available": False, "text": "", "confidence": 0.0, "words": [], "error": info["note"]}
    try:
        import pytesseract  # noqa: PLC0415
    except Exception:
        return {"available": False, "text": "", "confidence": 0.0, "words": [], "error": "Falta el paquete pytesseract."}
    pytesseract.pytesseract.tesseract_cmd = info["path"]
    config = f"--oem 1 --psm {int(psm)}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"
    best: dict[str, Any] = {"available": True, "text": "", "confidence": 0.0, "words": []}
    for variant in _prepare(image):
        try:
            with _lock:  # Tesseract usa varios hilos por si solo; en serie se evita saturar el equipo
                data = pytesseract.image_to_data(variant, lang=languages, config=config, timeout=timeout,
                                                 output_type=pytesseract.Output.DICT)
        except RuntimeError as error:  # tiempo agotado
            best.setdefault("error", f"Tiempo de lectura agotado ({timeout:.0f} s): {error}")
            continue
        except Exception as error:  # idioma no instalado, etc.
            best.setdefault("error", str(error)[:300])
            continue
        words = [
            {"text": text, "confidence": float(conf)}
            for text, conf in zip(data.get("text", []), data.get("conf", []))
            if str(text).strip() and float(conf) >= 0
        ]
        if not words:
            continue
        confidence = float(np.mean([w["confidence"] for w in words]))
        if confidence > best["confidence"]:
            best = {"available": True, "text": " ".join(w["text"] for w in words), "confidence": round(confidence, 1),
                    "words": words}
        if confidence >= 90:
            break
    best["text"] = re.sub(r"\s+", " ", best["text"]).strip()
    return best
