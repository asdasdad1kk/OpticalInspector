# Guía de ajuste: contorno, detección, alineación (rectificación) y veredicto

Todos los valores están en el código como constantes con nombre. Cambia uno, corre los tests
(`cd backend && python tests/<test>.py`) y mira el efecto. **Cambia una cosa a la vez.**

## 1. Flujo en una línea

`foto → detectar features (insertos/delrons/barrenos) → clasificar tipo → contorno (GrabCut + filtros)`
al dar de alta la pieza patrón; y al inspeccionar:
`calidad de imagen → registro (alinear con el patrón) → ¿cabe en el encuadre? → rectificar → verificar cada feature + contorno + distancias`.

Cualquier fallo en los pasos previos a «rectificar» da **NO CONCLUYENTE** (hay que repetir la foto);
los pasos posteriores dan OK / REVISAR / FALLO.

## 2. «NO CONCLUYENTE»: causas y qué tocar

| Mensaje | Causa | Dónde ajustarlo |
|---|---|---|
| Imagen muy oscura / sobreexpuesta / desenfocada / píxeles saturados | Compuertas de calidad | `DEFAULT_OPTIONS` en `modules/inspect/pipeline.py`: `min_brightness`, `max_brightness`, `min_sharpness`, `max_clipped_fraction` |
| No fue posible alinear la pieza | Menos de 4 features coinciden con el patrón | Ver §4 (registro) y mejorar enfoque/iluminación |
| Alineación poco confiable (X de Y) | Coinciden menos del `min_inlier_fraction` (0.6) | `min_inlier_fraction` (bajarlo = más permisivo; 0.5 mínimo recomendable) |
| La pieza no cabe completa en el encuadre | Una parte de la silueta o algún feature queda fuera de la foto | `min_visible_fraction` (0.97) y `frame_margin` (0.005) en `DEFAULT_OPTIONS` |

Antes se comparaba la **caja rectangular** de la pieza patrón con la foto, así que una pieza girada
daba «no cabe» aunque se viera entera. Ahora se mide la **silueta real**: la pieza puede estar en
cualquier posición o giro; solo importa que se vea completa.

## 3. Posición de la pieza (versatilidad)

* **Giro**: `MAX_ROTATION_DEG` en `modules/inspect/registration.py`. 180 = cualquier orientación
  (valor actual). Si en una pieza muy simétrica (dos pares de agujeros iguales en los extremos) el
  alineamiento se confunde, bájalo (p. ej. `35`) para que solo acepte giros pequeños o de 180°.
* **Tamaño/distancia**: `min_fill` / `max_fill` en `register()` (misma archivo): la pieza debe ocupar
  entre 25 % y 100 % del lado mayor de la foto.
* **Tolerancia de emparejado**: `tolerance = max(6 px, 1.4 % del ancho de la foto)` en `register()`.
  Más grande = admite piezas más deformadas/paralaje, pero acepta emparejamientos falsos.

## 4. Qué tan estricto es el veredicto (se mantiene estricto)

En `DEFAULT_OPTIONS` (`pipeline.py`), unidades en **radios del feature**:

* `position_ok_radii` 0.45 → hasta aquí OK; `position_review_radii` 0.90 → REVISAR; más → FALLO.
* `diameter_review_ratio`, `min_presence`: diámetro y presencia del feature.
* `dimension_tolerance_pct` 1.5 y `distance_tolerance_pct` 1.5: contorno y distancias de la pieza real.
* `brass_saturation`: umbral latón/acero.

Las opciones también pueden mandarse por inspección (`options`) sin editar código.

## 5. Contorno de la pieza (`modules/inspect/golden.py`)

El contorno sale de GrabCut sembrado con los propios features y se limpia en tres pasos:

1. **Recorte por color** (`_drop_off_color`): quita lo que se parece más a la mesa que a la placa
   (madera, mesa de color). Solo actúa si la mesa y la placa tienen croma distinto; en mesa gris
   sobre placa gris no hace nada.
   * `OFF_COLOR_SIGMAS` (3.0) y `OFF_COLOR_MIN_DISTANCE` (4.5): **bajarlos** recorta más
     agresivamente (si el contorno incluye mesa); **subirlos** recorta menos (si se come la placa).
2. **Afinado de borde** (`_refine_edges`): segundo GrabCut a mayor resolución solo en una banda
   alrededor del contorno aproximado; sigue pestañas, escalones y bordes descuadrados.
   * `REFINE_BAND` (0.025 del lado mayor): cuánto puede moverse el borde al dar de alta la pieza.
   * `INSPECT_BAND_RATIO` (0.04 del lado corto): lo mismo al inspeccionar (la pieza ya está alineada).
     Más grande = mide desviaciones mayores pero admite más ruido del fondo.
   * `REFINE_SPUR` (0.01): puntas/rebabas más angostas que esto se eliminan.
3. **Casco convexo**: `HULL_MIN_SOLIDITY` (0.985) solo tapa ruido en siluetas casi perfectamente
   convexas. Si lo bajas (0.88) también rellena pestañas y muescas reales: no lo hagas en piezas con ellas.
4. `segment_panel_from_features`: `grow(hull, …)` define cuánta zona «probable» tiene GrabCut; y el
   punto 1.8×radio de cada feature es placa segura.

Para probarlo visualmente corre el barrido (muestra el contorno y los features sobre cada foto):

```
cd backend
python tests/test_contours.py        # contornos esperados por foto (rectángulos)
python tests/test_feature_types.py   # tipos de feature por foto, a 1x/2x/3x
```

Si agregas una foto nueva: copia a `backend/tests/`, añade sus coordenadas en `test_feature_types.py`
(`CASES`) y su rectángulo en `test_contours.py` (`CASES`).

## 6. Clasificación del tipo de feature (`pipeline.py`, constantes arriba de `_radial_profile`)

| Constante | Qué hace |
|---|---|
| `CONE_SCORE_MIN` (54) | Saturación mínima del cono dorado. |
| `CONE_TAN_SAT_MIN` (28) | Cono «tostado» de cámaras de alta resolución (BRIO): saturación en 1.2–2.4 R. Un inserto es gris (≤ 17). **Si un cono sale inserto, bájalo; si un inserto sale cono, súbelo.** |
| `INSERT_DISK_RATIO_MAX` (0.79) | Disco gris más oscuro que la placa = inserto. |
| `BARRENO_RING_MAX` (0.07) | Barreno liso: sin otro borde a su alrededor. |
| `CONE_RAMP_MIN`, `CONE_MAX_INNER_STEP` | Cono gris sin color (embudo). |
| `LARGE_HOLE_MIN_PX` (45) | Desde este radio es un hueco grande. |

Detección de los features: `detect_features` y `_blob_shape` en `golden.py`.

## 7. Cámara BRIO (`modules/camera/service.py`)

`BRIO_MAX_FPS` (60), `BRIO_MAX_FPS_4K` (30), `BRIO_WARMUP_FRAMES` (12: cuadros descartados para que
asiente el autoenfoque), `BRIO_STILL_CANDIDATES` (4: se usa el más nítido de los últimos cuadros).

## 8. Tests (`cd backend`)

```
python tests/test_feature_types.py     python tests/test_contours.py
python tests/test_self_inspection.py   python tests/test_partial_inspection.py
python tests/test_position_freedom.py  # giros 25/-30/90/180° y pieza cortada = NO CONCLUYENTE
python tests/test_relief.py
```
