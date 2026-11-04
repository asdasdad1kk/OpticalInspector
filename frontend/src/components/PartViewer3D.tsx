import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useLoader } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { Box, Eye, Gauge, Layers, Palette, Ruler } from "lucide-react";

import { FEATURE_TYPES, typeLabel } from "./FeatureInfo";
import type { Relief, ReliefGrid, ReliefMeasures } from "../types/api";

/** Datos para reconstruir la pieza. Coordenadas con Y hacia arriba, en `unit`. */
export interface Model3D {
  unit: string;
  contour: [number, number][];
  features: {
    id: number | string;
    type: string;
    x: number;
    y: number;
    radius: number;
    outer_radius?: number;
    bore_radius?: number;
    height_mm?: number;
    height_reliable?: boolean;
    enabled?: boolean;
  }[];
  /** Foto de la pieza: px = (x / scale, -y / scale). Sin ella la placa se ve lisa. */
  texture?: { url: string; width: number; height: number; scale: number } | null;
  /** Relieve medido con la profundidad de la RealSense (opcional). */
  relief?: Relief | null;
}

interface Props {
  model: Model3D;
  className?: string;
  /** Llena el contenedor padre (que debe ser `relative`) en vez de usar `className` para el alto. */
  fill?: boolean;
  onSelectFeature?: (id: number | string | null) => void;
}

type Feature = Model3D["features"][number];
type Texture = NonNullable<Model3D["texture"]>;
type ReliefOn = Extract<Relief, { available: true }>;

// Alturas de cabeza (en radios) cuando no hay medicion: una sola foto no contiene profundidad real.
const HEAD_HEIGHT: Record<string, number> = { INSERTO: 0.05, INSERTO_LATON: 0.05, DELRON_CONICO: 0.8, DELRON_PLANO: 0.3 };

const REAL_COLORS: Record<string, string> = {
  INSERTO: "#9aa0a8",
  INSERTO_LATON: "#c9a24a",
  DELRON_CONICO: "#d6b36a",
  DELRON_PLANO: "#e6dcc2",
};

const outerOf = (feature: Feature) => feature.outer_radius ?? feature.radius;

function boreOf(feature: Feature) {
  if (feature.type === "BARRENO") return feature.radius;
  return Math.min(feature.bore_radius ?? feature.radius * 0.35, outerOf(feature) * 0.85);
}

/** Altura de la cabeza sobre la placa: la medida con profundidad si es confiable, si no la estimada. */
function headOf(feature: Feature) {
  const outer = outerOf(feature);
  if (feature.height_mm !== undefined && feature.height_reliable !== false) {
    return Math.max(feature.height_mm, 0.03 * outer);
  }
  return (HEAD_HEIGHT[feature.type] ?? 0.1) * outer;
}

function headProfile(feature: Feature, t: number): THREE.Vector2[] {
  const r = outerOf(feature);
  const b = boreOf(feature);
  const h = headOf(feature);
  const v = (x: number, z: number) => new THREE.Vector2(x, z);
  if (feature.type === "DELRON_CONICO") {
    const topRadius = b + 0.35 * (r - b);
    return [v(b, 0), v(0.75 * r, 0), v(0.75 * r, t), v(r, t), v(topRadius, t + h), v(b, t + h), v(b, 0)];
  }
  if (feature.type === "DELRON_PLANO") {
    return [v(b, 0), v(0.75 * r, 0), v(0.75 * r, t), v(r, t), v(r, t + h), v(b, t + h), v(b, 0)];
  }
  return [v(b, 0), v(r, 0), v(r, t + h), v(b, t + h), v(b, 0)];
}

function holesOf(model: Model3D): { x: number; y: number; r: number }[] {
  return model.features
    .filter((feature) => feature.enabled !== false && boreOf(feature) > 0)
    .map((feature) => ({ x: feature.x, y: feature.y, r: boreOf(feature) }));
}

function buildShape(model: Model3D) {
  const shape = new THREE.Shape(model.contour.map(([x, y]) => new THREE.Vector2(x, y)));
  for (const hole of holesOf(model)) {
    const path = new THREE.Path();
    path.absarc(hole.x, hole.y, hole.r, 0, Math.PI * 2, true);
    shape.holes.push(path);
  }
  return shape;
}

