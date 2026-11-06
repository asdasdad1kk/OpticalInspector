"""Lectura de codigos de barras y 2D.

Motor principal: zxing-cpp (Code128, Code39, Code93, EAN/UPC, ITF, Codabar, DataBar, QR, Micro QR, DataMatrix,
Aztec, PDF417...). Respaldo sin dependencias extra: OpenCV (solo QR y EAN/UPC). Todo se importa al usarlo.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

import cv2
import numpy as np

# Grupos de formatos que se pueden elegir en la configuracion. "comunes" es el valor por defecto.
FORMAT_GROUPS: dict[str, tuple[str, ...]] = {
    "comunes": (
        "QRCode", "MicroQRCode", "DataMatrix", "Aztec", "PDF417",
        "Code128", "Code39", "Code93", "EAN13", "EAN8", "UPCA", "UPCE", "ITF", "Codabar", "DataBar",
    ),
    "2d": ("QRCode", "MicroQRCode", "DataMatrix", "Aztec", "PDF417"),
    "lineales": ("Code128", "Code39", "Code93", "EAN13", "EAN8", "UPCA", "UPCE", "ITF", "Codabar", "DataBar"),
    "todos": (),
}


@lru_cache(maxsize=1)
def _zxing() -> Any | None:
    try:
        import zxingcpp  # noqa: PLC0415

        return zxingcpp
    except Exception:  # pragma: no cover - falta el paquete o su DLL
        return None


def engine_info() -> dict[str, Any]:
    zxing = _zxing()
    if zxing is not None:
        return {"available": True, "engine": "zxing-cpp", "version": getattr(zxing, "__version__", None)}
    return {"available": True, "engine": "opencv", "version": cv2.__version__,
            "note": "Sin zxing-cpp solo se leen QR y EAN/UPC."}


def _formats(zxing: Any, group: str) -> Any:
    names = FORMAT_GROUPS.get(group, FORMAT_GROUPS["comunes"])
    if not names:
        return getattr(zxing.BarcodeFormat, "AllReadable", None) or getattr(zxing.BarcodeFormat, "Any", None)
    combined = None
    for name in names:
        value = getattr(zxing.BarcodeFormat, name, None)
        if value is None:
            continue
        combined = value if combined is None else combined | value
    return combined


def _position(item: Any) -> list[list[float]]:
    try:
        p = item.position
        return [[float(c.x), float(c.y)] for c in (p.top_left, p.top_right, p.bottom_right, p.bottom_left)]
    except Exception:
        return []


def _prepare(image: np.ndarray) -> list[np.ndarray]:
    """Variantes de la imagen: original, ampliada (codigos chicos) y con contraste local (reflejos)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    variants = [gray]
    if max(gray.shape) < 900:
        variants.append(cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
    variants.append(cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gray))
    return variants


def read_codes(image: np.ndarray, group: str = "comunes") -> list[dict[str, Any]]:
    """Todos los codigos legibles de la imagen: [{format, text, polygon, valid}]. Sin duplicados."""
    found: dict[tuple[str, str], dict[str, Any]] = {}
    zxing = _zxing()
    for index, variant in enumerate(_prepare(image)):
        scale = variant.shape[1] / image.shape[1]
        results: list[dict[str, Any]] = []
        if zxing is not None:
            results = _read_once(zxing, variant, group)
        else:
            results = _read_opencv(variant)
        for result in results:
            if scale != 1.0:
                result["polygon"] = [[x / scale, y / scale] for x, y in result["polygon"]]
            found.setdefault((result["format"], result["text"]), result)
        if found and index == 0:
            break  # la imagen original ya dio resultado: no hace falta probar variantes
    if not found and zxing is not None:
        # Algunos lectores (PDF417, lineales) solo toleran giros pequenos o de 90 grados: se reintenta enderezando.
        for angle in ROTATION_RETRIES:
            rotated, inverse = _rotate(image, angle)
            for result in _read_once(zxing, rotated, group):
                points = np.float32(result["polygon"]).reshape(-1, 1, 2) if result["polygon"] else None
                if points is not None:
                    result["polygon"] = cv2.transform(points, inverse).reshape(-1, 2).tolist()
                found.setdefault((result["format"], result["text"]), result)
            if found:
                break
    return list(found.values())


# Giros a probar si no se encontro nada (los de 90/180 ya los prueba el lector).
ROTATION_RETRIES = (15, 30, 45, 60, 75)


def _rotate(image: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray]:
    """Imagen girada sin recortar y la matriz que devuelve sus puntos a la imagen original."""
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w, new_h = int(height * sin + width * cos), int(height * cos + width * sin)
    matrix[0, 2] += new_w / 2 - width / 2
    matrix[1, 2] += new_h / 2 - height / 2
    rotated = cv2.warpAffine(image, matrix, (new_w, new_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return rotated, cv2.invertAffineTransform(matrix)


def _read_once(zxing: Any, image: np.ndarray, group: str) -> list[dict[str, Any]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    kwargs: dict[str, Any] = {}
    formats = _formats(zxing, group)
    if formats is not None:
        kwargs["formats"] = formats
    try:
        return [
            {"format": str(item.format).split(".")[-1], "text": item.text, "polygon": _position(item)}
            for item in zxing.read_barcodes(gray, **kwargs) if getattr(item, "valid", True) and item.text
        ]
    except Exception:
        return []


def _read_opencv(gray: np.ndarray) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        detector = cv2.QRCodeDetectorAruco() if hasattr(cv2, "QRCodeDetectorAruco") else cv2.QRCodeDetector()
        ok, texts, points, _ = detector.detectAndDecodeMulti(gray)
        if ok:
            for text, quad in zip(texts, points if points is not None else []):
                if text:
                    results.append({"format": "QRCode", "text": text, "polygon": quad.tolist()})
    except cv2.error:
        pass
    try:
        if hasattr(cv2, "barcode"):
            ok, texts, types, points = cv2.barcode.BarcodeDetector().detectAndDecodeWithType(gray)
            if ok:
                for text, kind, quad in zip(texts, types, points):
                    if text:
                        results.append({"format": str(kind), "text": text, "polygon": quad.tolist()})
    except (cv2.error, AttributeError, ValueError):
        pass
    return results
