"""Motor del escaner 3D: odometria RGB-D + fusion TSDF (Open3D).

Para piezas que no caben en el campo de vision de la camara: se mueve la RealSense cerca de la
pieza y cada cuadro color+profundidad se alinea contra el ultimo fotograma clave (odometria
RGB-D hibrida: color + geometria) y se funde en un volumen TSDF, que promedia el ruido del
sensor y produce una malla continua. Todo el trabajo interno va en metros; la malla final sale
en milimetros, alineada a sus ejes principales.

Open3D se importa de forma perezosa: si no esta instalado solo falla este modulo, no la app.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


class ScannerUnavailable(RuntimeError):
    """Open3D no esta instalado o no se pudo cargar."""


def load_open3d():
    try:
        import open3d as o3d
    except Exception as error:  # ImportError o DLL ausente
        raise ScannerUnavailable(
            "El escáner 3D necesita la librería Open3D. Instálela con: pip install open3d"
        ) from error
    return o3d


@dataclass
class ScanOptions:
    voxel_mm: float = 2.0  # resolucion del modelo
    max_depth_mm: float = 1000.0  # lo mas lejano que se fusiona
    min_depth_mm: float = 120.0
    work_width: int = 640  # ancho de trabajo (se reduce la imagen para ir fluido)
    odometry_width: int = 320  # la odometria (lo mas caro) corre a menor resolucion que la fusion
    key_translation_mm: float = 8.0  # movimiento minimo para fundir otro cuadro
    key_rotation_deg: float = 1.5
    max_jump_mm: float = 150.0  # un salto mayor entre cuadros se considera rastreo perdido
    max_jump_deg: float = 25.0
    min_valid_fraction: float = 0.15
    # Una alineacion buena deja ~0.003 de error fotometrico (intensidad 0..1); una falsa (placa plana
    # que "se parece a si misma" desplazada) ~0.15. El umbral deja margen para el ruido de un sensor real.
    max_photometric_error: float = 0.07
    min_overlap: float = 0.30
    min_consistent: float = 0.50
    min_weight: float = 1.0  # observaciones minimas de un voxel para entrar a la malla


def _rotation_angle_deg(rotation: np.ndarray) -> float:
    cosine = (np.trace(rotation) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


class ScanEngine:
    """Una sesion de escaneo: recibe cuadros RGB-D y mantiene la reconstruccion."""

    def __init__(self, options: ScanOptions, intrinsics: dict[str, float]) -> None:
        self.o3d = load_open3d()
        o3d = self.o3d
        self.options = options
        width, height = int(intrinsics["width"]), int(intrinsics["height"])
        self.factor = min(1.0, options.work_width / width)
        self.width, self.height = int(round(width * self.factor)), int(round(height * self.factor))
        f = self.factor
        self.intrinsic = o3d.camera.PinholeCameraIntrinsic(
            self.width, self.height, intrinsics["fx"] * f, intrinsics["fy"] * f,
            intrinsics["ppx"] * f, intrinsics["ppy"] * f,
        )
        voxel = options.voxel_mm / 1000.0
        o3c = o3d.core
        self.o3c = o3c
        # TSDF de tensores (VoxelBlockGrid): el volumen clasico (ScalableTSDFVolume) devuelve una malla
        # vacia en Open3D 0.20, y este es ademas ~5x mas rapido. Los bloques de 8^3 crecen solos.
        self.volume = o3d.t.geometry.VoxelBlockGrid(
            attr_names=("tsdf", "weight", "color"),
            attr_dtypes=(o3c.float32, o3c.float32, o3c.float32),
            attr_channels=((1), (1), (3)),
            voxel_size=voxel,
            block_resolution=8,
            block_count=4000,
            device=o3c.Device("CPU:0"),
        )
        self.truncation_voxels = max(4.0, 6.0 / options.voxel_mm)  # banda de truncamiento en voxeles
        self.k_tensor = o3c.Tensor(np.asarray(self.intrinsic.intrinsic_matrix), o3c.Dtype.Float64)
        # Odometria a menor resolucion: el costo cae ~4x y la precision del movimiento casi no cambia.
        self.odo_factor = min(1.0, options.odometry_width / self.width)
        self.odo_size = (int(round(self.width * self.odo_factor)), int(round(self.height * self.odo_factor)))
        g = self.odo_factor
        self.odo_intrinsic = o3d.camera.PinholeCameraIntrinsic(
            self.odo_size[0], self.odo_size[1], self.intrinsic.get_focal_length()[0] * g,
            self.intrinsic.get_focal_length()[1] * g, self.intrinsic.get_principal_point()[0] * g,
            self.intrinsic.get_principal_point()[1] * g,
        )
        self.odometry_option = o3d.pipelines.odometry.OdometryOption()
        self.odometry_option.depth_min = options.min_depth_mm / 1000.0
        self.odometry_option.depth_max = options.max_depth_mm / 1000.0
        self.jacobian = o3d.pipelines.odometry.RGBDOdometryJacobianFromHybridTerm()

        self.pose = np.eye(4)  # camara -> mundo (el mundo es la primera camara)
        self.poses: list[np.ndarray] = []  # de los fotogramas fundidos
        self.key_pose = np.eye(4)
        self.key_rgbd: Any = None
        self.last_relative = np.eye(4)
        self.preview = o3d.geometry.PointCloud()
        self._since_downsample = 0

        self.frames = 0
        self.keyframes = 0
        self.lost_total = 0
        self.lost_streak = 0
        self.tracking = "inicializando"
        self.message = ""
        self.speed_mm_per_frame = 0.0  # movimiento medio entre cuadros (media movil)
        self.alignment_quality = 100.0  # 0-100, de la ultima alineacion aceptada

    # ------------------------------------------------------------------ cuadro a cuadro
    def _prepare(self, color_bgr: np.ndarray, depth_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if self.factor < 1.0:
            size = (self.width, self.height)
            color_bgr = cv2.resize(color_bgr, size, interpolation=cv2.INTER_AREA)
            depth_m = cv2.resize(depth_m, size, interpolation=cv2.INTER_NEAREST)  # sin mezclar con pixeles vacios
        depth = depth_m.astype(np.float32).copy()
        depth[(depth < self.options.min_depth_mm / 1000.0) | (depth > self.options.max_depth_mm / 1000.0)] = 0.0
        return cv2.cvtColor(color_bgr, cv2.COLOR_BGR2RGB), depth

    def _rgbd(self, color_rgb: np.ndarray, depth: np.ndarray, intensity: bool):
        o3d = self.o3d
        return o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(np.ascontiguousarray(color_rgb)),
            o3d.geometry.Image(np.ascontiguousarray(depth)),
            depth_scale=1.0,
            depth_trunc=self.options.max_depth_mm / 1000.0,
            convert_rgb_to_intensity=intensity,
        )

    def _lost(self, reason: str) -> dict[str, Any]:
        self.lost_total += 1
        self.lost_streak += 1
        self.tracking = "perdido"
        self.message = reason
        return {"tracked": False, "integrated": False, "reason": reason}

    def process(self, color_bgr: np.ndarray, depth_m: np.ndarray) -> dict[str, Any]:
        """Procesa un cuadro. Devuelve {tracked, integrated, reason}."""
        self.frames += 1
        color_rgb, depth = self._prepare(color_bgr, depth_m)
        valid = float((depth > 0).mean())
        if valid < self.options.min_valid_fraction:
            return self._lost(f"Poca profundidad válida ({valid:.0%}): ajuste la distancia a la pieza (15-80 cm).")

        rgbd_color = self._rgbd(color_rgb, depth, intensity=False)
        if self.odo_factor < 1.0:
            small_color = cv2.resize(color_rgb, self.odo_size, interpolation=cv2.INTER_AREA)
            small_depth = cv2.resize(depth, self.odo_size, interpolation=cv2.INTER_NEAREST)
            rgbd_intensity = self._rgbd(small_color, small_depth, intensity=True)
        else:
            rgbd_intensity = self._rgbd(color_rgb, depth, intensity=True)

        if self.key_rgbd is None:
            self._integrate(rgbd_color, rgbd_intensity, np.eye(4))
            self.tracking = "ok"
            self.message = ""
            return {"tracked": True, "integrated": True, "reason": ""}

        success, transform, _ = self.o3d.pipelines.odometry.compute_rgbd_odometry(
            rgbd_intensity, self.key_rgbd, self.odo_intrinsic, self.last_relative, self.jacobian, self.odometry_option,
        )
        if not success:
            return self._lost("Rastreo perdido: regrese a una zona ya escaneada y mueva la cámara más despacio.")

        quality = self._alignment_check(rgbd_intensity, transform)
        if quality is not None:
            return self._lost(quality)

        pose = self.key_pose @ transform
        jump = np.linalg.inv(self.pose) @ pose
        if (
            np.linalg.norm(jump[:3, 3]) * 1000.0 > self.options.max_jump_mm
            or _rotation_angle_deg(jump[:3, :3]) > self.options.max_jump_deg
        ):
            return self._lost("Movimiento demasiado brusco: mueva la cámara más despacio.")

        step_mm = float(np.linalg.norm(jump[:3, 3])) * 1000.0
        self.speed_mm_per_frame = step_mm if self.speed_mm_per_frame == 0.0 else 0.8 * self.speed_mm_per_frame + 0.2 * step_mm
        self.pose = pose
        self.last_relative = transform
        self.lost_streak = 0
        self.tracking = "ok"
        self.message = ""
        moved = float(np.linalg.norm(transform[:3, 3])) * 1000.0
        turned = _rotation_angle_deg(transform[:3, :3])
        if moved >= self.options.key_translation_mm or turned >= self.options.key_rotation_deg:
            self._integrate(rgbd_color, rgbd_intensity, pose)
            return {"tracked": True, "integrated": True, "reason": ""}
        return {"tracked": True, "integrated": False, "reason": ""}

    def _alignment_check(self, current, transform: np.ndarray) -> str | None:
        """Valida la odometria con una medida independiente: reproyecta el cuadro actual sobre el
        fotograma clave y compara intensidades. `success` de Open3D no basta: en una placa plana
        con textura devuelve alineaciones falsas (desplazadas) que corromperian la malla."""
        depth = np.asarray(current.depth)
        intensity = np.asarray(current.color)
        key_depth = np.asarray(self.key_rgbd.depth)
        key_intensity = np.asarray(self.key_rgbd.color)
        height, width = depth.shape
        fx, fy = self.odo_intrinsic.get_focal_length()
        cx, cy = self.odo_intrinsic.get_principal_point()
        v, u = np.nonzero(depth > 0)
        if len(v) < 500:
            return "Poca profundidad válida: ajuste la distancia a la pieza."
        z = depth[v, u]
        points = np.stack([(u - cx) * z / fx, (v - cy) * z / fy, z])
        moved = transform[:3, :3] @ points + transform[:3, 3:4]
        front = moved[2] > 0.05
        safe_z = np.where(front, moved[2], 1.0)
        up, vp = fx * moved[0] / safe_z + cx, fy * moved[1] / safe_z + cy
        inside = front & (up >= 0) & (up < width - 1) & (vp >= 0) & (vp < height - 1)
        overlap = float(inside.mean())
        ui, vi = np.round(up[inside]).astype(int), np.round(vp[inside]).astype(int)
        reference = key_depth[vi, ui]
        consistent = (reference > 0) & (np.abs(reference - moved[2][inside]) < 0.02)
        consistency = float(consistent.mean()) if consistent.size else 0.0
        if overlap < self.options.min_overlap or consistency < self.options.min_consistent:
            return "Poco solape con lo ya escaneado: regrese a una zona conocida y mueva la cámara más despacio."
        error = float(np.median(np.abs(intensity[v, u][inside][consistent] - key_intensity[vi, ui][consistent])))
        if error > self.options.max_photometric_error:
            return "Alineación dudosa (movimiento brusco o poca textura): regrese a una zona escaneada y mueva más despacio."
        self.alignment_quality = float(np.clip(100.0 * (1.0 - error / self.options.max_photometric_error), 0.0, 100.0))
        return None

    def _integrate(self, rgbd_color, rgbd_intensity, pose: np.ndarray) -> None:
        o3d = self.o3d
        extrinsic = np.linalg.inv(pose)  # mundo -> camara
        self._fuse(np.asarray(rgbd_color.color), np.asarray(rgbd_color.depth), extrinsic)
        self.pose = self.key_pose = pose
        self.key_rgbd = rgbd_intensity
        self.last_relative = np.eye(4)
        self.keyframes += 1
        self.poses.append(pose.copy())

        # Nube de vista previa: acumulada y aligerada, para no extraer la malla en cada refresco.
        cloud = o3d.geometry.PointCloud.create_from_rgbd_image(rgbd_color, self.intrinsic, extrinsic)
        cloud = cloud.voxel_down_sample(max(self.options.voxel_mm * 3.0, 6.0) / 1000.0)
        self.preview += cloud
        self._since_downsample += 1
        if self._since_downsample >= 8:
            self.preview = self.preview.voxel_down_sample(max(self.options.voxel_mm * 3.0, 6.0) / 1000.0)
            self._since_downsample = 0

    def _fuse(self, color_rgb: np.ndarray, depth: np.ndarray, extrinsic: np.ndarray) -> None:
        o3d, o3c = self.o3d, self.o3c
        color = o3d.t.geometry.Image(o3c.Tensor((np.ascontiguousarray(color_rgb) / 255.0).astype(np.float32)))
        depth_image = o3d.t.geometry.Image(o3c.Tensor(np.ascontiguousarray(depth, dtype=np.float32)))
        ext = o3c.Tensor(np.ascontiguousarray(extrinsic), o3c.Dtype.Float64)
        depth_max = self.options.max_depth_mm / 1000.0
        blocks = self.volume.compute_unique_block_coordinates(
            depth_image, self.k_tensor, ext, 1.0, depth_max, self.truncation_voxels
        )
        self.volume.integrate(blocks, depth_image, color, self.k_tensor, ext, 1.0, depth_max, self.truncation_voxels)

    # ------------------------------------------------------------------ lectura en vivo
    def preview_arrays(self, max_points: int = 60000) -> tuple[np.ndarray, np.ndarray]:
        """Puntos (mm) y colores (uint8) de la vista previa."""
        points = np.asarray(self.preview.points)
        colors = np.asarray(self.preview.colors)
        if len(points) > max_points:
            pick = np.random.default_rng(1).choice(len(points), max_points, replace=False)
            points, colors = points[pick], colors[pick]
        return (points * 1000.0).astype(np.float32), (colors * 255.0).clip(0, 255).astype(np.uint8)

    def coverage_mm(self) -> list[float] | None:
        points = np.asarray(self.preview.points)
        if len(points) < 10:
            return None
        extent = (points.max(axis=0) - points.min(axis=0)) * 1000.0
        return [round(float(v), 1) for v in extent]

    def camera_mm(self) -> dict[str, list[float]]:
        return {
            "position": [round(float(v), 1) for v in self.pose[:3, 3] * 1000.0],
            "forward": [round(float(v), 4) for v in self.pose[:3, 2]],
        }

    # ------------------------------------------------------------------ resultado
    def build_mesh(self, isolate: bool = False) -> tuple[Any, dict[str, Any]]:
        """Malla final en mm, alineada a sus ejes. Devuelve (malla, info del procesado)."""
        o3d = self.o3d
        try:
            mesh = self.volume.extract_triangle_mesh(weight_threshold=self.options.min_weight).to_legacy()
        except RuntimeError as error:  # volumen vacio: Open3D lo reporta como error
            raise RuntimeError("No hay suficiente geometría escaneada para generar la malla.") from error
        if len(mesh.triangles) == 0:
            raise RuntimeError("No hay suficiente geometría escaneada para generar la malla.")
        mesh.remove_duplicated_vertices()
        mesh.remove_degenerate_triangles()
        mesh.remove_unreferenced_vertices()
        mesh = _drop_small_clusters(mesh)
        mesh.scale(1000.0, center=(0.0, 0.0, 0.0))  # m -> mm

        info: dict[str, Any] = {"isolated": False, "isolate_message": ""}
        centroid = np.mean([p[:3, 3] for p in self.poses], axis=0) * 1000.0 if self.poses else np.zeros(3)
        if isolate:
            mesh, info["isolated"], info["isolate_message"] = isolate_part(mesh, centroid)
        mesh = align_to_axes(mesh, centroid)
        mesh.compute_vertex_normals()
        return mesh, info


def _drop_small_clusters(mesh, keep_fraction: float = 0.02):
    """Quita islas flotantes pequenas (ruido) y deja los componentes relevantes."""
    clusters, counts, _ = mesh.cluster_connected_triangles()
    clusters, counts = np.asarray(clusters), np.asarray(counts)
    if len(counts) <= 1:
        return mesh
    small = counts < keep_fraction * counts.max()
    mesh.remove_triangles_by_mask(small[clusters])
    mesh.remove_unreferenced_vertices()
    return mesh


def isolate_part(mesh, camera_centroid: np.ndarray, tolerance_mm: float = 1.2):
    """Quita la mesa y deja solo la pieza apoyada en ella.

    La mesa es el plano paralelo mas bajo con superficie importante. La altura de cada punto se mide
    sobre la superficie LOCAL de la mesa (plano + curvatura ajustados a sus puntos), no sobre un plano
    global: la deriva de rotacion de la odometria inclina la mesa y la pieza juntas, y un corte a
    altura constante borraria un extremo de la pieza. Si no hay mesa visible no se toca nada y se avisa.
    Devuelve (malla, aislada, mensaje).
    """
    o3d = load_open3d()
    vertices = np.asarray(mesh.vertices)
    total = len(vertices)
    if total < 500:
        return mesh, False, "Muy poca geometría para aislar la pieza."

    remaining = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(vertices))
    index = np.arange(total)
    planes: list[tuple[float, np.ndarray]] = []  # (altura a lo largo de la normal, indices de vertices)
    normal_ref: np.ndarray | None = None
    for _ in range(5):
        if len(remaining.points) < 0.02 * total:
            break
        model, inliers = remaining.segment_plane(distance_threshold=0.8, ransac_n=3, num_iterations=600)
        if len(inliers) < 0.02 * total:
            break
        norm = float(np.linalg.norm(model[:3]))
        normal, offset = np.array(model[:3]) / norm, -model[3] / norm
        center = np.asarray(remaining.points)[inliers].mean(axis=0)
        if np.dot(normal, camera_centroid - center) < 0:  # la normal apunta hacia la camara
            normal, offset = -normal, -offset
        if normal_ref is None:
            normal_ref = normal
        if math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(normal, normal_ref)))))) < 8.0:
            planes.append((float(offset), index[inliers]))
        remaining = remaining.select_by_index(inliers, invert=True)
        index = np.delete(index, inliers)

    if len(planes) < 2 or normal_ref is None:
        return mesh, False, "No se distingue la mesa (no hay un plano paralelo debajo de la pieza); se conserva todo."

    table_level = min(offset for offset, _ in planes)
    table_ids = np.concatenate([ids for offset, ids in planes if offset <= table_level + 2.0])
    if len(table_ids) < 300:
        return mesh, False, "Se ve muy poca mesa para separarla de la pieza."

    helper = np.array([1.0, 0, 0]) if abs(normal_ref[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(normal_ref, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(normal_ref, e1)
    u, w, h = vertices @ e1, vertices @ e2, vertices @ normal_ref

    def basis(a, b):  # plano + curvatura suave
        a, b = a / 300.0, b / 300.0
        return np.column_stack((np.ones_like(a), a, b, a * a, a * b, b * b))

    keep_fit = np.ones(len(table_ids), bool)
    coeffs = np.zeros(6)
    for _ in range(4):
        coeffs, *_ = np.linalg.lstsq(basis(u[table_ids][keep_fit], w[table_ids][keep_fit]), h[table_ids][keep_fit], rcond=None)
        residual = h[table_ids] - basis(u[table_ids], w[table_ids]) @ coeffs
        sigma = max(1.4826 * float(np.median(np.abs(residual[keep_fit]))), 0.3)
        keep_fit = np.abs(residual) < 3.0 * sigma
    relative = h - basis(u, w) @ coeffs  # altura sobre la mesa local

    above = relative > 2 * tolerance_mm
    if above.sum() < 0.02 * total:
        return mesh, False, "Solo se ve un plano: no se pudo separar la pieza de la mesa."
    counts, edges = np.histogram(relative[above & (relative < 60.0)], bins=np.arange(2 * tolerance_mm, 60.0, 0.5))
    face = float(edges[int(np.argmax(counts))] + 0.25)  # altura de la cara dominante sobre la mesa
    # El TSDF suaviza el escalon entre la pieza y la mesa en una rampa simetrica: cortar a la mitad de
    # su altura deja el borde de la pieza donde esta (cortar a ras de la mesa lo "infla" varios mm).
    cut = max(tolerance_mm, 0.5 * face)
    footprint = _footprint_mask(u, w, (relative > cut) & (relative < face + 3.0))
    mesh.remove_vertices_by_mask(~((relative > cut) & footprint))
    mesh.remove_unreferenced_vertices()
    mesh = _drop_small_clusters(mesh, keep_fraction=0.2)
    if len(mesh.triangles) == 0:
        return mesh, False, "Al quitar la mesa no quedó geometría."
    return mesh, True, ""


def _footprint_mask(u: np.ndarray, w: np.ndarray, band: np.ndarray, pixel_mm: float = 2.0) -> np.ndarray:
    """Vertices cuya proyeccion cae dentro de la huella de la cara superior de la pieza.

    El ruido de la mesa produce restos pegados al borde que el corte por altura no quita; la huella
    (puntos de la cara, cerrados y rellenados) los descarta sin deformar la pieza.
    """
    u0, w0 = u.min(), w.min()
    cols = int((u.max() - u0) / pixel_mm) + 3
    rows = int((w.max() - w0) / pixel_mm) + 3
    raster = np.zeros((rows, cols), np.uint8)
    raster[((w[band] - w0) / pixel_mm).astype(int), ((u[band] - u0) / pixel_mm).astype(int)] = 255
    raster = cv2.morphologyEx(raster, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(raster)
    if count <= 1:
        return np.ones(len(u), bool)
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    contours, _ = cv2.findContours((labels == biggest).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    footprint = np.zeros_like(raster)
    cv2.drawContours(footprint, contours, -1, 255, thickness=cv2.FILLED)
    footprint = cv2.dilate(footprint, np.ones((3, 3), np.uint8))
    return footprint[((w - w0) / pixel_mm).astype(int), ((u - u0) / pixel_mm).astype(int)] > 0


def align_to_axes(mesh, camera_centroid: np.ndarray):
    """Gira la malla a sus ejes principales (largo en X, ancho en Y, espesor en Z, cara hacia arriba)
    y la apoya con su esquina minima en el origen.

    Z es la normal del plano dominante (la cara que vio la camara); X/Y salen del rectangulo de
    area minima de la huella proyectada. (La caja orientada de Open3D se calcula con el casco
    convexo y, con bordes ruidosos, puede salir girada decenas de grados.)
    """
    o3d = load_open3d()
    vertices = np.asarray(mesh.vertices)
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(vertices))
    model, _ = cloud.segment_plane(distance_threshold=0.8, ransac_n=3, num_iterations=600)
    normal = np.array(model[:3]) / np.linalg.norm(model[:3])
    if np.dot(normal, camera_centroid - vertices.mean(axis=0)) < 0:  # la cara que vio la camara queda arriba
        normal = -normal
    helper = np.array([1.0, 0, 0]) if abs(normal[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(normal, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(normal, e1)
    flat = np.column_stack((vertices @ e1, vertices @ e2)).astype(np.float32)
    box = cv2.boxPoints(cv2.minAreaRect(flat))
    edges = [box[(k + 1) % 4] - box[k] for k in range(4)]
    long_edge = max(edges, key=lambda edge: float(np.linalg.norm(edge)))
    direction = long_edge / np.linalg.norm(long_edge)
    x_axis = direction[0] * e1 + direction[1] * e2
    y_axis = np.cross(normal, x_axis)  # derecho: X x Y = Z
    rotation = np.column_stack((x_axis, y_axis, normal))
    aligned = vertices @ rotation
    aligned -= aligned.min(axis=0)
    mesh.vertices = o3d.utility.Vector3dVector(aligned)
    return mesh


def analyze_mesh(mesh) -> dict[str, Any]:
    """Medidas de la malla ya alineada (mm): dimensiones, area y planitud de la cara principal."""
    o3d = load_open3d()
    vertices = np.asarray(mesh.vertices)
    extent = vertices.max(axis=0) - vertices.min(axis=0)
    result: dict[str, Any] = {
        "dimensions_mm": [round(float(v), 1) for v in extent],
        "vertices": int(len(vertices)),
        "triangles": int(len(mesh.triangles)),
        "area_mm2": round(float(mesh.get_surface_area()), 1),
        "watertight": bool(mesh.is_watertight()),
    }
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(vertices))
    if len(vertices) >= 200:
        model, inliers = cloud.segment_plane(distance_threshold=0.8, ransac_n=3, num_iterations=600)
        normal = np.array(model[:3])
        norm = np.linalg.norm(normal)
        residual = (vertices[inliers] @ normal + model[3]) / norm
        result["flatness"] = {
            "planar_fraction": round(len(inliers) / len(vertices), 3),
            "peak_to_valley_mm": round(float(np.percentile(residual, 98) - np.percentile(residual, 2)), 2),
            "rms_mm": round(float(np.std(residual)), 3),
            "tilt_deg": round(math.degrees(math.acos(min(1.0, abs(float(normal[2] / norm))))), 2),
        }
    return result
