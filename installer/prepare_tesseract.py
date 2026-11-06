"""Prepara la carpeta de Tesseract OCR que se incluye (opcionalmente) en el instalador.

Uso:  python installer/prepare_tesseract.py [--out installer/vendor/tesseract_bundle]

1. Descarga (con cache en installer/vendor/cache) la version FIJADA de Tesseract para Windows (build de
   UB-Mannheim) y los modelos `tessdata_best` de ingles y espanol, y verifica su SHA256.
2. Extrae el instalador NSIS con 7-Zip (no lo ejecuta).
3. Copia solo tesseract.exe y las DLL que realmente importa (leyendo sus tablas de importacion con `pefile`,
   sin ejecutar nada): las herramientas de entrenamiento y sus dependencias (cairo, pango...) no se incluyen.
4. Agrega licencias y un VERSION.txt.

Requisitos solo en la PC que genera el instalador: 7-Zip y `pip install pefile`.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "vendor" / "cache"

TESSERACT_VERSION = "5.4.0.20240606"
DOWNLOADS = {
    "tesseract-setup.exe": (
        f"https://github.com/UB-Mannheim/tesseract/releases/download/v{TESSERACT_VERSION}/"
        f"tesseract-ocr-w64-setup-{TESSERACT_VERSION}.exe",
        "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9",
    ),
    # tessdata_best: modelos LSTM de mayor precision (mas lentos que "fast"; solo se usan si hay zonas de texto).
    "eng.traineddata": (
        "https://github.com/tesseract-ocr/tessdata_best/raw/main/eng.traineddata",
        "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba",
    ),
    "spa.traineddata": (
        "https://github.com/tesseract-ocr/tessdata_best/raw/main/spa.traineddata",
        "e2c1ffdad8b30f26c45d4017a9183d3a7f9aa69e59918be4f88b126fac99ab2c",
    ),
    "osd.traineddata": (
        "https://github.com/tesseract-ocr/tessdata_best/raw/main/osd.traineddata",
        "9cf5d576fcc47564f11265841e5ca839001e7e6f38ff7f7aacf46d15a96b00ff",
    ),
}
# DLL del sistema: no se copian (las trae Windows).
SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll", "ole32.dll", "oleaut32.dll",
    "ws2_32.dll", "msvcrt.dll", "ucrtbase.dll", "bcrypt.dll", "crypt32.dll", "secur32.dll", "shlwapi.dll",
    "comdlg32.dll", "winmm.dll", "version.dll", "iphlpapi.dll", "dnsapi.dll", "normaliz.dll", "wldap32.dll",
    "rpcrt4.dll", "userenv.dll", "ntdll.dll", "dwrite.dll", "usp10.dll", "msimg32.dll", "comctl32.dll",
    "setupapi.dll", "cfgmgr32.dll", "imm32.dll", "mpr.dll", "netapi32.dll", "psapi.dll", "dbghelp.dll",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name: str) -> Path:
    url, expected = DOWNLOADS[name]
    target = CACHE / name
    if target.exists() and sha256(target) == expected:
        return target
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"  descargando {name} ...")
    temporary = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as response, temporary.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    actual = sha256(temporary)
    if actual != expected:
        temporary.unlink(missing_ok=True)
        raise SystemExit(f"SHA256 de {name} no coincide (esperado {expected}, obtenido {actual}).")
    temporary.replace(target)
    return target


def find_7zip() -> str:
    for candidate in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    raise SystemExit("Se necesita 7-Zip para extraer Tesseract (https://www.7-zip.org).")


def dependencies(binary: Path, available: dict[str, Path]) -> set[str]:
    """Cierre de DLL importadas (solo las que vienen en el paquete de Tesseract)."""
    import pefile

    needed: set[str] = set()
    pending = [binary]
    while pending:
        current = pending.pop()
        pe = pefile.PE(str(current), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        entries = list(getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])) + list(getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []))
        for entry in entries:
            name = entry.dll.decode("ascii", "ignore").lower()
            if name in SYSTEM_DLLS or name.startswith(("api-ms-win-", "ext-ms-")) or name in needed:
                continue
            if name in available:
                needed.add(name)
                pending.append(available[name])
        pe.close()
    return needed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "vendor" / "tesseract_bundle"))
    out = Path(parser.parse_args().out)

    print(f"Tesseract {TESSERACT_VERSION} (modelos best: eng, spa)")
    setup = fetch("tesseract-setup.exe")
    models = [fetch(name) for name in ("eng.traineddata", "spa.traineddata", "osd.traineddata")]

    extracted = CACHE / f"tesseract-{TESSERACT_VERSION}"
    if not (extracted / "tesseract.exe").exists():
        shutil.rmtree(extracted, ignore_errors=True)
        subprocess.run([find_7zip(), "x", "-y", f"-o{extracted}", str(setup)], check=True, stdout=subprocess.DEVNULL)

    available = {path.name.lower(): path for path in extracted.glob("*.dll")}
    needed = dependencies(extracted / "tesseract.exe", available)

    shutil.rmtree(out, ignore_errors=True)
    (out / "tessdata").mkdir(parents=True)
    shutil.copy2(extracted / "tesseract.exe", out)
    for name in sorted(needed):
        shutil.copy2(available[name], out)
    if (extracted / "tessdata" / "configs").is_dir():
        shutil.copytree(extracted / "tessdata" / "configs", out / "tessdata" / "configs")
    for model in models:
        shutil.copy2(model, out / "tessdata" / model.name)
    licenses = out / "licenses"
    licenses.mkdir()
    for candidate in ("doc/LICENSE", "doc/AUTHORS", "LICENSE"):
        source = extracted / candidate
        if source.exists():
            shutil.copy2(source, licenses / source.name)
    (out / "VERSION.txt").write_text(
        f"Tesseract OCR {TESSERACT_VERSION} (UB-Mannheim build)\nModelos: tessdata_best eng, spa, osd\n"
        "Licencia: Apache-2.0 (ver licenses/)\n", encoding="utf-8",
    )
    size = sum(path.stat().st_size for path in out.rglob("*") if path.is_file()) / 1e6
    print(f"  listo: {out} ({len(needed)} DLL, {size:.0f} MB)")


if __name__ == "__main__":
    sys.exit(main())
