"""Genera installer/app.ico (mismo ojo verde de la bandeja del sistema)."""
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256  # se dibuja en grande y Pillow reduce a cada tamano del .ico


def draw() -> Image.Image:
    k = SIZE / 64
    image = Image.new("RGBA", (SIZE, SIZE), (15, 23, 42, 255))
    d = ImageDraw.Draw(image)
    d.ellipse([v * k for v in (5, 5, 59, 59)], fill=(15, 23, 42, 255), outline=(34, 197, 94, 255), width=int(5 * k))
    d.ellipse([v * k for v in (14, 21, 50, 43)], fill=(34, 197, 94, 255))
    d.ellipse([v * k for v in (25, 23, 39, 41)], fill=(15, 23, 42, 255))
    d.ellipse([v * k for v in (29, 26, 34, 31)], fill=(255, 255, 255, 255))
    return image


if __name__ == "__main__":
    target = Path(__file__).with_name("app.ico")
    draw().save(target, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Icono generado: {target}")