/** Cara superior plana con la foto: UV = posición del vértice en la imagen original. */
function topGeometry(shape: THREE.Shape, texture: Texture) {
  const geometry = new THREE.ShapeGeometry(shape, 32);
  const position = geometry.getAttribute("position");
  const uv = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i++) {
    uv[i * 2] = position.getX(i) / texture.scale / texture.width;
    uv[i * 2 + 1] = 1 - -position.getY(i) / texture.scale / texture.height;
  }
  geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return geometry;
}

// ---------------------------------------------------------------------------
// Relieve medido
// ---------------------------------------------------------------------------

function decodeGrid(grid: ReliefGrid): Float32Array {
  const binary = atob(grid.data_b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const raw = new Int16Array(bytes.buffer);
  const heights = new Float32Array(raw.length);
  for (let i = 0; i < raw.length; i++) heights[i] = raw[i] * grid.quant_mm;
  return heights;
}

/** Máscara de la silueta (con huecos) para recortar la malla rectangular con precisión de píxel. */
function buildMask(model: Model3D, bounds: ReliefGrid["bounds"]) {
  const width = bounds.x1 - bounds.x0;
  const height = bounds.y1 - bounds.y0;
  const pixels = 2048;
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width >= height ? pixels : (pixels * width) / height);
  canvas.height = Math.round(width >= height ? (pixels * height) / width : pixels);
  const context = canvas.getContext("2d")!;
  const px = (x: number) => ((x - bounds.x0) / width) * canvas.width;
  const py = (y: number) => ((bounds.y1 - y) / height) * canvas.height;
  context.fillStyle = "#000";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#fff";
  context.beginPath();
  model.contour.forEach(([x, y], index) => (index ? context.lineTo(px(x), py(y)) : context.moveTo(px(x), py(y))));
  context.closePath();
  for (const hole of holesOf(model)) {
    context.moveTo(px(hole.x + hole.r), py(hole.y));
    context.arc(px(hole.x), py(hole.y), (hole.r / width) * canvas.width, 0, Math.PI * 2, true);
  }
  context.fill("evenodd");
  const texture = new THREE.CanvasTexture(canvas);
  texture.channel = 1; // usa el segundo juego de UV (0..1 sobre la malla)
  texture.colorSpace = THREE.NoColorSpace;
  return texture;
}

function heatColor(value: number, range: number, target: THREE.Color) {
  // Azul (hundido) - verde claro (plano) - rojo (alto)
  const t = Math.max(-1, Math.min(1, value / range));
  if (t < 0) target.setRGB(0.2 + 0.6 * (1 + t), 0.45 + 0.4 * (1 + t), 1.0);
  else target.setRGB(0.8 + 0.2 * t, 0.85 - 0.6 * t, 0.8 - 0.6 * t);
}

