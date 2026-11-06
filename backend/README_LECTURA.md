# Lectura de códigos de barras, QR y texto (OCR)

Opcional y configurable. Funciona en el **modo de paneles** (Pieza real → Inspección) y en el **Comparador**.

| Qué lee | Motor | Instalación |
|---|---|---|
| Códigos 1D y 2D: QR, Micro QR, DataMatrix, Aztec, PDF417, Code128, Code39, Code93, EAN-13/8, UPC-A/E, ITF, Codabar, DataBar | **zxing-cpp** | Va siempre dentro del programa (pocos MB). Si faltara, se usa OpenCV, que solo lee QR y EAN/UPC. |
| Texto (número de parte, lote, serie) | **Tesseract 5.4** con modelos *best* de **español e inglés** | Componente del instalador **«Lectura de texto (OCR)»**, marcado por defecto (~160 MB). |

El OCR **solo se ejecuta cuando una pieza tiene zonas de texto**: si nadie lo usa, no consume tiempo.

## Uso

1. **Al dar de alta la pieza** (Pieza real, después de *Analizar*) o **al guardar un patrón** (Comparador): botón
   **«Zona de lectura»** → dibujar un rectángulo sobre el código, QR o texto. Se lee al momento y se propone el valor.
2. Para cada zona se elige el **tipo** (código o texto) y la **regla**:
   * **Igual a**: el valor debe ser exactamente ese (en texto no distingue mayúsculas ni espacios).
   * **Expresión regular**: p. ej. `^PN-[0-9]{4}-[A-Z]$` (sirve para seriales que cambian pieza a pieza).
   * **Que exista**: basta con que se pueda leer algo.
   * **Solo registrar**: se guarda lo leído, sin afectar el veredicto.
3. En cada inspección la foto se alinea con la pieza patrón (aunque esté movida o girada), cada zona se lee y su
   resultado aparece en el informe con lo leído, lo esperado y el estado.

## Configuración (Configuración → «Lectura (códigos / OCR)»)

* Activar/desactivar códigos y texto; tipos de código (comunes, solo 2D, solo lineales, todos).
* Idiomas del OCR (español + inglés, español o inglés), modo de página (PSM 7 = una línea, recomendado),
  confianza mínima (60 %) y tiempo máximo por zona (8 s).
* Qué pasa si **no se puede leer** (REVISAR por defecto) y si **se lee otro valor** (FALLO por defecto).
* Estado de los motores y botón **«Probar con una foto»**.
* `.env`: `OI_TESSERACT_PATH` = ruta a `tesseract.exe` (o su carpeta) para usar otra instalación; `off` lo desactiva.

## Calidad de la foto

* Códigos: al menos ~2 px por barra o módulo. Con la BRIO en 4K sobra; con 720p, las etiquetas pequeñas pueden no leerse
  (se informa «no se pudo leer», nunca se inventa un valor).
* Texto: letras de al menos ~20 px de alto. Las zonas chicas se amplían solas.
* Los reflejos sobre etiquetas plásticas son la causa típica de fallas: se prueban varios preprocesados.

## Instalador

`installer\build_installer.ps1` ejecuta `installer\prepare_tesseract.py`, que:

1. Descarga Tesseract 5.4.0.20240606 (UB-Mannheim) y `tessdata_best` eng/spa/osd, **verificando su SHA256**
   (caché en `installer\vendor\cache`, fuera de git).
2. Lo extrae con 7-Zip **sin ejecutarlo** y copia solo `tesseract.exe` y las DLL que realmente usa (sin herramientas de
   entrenamiento) más licencias (Apache-2.0).
3. Inno Setup lo instala en `{app}\tesseract` como componente **«ocr»**. La prueba automática del build verifica que el
   ejecutable empaquetado lea códigos con zxing-cpp y que el OCR responda con español e inglés.
   `-SkipOcr` genera un instalador sin el componente de texto.

Requisitos solo en la PC que compila: 7-Zip y `pefile` (se instala solo en el venv de compilación).

## Código

* `modules/reading/codes.py`: lectura de códigos (zxing-cpp/OpenCV, variantes y reintento girado).
* `modules/reading/ocr.py`: localización de Tesseract y OCR con preprocesado.
* `modules/reading/service.py`: zonas, reglas, ajustes y lectura a través de la alineación (`read_zones_mapped`).
* Paneles: `check_readings` en `modules/inspect/golden.py`. Comparador: `_apply_readings` en `modules/compare/service.py`.
* API: `GET /api/reading/capabilities`, `POST /api/reading/test`; zonas del comparador: `PUT /api/compare/parts/{id}/readings`.

## Pruebas

```
cd backend
python tests/test_reading.py   # códigos generados (giros, chicos), reglas, paneles; OCR se omite si no está instalado
python tests/test_compare.py   # incluye zonas de lectura en el comparador
```
