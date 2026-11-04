import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import { Canvas, useLoader } from "@react-three/fiber";
import { Html, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import { Box, Eye, Layers, Palette } from "lucide-react";

import { FEATURE_TYPES, typeLabel } from "./FeatureInfo";

/** Datos mínimos para reconstruir la pieza. Coordenadas con Y hacia arriba, en `unit`. */
export interface Model3D {
  unit: string;
  contour: [number, number][];
  features: {
    id: number | string;
    type: string;
    x: number;
    y: number;
    radius: number;
    bore_radius?: number;
    enabled?: boolean;
  }[];
  /** Foto de la pieza: px = (x / scale, -y / scale). Sin ella la placa se ve lisa. */
  texture?: { url: string; width: number; height: number; scale: number } | null;
}

interface Props {
  model: Model3D;
  className?: string;
  /** Llena el contenedor padre (que debe ser `relative`) en vez de usar `className` para el alto. */
  fill?: boolean;
  onSelectFeature?: (id: number | string | null) => void;
}

type Feature = Model3D["features"][number];

// Altura de cada tipo sobre la cara superior, en radios del feature (estimaciones: una
// sola foto no contiene profundidad real).
const HEAD_HEIGHT: Record<string, number> = { INSERTO: 0.05, INSERTO_LATON: 0.05, DELRON_CONICO: 0.8, DELRON_PLANO: 0.3 };

const REAL_COLORS: Record<string, string> = {
  INSERTO: "#9aa0a8",
  INSERTO_LATON: "#c9a24a",
  DELRON_CONICO: "#d6b36a",
  DELRON_PLANO: "#e6dcc2",
};

function boreOf(feature: Feature) {
  if (feature.type === "BARRENO") return feature.radius;
  return Math.min(feature.bore_radius ?? feature.radius * 0.35, feature.radius * 0.9);
}

function headProfile(feature: Feature, t: number): THREE.Vector2[] {
  const r = feature.radius;
  const b = boreOf(feature);
  const h = (HEAD_HEIGHT[feature.type] ?? 0.1) * r;
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

function buildShape(model: Model3D) {
  const shape = new THREE.Shape(model.contour.map(([x, y]) => new THREE.Vector2(x, y)));
  for (const feature of model.features) {
    if (feature.enabled === false) continue;
    const radius = boreOf(feature);
    if (radius <= 0) continue;
    const hole = new THREE.Path();
    hole.absarc(feature.x, feature.y, radius, 0, Math.PI * 2, true);
    shape.holes.push(hole);
  }
  return shape;
}

/** Cara superior con la foto: UV = posición del vértice en la imagen original. */
function topGeometry(shape: THREE.Shape, texture: NonNullable<Model3D["texture"]>) {
  const geometry = new THREE.ShapeGeometry(shape, 32);
  const position = geometry.getAttribute("position");
  const uv = new Float32Array(position.count * 2);
  for (let i = 0; i < position.count; i++) {
    const px = position.getX(i) / texture.scale;
    const py = -position.getY(i) / texture.scale;
    uv[i * 2] = px / texture.width;
    uv[i * 2 + 1] = 1 - py / texture.height;
  }
  geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
  return geometry;
}

function PhotoTop({
  shape, texture, z, wireframe,
}: { shape: THREE.Shape; texture: NonNullable<Model3D["texture"]>; z: number; wireframe: boolean }) {
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

function Scene({
  model, thickness, showPhoto, byType, wireframe, selected, onSelect, controlsRef, distance,
}: {
  model: Model3D;
  thickness: number;
  showPhoto: boolean;
  byType: boolean;
  wireframe: boolean;
  selected: number | string | null;
  onSelect: (id: number | string | null) => void;
  controlsRef: React.MutableRefObject<{ object: THREE.Camera; target: THREE.Vector3; update: () => void } | null>;
  distance: number;
}) {
  const [hovered, setHovered] = useState<number | string | null>(null);
  const shape = useMemo(() => buildShape(model), [model]);
  const plate = useMemo(
    () => new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false, curveSegments: 32 }),
    [shape, thickness],
  );
  const plainTop = useMemo(() => new THREE.ShapeGeometry(shape, 32), [shape]);
  const center = useMemo(() => {
    const box = new THREE.Box3().setFromPoints(model.contour.map(([x, y]) => new THREE.Vector3(x, y, 0)));
    return box.getCenter(new THREE.Vector3());
  }, [model.contour]);

  const photo = showPhoto && model.texture ? model.texture : null;
  const features = model.features.filter((feature) => feature.enabled !== false);
  const active = hovered ?? selected;

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

        {photo ? (
          <Suspense fallback={null}>
            <PhotoTop shape={shape} texture={photo} z={thickness + distance * 0.0004} wireframe={wireframe} />
          </Suspense>
        ) : (
          <mesh geometry={plainTop} position={[0, 0, thickness + distance * 0.0004]}>
            <meshStandardMaterial color="#aeb4bc" roughness={0.5} metalness={0.5} wireframe={wireframe} />
          </mesh>
        )}

        {features.map((feature) => {
          const isActive = feature.id === active;
          const color = byType ? FEATURE_TYPES[feature.type]?.color ?? "#fb923c" : REAL_COLORS[feature.type];
          const height = (HEAD_HEIGHT[feature.type] ?? 0) * feature.radius;
          return (
            <group key={feature.id} position={[feature.x, feature.y, 0]}>
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
                position={[0, 0, thickness + height + distance * 0.0012]}
                onPointerOver={(event) => { event.stopPropagation(); setHovered(feature.id); }}
                onPointerOut={() => setHovered((current) => (current === feature.id ? null : current))}
                onClick={(event) => { event.stopPropagation(); onSelect(feature.id === selected ? null : feature.id); }}
              >
                <circleGeometry args={[Math.max(feature.radius * 1.1, distance * 0.006), 24]} />
                <meshBasicMaterial transparent opacity={0} depthWrite={false} />
              </mesh>
              {isActive && (
                <Html position={[0, 0, thickness + height + feature.radius * 0.5]} center zIndexRange={[20, 0]} style={{ pointerEvents: "none" }}>
                  <div className="-translate-y-full whitespace-nowrap rounded-lg border border-zinc-600 bg-zinc-950/95 px-2.5 py-1.5 text-xs text-zinc-100 shadow-xl">
                    <div className="flex items-center gap-1.5 font-semibold">
                      <span className="h-2 w-2 rounded-full" style={{ background: FEATURE_TYPES[feature.type]?.color ?? "#a1a1aa" }} />
                      #{feature.id} · {typeLabel(feature.type)}
                    </div>
                    <div className="mt-0.5 text-[10px] text-zinc-400">
                      Radio {feature.radius.toFixed(2)} {model.unit}
                      {feature.type !== "BARRENO" && ` · barreno ${boreOf(feature).toFixed(2)} ${model.unit}`}
                    </div>
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

export default function PartViewer3D({ model, className = "h-[70vh]", fill = false, onSelectFeature }: Props) {
  const bounds = useMemo(() => {
    const box = new THREE.Box2().setFromPoints(model.contour.map(([x, y]) => new THREE.Vector2(x, y)));
    const size = box.getSize(new THREE.Vector2());
    return { width: size.x, height: size.y, long: Math.max(size.x, size.y, 1e-6) };
  }, [model.contour]);

  const distance = bounds.long * 1.5;
  const [thicknessPercent, setThicknessPercent] = useState(1);
  const [showPhoto, setShowPhoto] = useState(true);
  const [byType, setByType] = useState(false);
  const [wireframe, setWireframe] = useState(false);
  const [selected, setSelected] = useState<number | string | null>(null);
  const controlsRef = useRef<{ object: THREE.Camera; target: THREE.Vector3; update: () => void } | null>(null);
  const thickness = (bounds.long * thicknessPercent) / 100;

  useEffect(() => {
    setSelected(null);
  }, [model.contour]);

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
  const toggleClass = (on: boolean) =>
    `${buttonClass} ${on ? "border-emerald-500/50 text-emerald-300" : ""}`;

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
          thickness={thickness}
          showPhoto={showPhoto}
          byType={byType}
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

      <div className="absolute right-3 top-3 z-10 flex flex-wrap justify-end gap-1.5">
        {model.texture && (
          <button type="button" className={toggleClass(showPhoto)} onClick={() => setShowPhoto((value) => !value)}>
            Foto
          </button>
        )}
        <button type="button" className={toggleClass(byType)} onClick={() => setByType((value) => !value)}>
          <Palette size={13} /> Color por tipo
        </button>
        <button type="button" className={toggleClass(wireframe)} onClick={() => setWireframe((value) => !value)}>
          Malla
        </button>
      </div>

      <div className="absolute bottom-3 left-3 z-10 flex items-center gap-3 rounded-lg border border-zinc-700 bg-zinc-950/85 px-3 py-2 text-xs text-zinc-300 backdrop-blur">
        <label className="flex items-center gap-2" title="Una foto no mide el espesor: ajústelo al real de la lámina.">
          Espesor estimado
          <input
            type="range"
            min={0.2}
            max={6}
            step={0.1}
            value={thicknessPercent}
            onChange={(event) => setThicknessPercent(Number(event.target.value))}
            className="w-28 accent-emerald-500"
          />
          <span className="w-20 tabular-nums text-zinc-400">
            {thickness.toFixed(2)} {model.unit}
          </span>
        </label>
      </div>

      <div className="pointer-events-none absolute bottom-3 right-3 z-10 max-w-[46%] rounded-md bg-zinc-950/70 px-2 py-1 text-right text-[10px] text-zinc-500">
        Arrastrar: girar · Rueda: zoom · Clic derecho: mover. Reconstrucción paramétrica a partir de la foto: alturas y espesor son estimados.
      </div>
    </div>
  );
}