function buildReliefGeometry(
  grid: ReliefGrid, heights: Float32Array, thickness: number, exaggeration: number,
  texture: Texture | null, heatRange: number | null, lift: number,
) {
  const { width: gw, height: gh, bounds } = grid;
  const cols = gw + 1;
  const rows = gh + 1;
  const positions = new Float32Array(cols * rows * 3);
  const uvs = new Float32Array(cols * rows * 2);
  const maskUvs = new Float32Array(cols * rows * 2);
  const colors = heatRange ? new Float32Array(cols * rows * 3) : null;
  const color = new THREE.Color();

  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      // Altura de la esquina = promedio de las celdas que la rodean.
      let sum = 0;
      let count = 0;
      for (let dj = -1; dj <= 0; dj++) {
        for (let di = -1; di <= 0; di++) {
          const ci = i + di;
          const cj = j + dj;
          if (ci >= 0 && ci < gw && cj >= 0 && cj < gh) {
            sum += heights[cj * gw + ci];
            count++;
          }
        }
      }
      const h = count ? sum / count : 0;
      const x = bounds.x0 + (i / gw) * (bounds.x1 - bounds.x0);
      const y = bounds.y1 - (j / gh) * (bounds.y1 - bounds.y0);
      const k = j * cols + i;
      positions.set([x, y, thickness + lift + h * exaggeration], k * 3);
      maskUvs.set([i / gw, 1 - j / gh], k * 2);
      if (texture) uvs.set([x / texture.scale / texture.width, 1 + y / texture.scale / texture.height], k * 2);
      if (colors && heatRange) {
        heatColor(h, heatRange, color);
        colors.set([color.r, color.g, color.b], k * 3);
      }
    }
  }

  const indices = new Uint32Array(gw * gh * 6);
  let n = 0;
  for (let j = 0; j < gh; j++) {
    for (let i = 0; i < gw; i++) {
      const a = j * cols + i;
      const b = a + 1;
      const c = a + cols;
      const d = c + 1;
      indices.set([a, c, b, b, c, d], n);
      n += 6;
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
  geometry.setAttribute("uv1", new THREE.BufferAttribute(maskUvs, 2));
  if (colors) geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  geometry.computeVertexNormals();
  return geometry;
}

/** Altura (mm) del relieve en (x, y), interpolada; 0 fuera de la malla. */
function sampleRelief(grid: ReliefGrid, heights: Float32Array, x: number, y: number) {
  const { width: gw, height: gh, bounds } = grid;
  const fx = ((x - bounds.x0) / (bounds.x1 - bounds.x0)) * gw - 0.5;
  const fy = ((bounds.y1 - y) / (bounds.y1 - bounds.y0)) * gh - 0.5;
  const i0 = Math.max(0, Math.min(gw - 1, Math.floor(fx)));
  const j0 = Math.max(0, Math.min(gh - 1, Math.floor(fy)));
  const i1 = Math.min(gw - 1, i0 + 1);
  const j1 = Math.min(gh - 1, j0 + 1);
  const tx = Math.max(0, Math.min(1, fx - i0));
  const ty = Math.max(0, Math.min(1, fy - j0));
  const top = heights[j0 * gw + i0] * (1 - tx) + heights[j0 * gw + i1] * tx;
  const bottom = heights[j1 * gw + i0] * (1 - tx) + heights[j1 * gw + i1] * tx;
  return top * (1 - ty) + bottom * ty;
}

type Loop = { x: number; y: number }[];

/** Subdivide un polígono cerrado para que cada tramo mida a lo sumo `step`. */
function resampleLoop(points: Loop, step: number): Loop {
  const out: Loop = [];
  for (let i = 0; i < points.length; i++) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    const parts = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / step));
    for (let k = 0; k < parts; k++) {
      out.push({ x: a.x + ((b.x - a.x) * k) / parts, y: a.y + ((b.y - a.y) * k) / parts });
    }
  }
  return out;
}

/**
 * Paredes del contorno y de los huecos, desde `bottom` hasta la superficie medida: la cara
 * superior real sigue el relieve, así que las paredes también.
 */
function buildSkirt(
  model: Model3D, grid: ReliefGrid, heights: Float32Array, thickness: number, exaggeration: number,
  bottom: number, lift: number,
) {
  const long = Math.max(grid.bounds.x1 - grid.bounds.x0, grid.bounds.y1 - grid.bounds.y0);
  const step = long / 220;
  const loops: Loop[] = [resampleLoop(model.contour.map(([x, y]) => ({ x, y })), step)];
  for (const hole of holesOf(model)) {
    const count = Math.max(24, Math.min(96, Math.ceil((2 * Math.PI * hole.r) / step)));
    loops.push(
      Array.from({ length: count }, (_, i) => ({
        x: hole.x + hole.r * Math.cos((2 * Math.PI * i) / count),
        y: hole.y + hole.r * Math.sin((2 * Math.PI * i) / count),
      })),
    );
  }
  const positions: number[] = [];
  for (const loop of loops) {
    for (let i = 0; i < loop.length; i++) {
      const a = loop[i];
      const b = loop[(i + 1) % loop.length];
      const za = thickness + lift + sampleRelief(grid, heights, a.x, a.y) * exaggeration;
      const zb = thickness + lift + sampleRelief(grid, heights, b.x, b.y) * exaggeration;
      positions.push(
        a.x, a.y, bottom, b.x, b.y, bottom, b.x, b.y, zb,
        a.x, a.y, bottom, b.x, b.y, zb, a.x, a.y, za,
      );
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(positions), 3));
  geometry.computeVertexNormals();
  return geometry;
}

