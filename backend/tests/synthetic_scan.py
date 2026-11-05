"""Escena sintetica para el escaner 3D: una placa grande sobre una mesa, recorrida por una camara.

Una placa de 600 x 400 mm y 3 mm de espesor apoyada en una mesa, con huecos y conos. Se renderizan
cuadros RGB-D con raycasting de Open3D desde una trayectoria "de mano" (boustrophedon con
temblor), con ruido de profundidad y perdidas como un sensor real. La verdad conocida permite
comparar la reconstruccion (dimensiones, planitud, deriva de la pose).

Unidades: metros (como Open3D). Convencion de camara: x derecha, y abajo, z hacia adelante.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
import open3d as o3d

PLATE_X, PLATE_Y = 0.30, 0.20  # semiancho / semialto de la placa
THICKNESS = 0.003
TABLE_X, TABLE_Y = 0.46, 0.34
GRID = 0.001  # 1 mm

WIDTH, HEIGHT = 640, 360
FX = FY = 460.0
CX, CY = 319.5, 179.5


def _noise(rng, shape, cell):
    """Ruido suave multiescala (textura) en [0, 1]."""
    out = np.zeros(shape, np.float32)
    for octave, weight in ((cell, 0.55), (cell // 3 or 1, 0.3), (max(cell // 9, 1), 0.15)):
        small = rng.random((shape[0] // octave + 2, shape[1] // octave + 2)).astype(np.float32)
        out += weight * cv2.resize(small, (shape[1], shape[0]), interpolation=cv2.INTER_CUBIC)
    return np.clip(out, 0, 1)


def build_scene(seed: int = 3):
    """Devuelve (RaycastingScene, malla legacy, datos de verdad)."""
    rng = np.random.default_rng(seed)
    xs = np.arange(-TABLE_X, TABLE_X + GRID, GRID, dtype=np.float32)
    ys = np.arange(-TABLE_Y, TABLE_Y + GRID, GRID, dtype=np.float32)
    xx, yy = np.meshgrid(xs, ys)
    height = np.full(xx.shape, -THICKNESS, np.float32)  # mesa

    plate = (np.abs(xx) <= PLATE_X) & (np.abs(yy) <= PLATE_Y)
    height[plate] = 0.0

    holes = [(0.10, 0.02, 0.055), (-0.20, -0.12, 0.012), (-0.20, 0.12, 0.012), (0.22, -0.14, 0.012), (0.22, 0.14, 0.012)]
    for hx, hy, hr in holes:  # huecos pasantes: se ve la mesa
        height[np.hypot(xx - hx, yy - hy) <= hr] = -THICKNESS
    cones = [(-0.10, 0.10, 0.009), (-0.10, -0.10, 0.009), (0.0, -0.13, 0.009), (-0.25, 0.0, 0.009)]
    for cx_, cy_, cr in cones:
        r = np.hypot(xx - cx_, yy - cy_)
        height += np.where(r <= cr, THICKNESS * (1.0 - r / cr), 0.0).astype(np.float32)

    # Textura: metal claro con motas y rayas en la placa; veta oscura en la mesa.
    texture = _noise(rng, xx.shape, 24)
    veins = 0.5 + 0.5 * np.sin(yy * 260.0 + 6.0 * _noise(rng, xx.shape, 90))
    metal = 0.62 + 0.25 * texture
    wood = 0.28 + 0.22 * veins * texture
    shade = np.where(plate & (height >= -1e-6), metal, wood)
    colors = np.stack([shade * 0.96, shade, shade * 1.03], axis=-1).clip(0, 1)

    ny, nx = xx.shape
    vertices = np.stack([xx.ravel(), yy.ravel(), height.ravel()], axis=1).astype(np.float64)
    index = np.arange(ny * nx).reshape(ny, nx)
    a, b, c, d = index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, :-1].ravel(), index[1:, 1:].ravel()
    triangles = np.concatenate([np.stack([a, c, b], 1), np.stack([b, c, d], 1)]).astype(np.int32)

    mesh = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(vertices), o3d.utility.Vector3iVector(triangles))
    mesh.vertex_colors = o3d.utility.Vector3dVector(colors.reshape(-1, 3).astype(np.float64))
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    truth = {"plate": (2 * PLATE_X, 2 * PLATE_Y), "thickness": THICKNESS, "holes": holes, "cones": cones}
    return scene, mesh, truth


def pose_looking_down(x: float, y: float, z: float, yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0) -> np.ndarray:
    """Camara (camara -> mundo) mirando hacia -Z del mundo, con pequenas rotaciones."""
    base = np.array([[1.0, 0, 0], [0, -1.0, 0], [0, 0, -1.0]])
    cyaw, syaw = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    rz = np.array([[cyaw, -syaw, 0], [syaw, cyaw, 0], [0, 0, 1]])
    ry = np.array([[cp, 0, sp], [0, 1, 0], [-sp, 0, cp]])
    rx = np.array([[1, 0, 0], [0, cr, -sr], [0, sr, cr]])
    pose = np.eye(4)
    pose[:3, :3] = rz @ ry @ rx @ base
    pose[:3, 3] = (x, y, z)
    return pose


def trajectory(step: float = 0.008, seed: int = 5, height: float = 0.35, jitter: bool = True) -> list[np.ndarray]:
    """Recorrido en serpentina sobre la placa (2 pasadas) con temblor de mano."""
    rng = np.random.default_rng(seed)
    waypoints = [(-0.12, -0.07), (0.12, -0.07), (0.12, 0.07), (-0.12, 0.07)]
    points = []
    for (x0, y0), (x1, y1) in zip(waypoints[:-1], waypoints[1:]):
        count = max(int(math.hypot(x1 - x0, y1 - y0) / step), 1)
        for k in range(count):
            t = k / count
            points.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    points.append(waypoints[-1])
    count = len(points)
    # Temblor suave (paseo aleatorio filtrado) en posicion, altura y orientacion.
    def smooth(scale):
        walk = np.cumsum(rng.normal(0, 1, count))
        walk = cv2.GaussianBlur(walk.reshape(-1, 1).astype(np.float32), (1, 0), 4).ravel()
        walk -= walk.mean()
        return walk / (np.abs(walk).max() + 1e-9) * scale
    if jitter:
        jx, jy, jz = smooth(0.004), smooth(0.004), smooth(0.015)
        yaw, pitch, roll = smooth(math.radians(3)), smooth(math.radians(1.5)), smooth(math.radians(1.5))
    else:
        jx = jy = jz = yaw = pitch = roll = np.zeros(count)
    return [pose_looking_down(x + jx[i], y + jy[i], height + jz[i], yaw[i], pitch[i], roll[i]) for i, (x, y) in enumerate(points)]


def render(scene, mesh, pose: np.ndarray, noise: float = 0.0005, dropout: float = 0.02, seed: int = 0):
    """RGB-D desde `pose`: color BGR uint8 y profundidad float32 (m, 0 = sin dato)."""
    intrinsic = np.array([[FX, 0, CX], [0, FY, CY], [0, 0, 1.0]])
    extrinsic = np.linalg.inv(pose)
    rays = scene.create_rays_pinhole(intrinsic, extrinsic, WIDTH, HEIGHT)
    hits = scene.cast_rays(rays)
    t_hit = hits["t_hit"].numpy()
    rays_np = rays.numpy()
    origin, direction = rays_np[..., :3], rays_np[..., 3:]
    valid = np.isfinite(t_hit)
    world = origin + direction * np.where(valid, t_hit, 0)[..., None]
    cam = (world - pose[:3, 3]) @ pose[:3, :3]
    depth = np.where(valid, cam[..., 2], 0).astype(np.float32)

    triangles = np.asarray(mesh.triangles)
    colors = np.asarray(mesh.vertex_colors)
    ids = hits["primitive_ids"].numpy().astype(np.int64)
    ids = np.where((ids < 0) | (ids >= len(triangles)), 0, ids)  # rayo sin impacto: id invalido (uint32 max)
    uv = hits["primitive_uvs"].numpy()
    tri = triangles[ids]
    w0 = (1 - uv[..., 0] - uv[..., 1])[..., None]
    rgb = w0 * colors[tri[..., 0]] + uv[..., 0:1] * colors[tri[..., 1]] + uv[..., 1:2] * colors[tri[..., 2]]
    rgb = np.where(valid[..., None], rgb, 0.0)
    bgr = (rgb[..., ::-1] * 255).clip(0, 255).astype(np.uint8)

    rng = np.random.default_rng(seed)
    sigma = noise * (np.maximum(depth, 0.1) / 0.35) ** 2
    depth = depth + rng.normal(0, 1, depth.shape).astype(np.float32) * sigma.astype(np.float32)
    depth[rng.random(depth.shape) < dropout] = 0.0
    depth[depth < 0.05] = 0.0
    return bgr, depth.astype(np.float32)
