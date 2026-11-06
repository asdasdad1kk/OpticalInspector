# Comparador óptico general

Compara **cualquier pieza** contra su pieza patrón (golden) y explica en qué zonas es distinta. No sabe
nada de paneles, insertos ni delrones: es una comparación visual. Es independiente del módulo de paneles
(base de datos, archivos y API propios) y sirve de base para comparadores más específicos.

Menú **Comparador** (`/compare`).

## Uso (lo único que hace el operador)

1. **Guardar patrón** (una vez por pieza): tomar la foto de una pieza buena → recortar el área a comparar →
   escribir el nombre → *Guardar como patrón*. Si el nombre ya existe se guarda como una **revisión** nueva
   (la anterior se conserva y se puede eliminar).
2. **Inspeccionar**: tomar la foto → recortar el área → elegir la pieza (por nombre) → *Inspeccionar*.

**El patrón puede ser solo un recorte y la inspección puede usar la foto completa**: el sistema busca el patrón dentro de la foto (cualquier posición, giro o tamaño), lo alinea y analiza desde ahí; en «Ubicación en la foto» se ve dónde lo encontró. Recortar en la inspección es opcional (acelera y evita fondos parecidos).

No hay que alinear, calibrar ni marcar nada: la foto puede estar en otra posición, girada o a otra distancia,
y el recorte no tiene que ser idéntico (debe incluir toda el área del patrón).

El resultado muestra: veredicto (OK / REVISAR / FALLO), puntaje, resumen, imagen con las zonas numeradas
(también mapa de calor, patrón y pieza alineada) y, por cada discrepancia, el recorte patrón-vs-pieza, **dónde
está** (zona, % de ancho/alto, píxeles, % de la pieza), **qué es distinto** (color, brillo, falta/sobra detalle,
elemento desplazado, reflejo), cuánto y la **causa probable**. Todo queda en el historial de la pieza.
Si la foto no permite decidir (no se alinea, está borrosa, muy oscura, demasiado reflejo) el resultado es
**NO CONCLUYENTE** con el motivo, en lugar de un veredicto inventado.

## Cómo compara (`modules/compare/engine.py`)

1. **Búsqueda y alineación**: la foto se conserva grande (`SEARCH_SIDE`, 3200 px) para buscar el patrón; puntos SIFT con votación de giro/escala + RANSAC (similitud; homografía si hay muchos puntos) y afinado ECC sobre bordes.
   Cobertura mínima del patrón: `MIN_COVERAGE` (0.80).
2. **Luz**: se iguala el color global y se corrige un campo suave de brillo (celdas grandes con mediana, así un
   defecto chico no se «normaliza»).
3. **Tres mapas de diferencia**, tolerantes a un desplazamiento de `TOLERANCE_FRACTION` (0.35 % del lado mayor):
   color (ΔE en Lab), borde que sobra y borde que falta. El umbral se adapta al ruido de la propia foto
   (mediana + `NOISE_SIGMAS` × MAD) y nunca baja del mínimo de la sensibilidad elegida.
4. **Regiones**: se agrupan los píxeles distintos; cada una se mide (color medio de cada foto, ΔL, cambio de
   croma, energía de borde, desplazamiento local por correlación de fase) y se clasifica.
5. **Veredicto**: FALLO si hay una zona crítica o el área distinta supera `fail_pct`; REVISAR si hay cualquier
   zona; OK si no hay ninguna. Los reflejos (zonas muy claras en ambas fotos) solo avisan (severidad leve).
   Donde **ambas** fotos están quemadas no hay nada que comparar y se marca como no evaluable (azul claro);
   si es más del `MAX_EXCLUDED` (45 %) el resultado es NO CONCLUYENTE.

## Ajustes