function ReliefSurface({
  model, relief, heights, thickness, exaggeration, photo, heat, wireframe, lift,
}: {
  model: Model3D;
  relief: ReliefOn;
  heights: Float32Array;
  thickness: number;
  exaggeration: number;
  photo: Texture | null;
  heat: boolean;
  wireframe: boolean;
  lift: number;
}) {
  const range = Math.max(0.3, (relief.measures.flatness_mm ?? 1) * 0.6);
  const mask = useMemo(() => buildMask(model, relief.grid.bounds), [model, relief.grid.bounds]);
  const geometry = useMemo(
    () => buildReliefGeometry(relief.grid, heights, thickness, exaggeration, photo, heat ? range : null, lift),
    [relief.grid, heights, thickness, exaggeration, photo, heat, range, lift],
  );
  return photo && !heat ? (
    <PhotoMaterial geometry={geometry} photo={photo} mask={mask} wireframe={wireframe} />
  ) : (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        color={heat ? "#ffffff" : "#aeb4bc"}
        vertexColors={heat}
        alphaMap={mask}
        alphaTest={0.5}
        roughness={0.55}
        metalness={heat ? 0.05 : 0.5}
        side={THREE.DoubleSide}
        wireframe={wireframe}
      />
    </mesh>
  );
}

function PhotoMaterial({
  geometry, photo, mask, wireframe,
}: { geometry: THREE.BufferGeometry; photo: Texture; mask: THREE.Texture; wireframe: boolean }) {
  const map = useLoader(THREE.TextureLoader, photo.url);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        map={map}
        alphaMap={mask}
        alphaTest={0.5}
        roughness={0.75}
        metalness={0.1}
        side={THREE.DoubleSide}
        wireframe={wireframe}
      />
    </mesh>
  );
}

// ---------------------------------------------------------------------------
// Escena
// ---------------------------------------------------------------------------

function PhotoTop({
  shape, texture, z, wireframe,
}: { shape: THREE.Shape; texture: Texture; z: number; wireframe: boolean }) {
  const map = useLoader(THREE.TextureLoader, texture.url);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 8;
  const geometry = useMemo(() => topGeometry(shape, texture), [shape, texture]);
  return (
    <mesh geometry={geometry} position={[0, 0, z]}>
      <meshStandardMaterial map={map} roughness={0.75} metalness={0.1} wireframe={wireframe} />
    </mesh>
  );
}

interface SceneProps {
  model: Model3D;
  relief: ReliefOn | null;
  thickness: number;
  exaggeration: number;
  showPhoto: boolean;
  byType: boolean;
  heat: boolean;
  wireframe: boolean;
  selected: number | string | null;
  onSelect: (id: number | string | null) => void;
  controlsRef: React.MutableRefObject<{ object: THREE.Camera; target: THREE.Vector3; update: () => void } | null>;
  distance: number;
}

