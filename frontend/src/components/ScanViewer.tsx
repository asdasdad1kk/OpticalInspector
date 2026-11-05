import { useEffect, useMemo } from "react";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import * as THREE from "three";

import type { MeshData, PointCloudData } from "../lib/scannerApi";

interface Props {
  cloud?: PointCloudData | null;
  mesh?: MeshData | null;
  /** Posición y dirección de la cámara (mm, marco de la primera captura) para dibujarla mientras se escanea. */
  camera?: { position: number[]; forward: number[] } | null;
  showColor?: boolean;
  wireframe?: boolean;
  showGrid?: boolean;
  /** Cambiarlo reencuadra la vista sobre lo que haya. */
  fitToken?: number | string;
  className?: string;
}

/**
 * La nube en vivo está en el marco de la primera cámara (x derecha, y abajo, z hacia la pieza); se gira 180° sobre X
 * para verla con Z arriba. La malla final ya viene alineada a sus ejes, con Z arriba.
 */
const PREVIEW_ROTATION: [number, number, number] = [Math.PI, 0, 0];

function boundsOf(positions: Float32Array): THREE.Box3 {
  const box = new THREE.Box3();
  const point = new THREE.Vector3();
  const step = Math.max(1, Math.floor(positions.length / 3 / 20000));
  for (let i = 0; i < positions.length; i += 3 * step) {
    box.expandByPoint(point.set(positions[i], positions[i + 1], positions[i + 2]));
  }
  return box;
}

function Fit({ box, token }: { box: THREE.Box3 | null; token: number | string | undefined }) {
  const { camera, controls } = useThree();
  useEffect(() => {
    if (!box || box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size = Math.max(box.getSize(new THREE.Vector3()).length(), 50);
    camera.position.set(center.x + size * 0.15, center.y - size * 0.9, center.z + size * 0.75);
    camera.near = size / 500;
    camera.far = size * 40;
    camera.updateProjectionMatrix();
    const orbit = controls as unknown as { target: THREE.Vector3; update: () => void } | null;
    if (orbit) {
      orbit.target.copy(center);
      orbit.update();
    }
    camera.lookAt(center);
    // Solo al cambiar `token`: durante el escaneo la vista no debe saltar cada vez que crece la nube.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, controls]);
  return null;
}

function CameraMarker({ position, forward, scale }: { position: number[]; forward: number[]; scale: number }) {
  const quaternion = useMemo(() => {
    const q = new THREE.Quaternion();
    q.setFromUnitVectors(new THREE.Vector3(0, 0, 1), new THREE.Vector3(...forward).normalize());
    return q;
  }, [forward]);
  return (
    <group position={position as [number, number, number]} quaternion={quaternion}>
      <mesh position={[0, 0, scale * 0.6]} rotation={[Math.PI / 2, Math.PI / 4, 0]}>
        <coneGeometry args={[scale * 0.55, scale * 1.2, 4]} />
        <meshBasicMaterial color="#34d399" wireframe />
      </mesh>
      <mesh>
        <sphereGeometry args={[scale * 0.14, 12, 12]} />
        <meshBasicMaterial color="#34d399" />
      </mesh>
    </group>
  );
}

function Cloud({ cloud, color }: { cloud: PointCloudData; color: boolean }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(cloud.positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(cloud.colors, 3, true));
    return g;
  }, [cloud]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <points geometry={geometry}>
      <pointsMaterial size={2.5} sizeAttenuation={false} vertexColors={color} color={color ? "#ffffff" : "#94a3b8"} />
    </points>
  );
}

function Surface({ mesh, color, wireframe }: { mesh: MeshData; color: boolean; wireframe: boolean }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(mesh.positions, 3));
    g.setAttribute("color", new THREE.BufferAttribute(mesh.colors, 3, true));
    g.setIndex(new THREE.BufferAttribute(mesh.indices, 1));
    g.computeVertexNormals();
    return g;
  }, [mesh]);
  useEffect(() => () => geometry.dispose(), [geometry]);
  return (
    <mesh geometry={geometry}>
      <meshStandardMaterial
        vertexColors={color}
        color={color ? "#ffffff" : "#b4bbc4"}
        roughness={0.65}
        metalness={0.15}
        side={THREE.DoubleSide}
        wireframe={wireframe}
      />
    </mesh>
  );
}

export default function ScanViewer({
  cloud, mesh, camera, showColor = true, wireframe = false, showGrid = true, fitToken, className = "h-[70vh]",
}: Props) {
  const box = useMemo(() => {
    if (mesh) return boundsOf(mesh.positions);
    if (cloud && cloud.count > 0) {
      const b = boundsOf(cloud.positions);
      // Marco de la nube: se gira 180° sobre X (y, z cambian de signo).
      return new THREE.Box3(
        new THREE.Vector3(b.min.x, -b.max.y, -b.max.z),
        new THREE.Vector3(b.max.x, -b.min.y, -b.min.z),
      );
    }
    return null;
  }, [mesh, cloud]);
  const size = box && !box.isEmpty() ? box.getSize(new THREE.Vector3()) : new THREE.Vector3(600, 400, 20);
  const grid = Math.ceil(Math.max(size.x, size.y, 100) / 100) * 100 * 1.4;

  return (
    <div className={`relative overflow-hidden rounded-xl border border-zinc-800 bg-gradient-to-b from-zinc-900 to-black ${className}`}>
      <Canvas camera={{ position: [300, -500, 400], up: [0, 0, 1], fov: 38, near: 1, far: 20000 }} dpr={[1, 2]}>
        <ambientLight intensity={0.85} />
        <hemisphereLight args={["#ffffff", "#4b5563", 0.6]} />
        <directionalLight position={[400, -300, 800]} intensity={1.5} />
        <directionalLight position={[-400, 300, 300]} intensity={0.5} />

        {mesh ? (
          <>
            <Surface mesh={mesh} color={showColor} wireframe={wireframe} />
            {showGrid && box && (
              <gridHelper args={[grid, Math.round(grid / 50), "#3f3f46", "#27272a"]} rotation={[Math.PI / 2, 0, 0]} position={[size.x / 2, size.y / 2, -0.5]} />
            )}
          </>
        ) : cloud && cloud.count > 0 ? (
          <group rotation={PREVIEW_ROTATION}>
            <Cloud cloud={cloud} color={showColor} />
            {camera && <CameraMarker position={camera.position} forward={camera.forward} scale={35} />}
          </group>
        ) : null}

        <Fit box={box} token={fitToken} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} />
      </Canvas>
    </div>
  );
}