| Qué | Dónde | Efecto |
|---|---|---|
| Sensibilidad por defecto: **estricto** | `PRESETS` / `DEFAULT_PRESET` en `engine.py` | `delta_e` (ΔE mínimo), `edge` (fuerza de borde mínima), `min_area_pct` (zona mínima, % de la pieza), `review_pct`, `fail_pct` |
| Ruido tolerado | `noise_sigmas` de cada preset (`NOISE_SIGMAS` = 6 por defecto) | más bajo = ve marcas más débiles (pero más falsos positivos si la foto es ruidosa) |
| Marcas finas e intensas | `STRONG_FACTOR` (2.5) | una marca ≥ 2.5 × el umbral cuenta aunque sea menor que `min_area_pct` |
| Desplazamiento tolerado | `TOLERANCE_FRACTION` | más alto = admite más movimiento entre fotos (y detecta menos desplazamientos pequeños) |
| Brillos quemados | `SATURATED_L` (247), `MAX_EXCLUDED` | qué se considera reflejo y cuánto puede haber |
| Calidad mínima de la foto | `limits` en `compare()` | nitidez, brillo, contraste, % saturado (también por `min_quality`) |
| Búsqueda del patrón en la foto | `SEARCH_SIDE`, `MIN_MATCHES` (14), `MIN_ECC` (0.30) | más puntos mínimos / más correlación = más estricto para aceptar que «ahí está la pieza»; si no la encuentra en fotos reales, baje `MIN_MATCHES` (ej. 10) |
| Tamaño de trabajo | `WORK_SIDE` (1400 px) | más alto = detecta cosas más finas, más lento |
| Textos de causas y tipos | `_explain`, `KIND_LABEL` | para afinar el lenguaje o agregar tipos |

## Si una marca fina no se detecta (p. ej. raya de pluma)

1. Mire el informe: **«Umbral dE» y «mayor diferencia de color encontrada»**. Si lo encontrado es menor que el umbral, la marca queda por debajo; y las que quedan entre 50 % y 100 % del umbral se listan como **marcas débiles (cuadros grises)**.
2. Cambie la sensibilidad a **Máximo** (ΔE ≥ 5, zonas desde 0.003 %, ruido 3.5σ). Es lo primero que hay que mover.
3. Si aun así no sale: baje `delta_e`/`edge` del preset `maximo` o su `noise_sigmas` (ej. 3.0), y reduzca `min_area_pct`. Si la foto es muy ruidosa, mejor mejorar la luz/enfoque que bajar umbrales.
4. El mapa de calor es una vista **sin umbral**: puede mostrar cosas claras que el umbral aún no considera diferencia.

La sensibilidad también se elige en pantalla (máximo / estricto / normal / tolerante) y se recuerda en el equipo.

## Datos

* BD propia: `OI_COMPARE_DB_PATH` (por defecto `optical_compare.db` en la carpeta de datos). Tablas `part`
  (patrones y sus revisiones) e `inspection` (informe completo en JSON). No usa ni crea nada en la BD de paneles.
* Archivos: `OI_COMPARE_DIR` (`storage/compare`): `golden/` (patrones) e `inspections/AAAA/MM/` (recorte de la
  pieza, alineada, mapas y un recorte por zona).
* El respaldo automático actual solo copia la BD de paneles; copie `optical_compare.db` y `storage/compare`
  si necesita respaldarlos.

## API (`/api/compare`)

`GET /options` · `GET/POST /parts` (multipart: `name`, `description`, `crop` JSON `{x,y,w,h}`, `photo`) ·
`GET /parts/{id}/image[?max_side=]` · `PATCH/DELETE /parts/{id}` · `POST /inspect` (`part_id` o `part_name`,
`sensitivity`, `crop`, `photo`) · `GET /inspections[?part=]` · `GET /inspections/{id}` ·
`GET /inspections/{id}/image/{annotated|heatmap|golden|aligned|test|region-N}` · `DELETE /inspections/{id}`.
`422` = NO CONCLUYENTE (el detalle trae el motivo).

## Hacerlo más específico

El motor devuelve regiones con medidas (`delta_e`, `delta_l`, `edge_*`, `shift_*`, colores BGR, `kind`). Un
comparador específico puede reutilizar `compare()` y: restringir el análisis a zonas con nombre (máscaras por
pieza), cambiar `_explain` por reglas del dominio (p. ej. «falta tornillo»), o fijar tolerancias por zona.

## Pruebas

```
cd backend
python tests/test_compare.py   # alta, inspección con defectos, revisión, errores/NO CONCLUYENTE, BD separada
```