function Scene({
  model, relief, thickness, exaggeration, showPhoto, byType, heat, wireframe, selected, onSelect, controlsRef, distance,
}: SceneProps) {
  const [hovered, setHovered] = useState<number | string | null>(null);
  const shape = useMemo(() => buildShape(model), [model]);
  const heights = useMemo(() => (relief ? decodeGrid(relief.grid) : null), [relief]);
  // Con relieve, la cara superior sólida debe quedar por debajo de toda la superficie medida
  // (si no, la tapa donde el relieve se hunde); las paredes las pone `skirt`.
  const plateDepth = useMemo(() => {
    if (!heights) return thickness;
    let lowest = 0;
    for (const value of heights) lowest = Math.min(lowest, value);
    return Math.max(0.05 * thickness, thickness + lowest * exaggeration - 0.03 * thickness);
  }, [heights, thickness, exaggeration]);
  const plate = useMemo(
    () => new THREE.ExtrudeGeometry(shape, { depth: plateDepth, bevelEnabled: false, curveSegments: 32 }),
    [shape, plateDepth],
  );
  const plainTop = useMemo(() => new THREE.ShapeGeometry(shape, 32), [shape]);
  const center = useMemo(() => {
    const box = new THREE.Box3().setFromPoints(model.contour.map(([x, y]) => new THREE.Vector3(x, y, 0)));
    return box.getCenter(new THREE.Vector3());
  }, [model.contour]);

  const photo = showPhoto && model.texture ? model.texture : null;
  const features = model.features.filter((feature) => feature.enabled !== false);
  const active = hovered ?? selected;
  const lift = distance * 0.0004;
  const skirt = useMemo(
    () => (relief && heights ? buildSkirt(model, relief.grid, heights, thickness, exaggeration, plateDepth * 0.97, lift) : null),
    [model, relief, heights, thickness, exaggeration, plateDepth, lift],
  );

  return (
    <>
      <ambientLight intensity={0.9} />
      <hemisphereLight args={["#ffffff", "#5a5f6a", 0.6]} />
      <directionalLight position={[distance, distance * 0.6, distance]} intensity={1.6} />
      <directionalLight position={[-distance, -distance * 0.4, distance * 0.5]} intensity={0.6} />

      <group position={[-center.x, -center.y, -thickness / 2]}>
        <mesh geometry={plate}>
          <meshStandardMaterial color="#8d939c" roughness={0.55} metalness={0.5} wireframe={wireframe} />
        </mesh>

        {skirt && (
          <mesh geometry={skirt}>
            <meshStandardMaterial color="#8d939c" roughness={0.55} metalness={0.5} side={THREE.DoubleSide} wireframe={wireframe} />
          </mesh>
        )}

        {relief && heights ? (
          <Suspense fallback={null}>
            <ReliefSurface
              model={model}
              relief={relief}
              heights={heights}
              thickness={thickness}
              exaggeration={exaggeration}
              photo={photo}
              heat={heat}
              wireframe={wireframe}
              lift={lift}
            />
          </Suspense>
        ) : photo ? (
          <Suspense fallback={null}>
            <PhotoTop shape={shape} texture={photo} z={thickness + lift} wireframe={wireframe} />
          </Suspense>
        ) : (
          <mesh geometry={plainTop} position={[0, 0, thickness + lift]}>
            <meshStandardMaterial color="#aeb4bc" roughness={0.5} metalness={0.5} wireframe={wireframe} />
          </mesh>
        )}

        {features.map((feature) => {
          const isActive = feature.id === active;
          const color = byType ? FEATURE_TYPES[feature.type]?.color ?? "#fb923c" : REAL_COLORS[feature.type];
          const head = feature.type === "BARRENO" ? 0 : headOf(feature);
          const base = relief && heights ? sampleRelief(relief.grid, heights, feature.x, feature.y) * exaggeration : 0;
          return (
            <group key={feature.id} position={[feature.x, feature.y, base]}>
              {feature.type !== "BARRENO" && (
                <mesh rotation={[Math.PI / 2, 0, 0]}>
                  <latheGeometry args={[headProfile(feature, thickness), 40]} />
                  <meshStandardMaterial
                    color={color}
                    emissive={isActive ? "#ffffff" : "#000000"}
                    emissiveIntensity={isActive ? 0.35 : 0}
                    roughness={0.45}
                    metalness={feature.type.startsWith("INSERTO") && !byType ? 0.45 : 0.1}
                    side={THREE.DoubleSide}
                    wireframe={wireframe}
                  />
                </mesh>
              )}
              {(byType || isActive) && feature.type === "BARRENO" && (
                <mesh position={[0, 0, thickness + distance * 0.0008]}>
                  <ringGeometry args={[feature.radius, feature.radius * 1.12 + distance * 0.001, 48]} />
                  <meshBasicMaterial color={FEATURE_TYPES.BARRENO.color} />
                </mesh>
              )}
              {/* Zona de clic/hover, un poco mayor que el feature para que sea fácil de apuntar. */}
              <mesh
                position={[0, 0, thickness + head + distance * 0.0012]}
                onPointerOver={(event) => { event.stopPropagation(); setHovered(feature.id); }}
                onPointerOut={() => setHovered((current) => (current === feature.id ? null : current))}
                onClick={(event) => { event.stopPropagation(); onSelect(feature.id === selected ? null : feature.id); }}
              >
                <circleGeometry args={[Math.max(outerOf(feature) * 1.1, distance * 0.006), 24]} />
                <meshBasicMaterial transparent opacity={0} depthWrite={false} />
              </mesh>
              {isActive && (
                <Html position={[0, 0, thickness + head + outerOf(feature) * 0.5]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
                  <div className="-translate-y-full whitespace-nowrap rounded-lg border border-zinc-600 bg-zinc-950/95 px-2.5 py-1.5 text-xs text-zinc-100 shadow-xl">
                    <div className="flex items-center gap-1.5 font-semibold">
                      <span className="h-2 w-2 rounded-full" style={{ background: FEATURE_TYPES[feature.type]?.color ?? "#a1a1aa" }} />
                      #{feature.id} · {typeLabel(feature.type)}
                    </div>
                    <div className="mt-0.5 text-[10px] text-zinc-400">
                      Radio {feature.radius.toFixed(2)} {model.unit}
                      {feature.type !== "BARRENO" && ` · barreno ${boreOf(feature).toFixed(2)} ${model.unit}`}
                    </div>
                    {feature.height_mm !== undefined && feature.type !== "BARRENO" && (
                      <div className="text-[10px] text-emerald-300">
                        Altura medida {feature.height_mm.toFixed(2)} mm{feature.height_reliable === false ? " (poco confiable)" : ""}
                      </div>
                    )}
                  </div>
                </Html>
              )}
            </group>
          );
        })}
      </group>

      <OrbitControls
        ref={controlsRef as never}
        makeDefault
        enableDamping
        dampingFactor={0.12}
        minDistance={distance * 0.05}
        maxDistance={distance * 6}
        target={[0, 0, 0]}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Panel de medidas
// ---------------------------------------------------------------------------

const QUALITY_STYLE: Record<ReliefMeasures["quality"], string> = {
  Buena: "bg-emerald-500/15 text-emerald-300",
  Regular: "bg-amber-500/15 text-amber-300",
  Pobre: "bg-red-500/15 text-red-300",
};

function MeasuresPanel({
  measures, measuredThickness, thickness,
}: { measures: ReliefMeasures; measuredThickness: number | null; thickness: number }) {
  const row = (label: string, value: string, warn = false) => (
    <div className="flex justify-between gap-6">
      <span className="text-zinc-500">{label}</span>
      <span className={`tabular-nums ${warn ? "text-amber-300" : "text-zinc-100"}`}>{value}</span>
    </div>
  );
  const usingMeasured = measuredThickness !== null && Math.abs(thickness - measuredThickness) < 0.005;
  return (
    <div className="w-60 space-y-1.5 rounded-xl border border-zinc-700 bg-zinc-950/90 p-3 text-xs backdrop-blur">
      <div className="mb-1 flex items-center justify-between">
        <span className="flex items-center gap-1.5 font-semibold text-zinc-100">
          <Gauge size={13} className="text-emerald-400" /> Medidas (RealSense)
        </span>
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${QUALITY_STYLE[measures.quality]}`}>
          {measures.quality}
        </span>
      </div>
      {row("Escala", `${measures.scale_mm_per_px.toFixed(3)} mm/px`)}
      {row("Distancia a la cámara", `${measures.distance_mm.toFixed(0)} mm`)}
      {row("Inclinación", `${measures.tilt_deg.toFixed(1)}°`, measures.tilt_deg > 3)}
      {row(
        "Espesor",
        measuredThickness !== null
          ? `${measuredThickness.toFixed(2)} mm${usingMeasured ? "" : " (ajustado)"}`
          : "no medido",
        measuredThickness === null,
      )}
      {measures.flatness_mm !== null && row("Planitud (P-V)", `${measures.flatness_mm.toFixed(2)} mm`)}
      {measures.rms_mm !== null && row("Rugosidad (RMS)", `${measures.rms_mm.toFixed(2)} mm`)}
      {row("Profundidad válida", `${(measures.valid_fraction * 100).toFixed(0)} %`, measures.valid_fraction < 0.6)}
      {measures.noise_mm !== null && row("Ruido temporal", `${measures.noise_mm.toFixed(2)} mm`)}
      {measures.thickness_source && (
        <p className="pt-1 text-[10px] text-zinc-500">
          Espesor medido por {measures.thickness_source.includes("hueco") ? "el hueco" : "la mesa"}; supone la pieza apoyada en la mesa.
        </p>
      )}
      {measures.warnings.map((warning) => (
        <p key={warning} className="rounded bg-amber-500/10 px-2 py-1 text-[10px] text-amber-300">
          {warning}
        </p>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Visor
// ---------------------------------------------------------------------------

export default function PartViewer3D({ model, className = "h-[70vh]", fill = false, onSelectFeature }: Props) {
  const relief = model.relief?.available ? model.relief : null;
  const measuredThickness = relief?.measures.thickness_mm ?? null;

  const bounds = useMemo(() => {
    const box = new THREE.Box2().setFromPoints(model.contour.map(([x, y]) => new THREE.Vector2(x, y)));
    const size = box.getSize(new THREE.Vector2());
    return { width: size.x, height: size.y, long: Math.max(size.x, size.y, 1e-6) };
  }, [model.contour]);

  const distance = bounds.long * 1.5;
  const defaultThickness = measuredThickness ?? bounds.long * 0.01;
  const [thickness, setThickness] = useState(defaultThickness);
  const [exaggeration, setExaggeration] = useState(relief ? 8 : 1);
  const [showPhoto, setShowPhoto] = useState(true);
  const [byType, setByType] = useState(false);
  const [heat, setHeat] = useState(false);
  const [wireframe, setWireframe] = useState(false);
  const [showMeasures, setShowMeasures] = useState(true);
  const [selected, setSelected] = useState<number | string | null>(null);
  const controlsRef = useRef<{ object: THREE.Camera; target: THREE.Vector3; update: () => void } | null>(null);

  useEffect(() => {
    setSelected(null);
  }, [model.contour]);

  // Al llegar una medición distinta se parte del espesor medido.
  useEffect(() => {
    setThickness(defaultThickness);
    setExaggeration(relief ? 8 : 1);
  }, [defaultThickness, relief ? 1 : 0]); // eslint-disable-line react-hooks/exhaustive-deps

  function select(id: number | string | null) {
    setSelected(id);
    onSelectFeature?.(id);
  }

  function view(position: [number, number, number]) {
    const controls = controlsRef.current;
    if (!controls) return;
    controls.object.position.set(...position);
    controls.target.set(0, 0, 0);
    controls.update();
  }

  const buttonClass =
    "inline-flex items-center gap-1.5 rounded-md border border-zinc-700 bg-zinc-950/85 px-2.5 py-1.5 text-xs text-zinc-200 backdrop-blur transition hover:bg-zinc-800";
  const toggleClass = (on: boolean) => `${buttonClass} ${on ? "border-emerald-500/50 text-emerald-300" : ""}`;
  const sliderMax = Math.max(bounds.long * 0.06, (measuredThickness ?? 0) * 3);

  return (
    <div
      className={`overflow-hidden bg-gradient-to-b from-zinc-900 to-black ${
        fill ? "absolute inset-0" : `relative rounded-xl border border-zinc-800 ${className}`
      }`}
    >
      <Canvas
        camera={{ position: [0, 0, distance], fov: 35, near: distance / 200, far: distance * 30 }}
        dpr={[1, 2]}
        gl={{ antialias: true, preserveDrawingBuffer: true }}
        onPointerMissed={() => select(null)}
      >
        <Scene
          model={model}
          relief={relief}
          thickness={thickness}
          exaggeration={exaggeration}
          showPhoto={showPhoto}
          byType={byType}
          heat={heat && relief !== null}
          wireframe={wireframe}
          selected={selected}
          onSelect={select}
          controlsRef={controlsRef}
          distance={distance}
        />
      </Canvas>

      <div className="absolute left-3 top-3 z-10 flex flex-wrap gap-1.5">
        <button type="button" className={buttonClass} onClick={() => view([0, 0, distance])}>
          <Box size={13} /> Frontal
        </button>
        <button type="button" className={buttonClass} onClick={() => view([distance * 0.6, -distance * 0.7, distance * 0.75])}>
          <Box size={13} /> Isométrica
        </button>
        <button type="button" className={buttonClass} onClick={() => view([distance, 0, distance * 0.08])}>
          <Layers size={13} /> Perfil
        </button>
        <button type="button" className={buttonClass} onClick={() => view([0, 0, -distance])}>
          <Eye size={13} /> Reverso
        </button>
      </div>

      <div className="absolute right-3 top-3 z-10 flex flex-col items-end gap-2">
        <div className="flex flex-wrap justify-end gap-1.5">
          {model.texture && (
            <button type="button" className={toggleClass(showPhoto && !heat)} onClick={() => { setShowPhoto((value) => !value); setHeat(false); }}>
              Foto
            </button>
          )}
          {relief && (
            <button type="button" className={toggleClass(heat)} onClick={() => setHeat((value) => !value)}>
              <Gauge size={13} /> Planitud
            </button>
          )}
          <button type="button" className={toggleClass(byType)} onClick={() => setByType((value) => !value)}>
            <Palette size={13} /> Color por tipo
          </button>
          <button type="button" className={toggleClass(wireframe)} onClick={() => setWireframe((value) => !value)}>
            Malla
          </button>
          {relief && (
            <button type="button" className={toggleClass(showMeasures)} onClick={() => setShowMeasures((value) => !value)}>
              <Ruler size={13} /> Medidas
            </button>
          )}
        </div>
        {relief && showMeasures && (
          <MeasuresPanel measures={relief.measures} measuredThickness={measuredThickness} thickness={thickness} />
        )}
      </div>

      <div className="absolute bottom-3 left-3 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-zinc-700 bg-zinc-950/85 px-3 py-2 text-xs text-zinc-300 backdrop-blur">
        <label
          className="flex items-center gap-2"
          title={measuredThickness !== null ? "Medido con la profundidad; puede ajustarlo." : "Una foto no mide el espesor: ajústelo al real de la lámina."}
        >
          {measuredThickness !== null ? "Espesor" : "Espesor estimado"}
          <input
            type="range"
            min={Math.max(0.1, bounds.long * 0.001)}
            max={sliderMax}
            step={Math.max(0.01, bounds.long * 0.0005)}
            value={Math.min(thickness, sliderMax)}
            onChange={(event) => setThickness(Number(event.target.value))}
            className="w-28 accent-emerald-500"
          />
          <span className="w-16 tabular-nums text-zinc-400">
            {thickness.toFixed(2)} {model.unit}
          </span>
        </label>
        {relief && (
          <label className="flex items-center gap-2" title="Amplifica las desviaciones de planitud para verlas; no cambia las medidas.">
            Relieve ×
            <input
              type="range"
              min={1}
              max={40}
              step={1}
              value={exaggeration}
              onChange={(event) => setExaggeration(Number(event.target.value))}
              className="w-24 accent-emerald-500"
            />
            <span className="w-6 tabular-nums text-zinc-400">{exaggeration}</span>
          </label>
        )}
      </div>

      <div className="pointer-events-none absolute bottom-3 right-3 z-10 max-w-[40%] rounded-md bg-zinc-950/70 px-2 py-1 text-right text-[10px] text-zinc-500">
        Arrastrar: girar · Rueda: zoom · Clic derecho: mover.{" "}
        {relief
          ? "Relieve medido con la profundidad de la RealSense; el espesor y las alturas pequeñas tienen la incertidumbre del sensor."
          : "Reconstrucción paramétrica a partir de la foto: alturas y espesor son estimados."}
      </div>
    </div>
  );
}
