# path: backend/app/game/world.py
"""The collision world: map loading, broadphase, raycasting.

The level is a set of axis-aligned boxes. That choice is what lets the server and the
browser agree perfectly on geometry — the client builds Three.js `BoxGeometry` from the
same JSON the server collides against, so there is no mesh conversion step to disagree
about.

Broadphase is a uniform grid over XZ. With a few hundred brushes in a 60x60 m arena that
beats a BVH on build time and is trivial to keep correct.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..config import MAPS_DIR
from .mathx import AABB, EPS, Vec3, aabb_from_center, ray_aabb, aabb_normal_at

CELL_SIZE = 4.0


@dataclass
class RayHit:
    t: float
    point: Vec3
    normal: Vec3
    material: str
    box_index: int


class World:
    """Static geometry plus the queries the sim needs against it."""

    def __init__(self, data: dict):
        self.data = data
        self.name: str = data.get("name", "unnamed")
        self.boxes: List[AABB] = []
        self.materials: List[str] = []

        for b in data.get("boxes", []):
            self.boxes.append(aabb_from_center(b["p"], b["s"]))
            self.materials.append(b.get("m", "concrete"))

        bounds = data.get("bounds")
        if bounds and len(bounds) == 6:
            self.bounds: AABB = tuple(float(v) for v in bounds)  # type: ignore[assignment]
        else:
            self.bounds = self._compute_bounds()

        self.spawns: Dict[str, List[Tuple[Vec3, float]]] = {}
        for team, entries in data.get("spawns", {}).items():
            self.spawns[team] = [
                (Vec3.from_seq(e["p"]), float(e.get("yaw", 0.0))) for e in entries
            ]

        self._grid: Dict[Tuple[int, int], List[int]] = {}
        self._build_grid()

    # ── construction helpers ──────────────────────────────────────────────────

    def _compute_bounds(self) -> AABB:
        if not self.boxes:
            return (-1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
        minx = min(b[0] for b in self.boxes)
        miny = min(b[1] for b in self.boxes)
        minz = min(b[2] for b in self.boxes)
        maxx = max(b[3] for b in self.boxes)
        maxy = max(b[4] for b in self.boxes)
        maxz = max(b[5] for b in self.boxes)
        return (minx, miny, minz, maxx, maxy, maxz)

    def _build_grid(self) -> None:
        self._grid.clear()
        for i, b in enumerate(self.boxes):
            x0 = int(math.floor(b[0] / CELL_SIZE))
            x1 = int(math.floor(b[3] / CELL_SIZE))
            z0 = int(math.floor(b[2] / CELL_SIZE))
            z1 = int(math.floor(b[5] / CELL_SIZE))
            # A floor brush spans the whole map and lands in every cell; that is fine and
            # still far cheaper than testing every brush.
            for cx in range(x0, x1 + 1):
                for cz in range(z0, z1 + 1):
                    self._grid.setdefault((cx, cz), []).append(i)

    # ── queries ───────────────────────────────────────────────────────────────

    def candidates(self, box: AABB) -> List[int]:
        """Brush indices whose grid cells intersect ``box``."""
        x0 = int(math.floor(box[0] / CELL_SIZE))
        x1 = int(math.floor(box[3] / CELL_SIZE))
        z0 = int(math.floor(box[2] / CELL_SIZE))
        z1 = int(math.floor(box[5] / CELL_SIZE))
        out: Set[int] = set()
        grid = self._grid
        for cx in range(x0, x1 + 1):
            for cz in range(z0, z1 + 1):
                cell = grid.get((cx, cz))
                if cell:
                    out.update(cell)
        return list(out)

    def overlapping(self, box: AABB) -> List[int]:
        """Brush indices actually intersecting ``box``."""
        out = []
        for i in self.candidates(box):
            b = self.boxes[i]
            if (
                box[0] < b[3] and box[3] > b[0]
                and box[1] < b[4] and box[4] > b[1]
                and box[2] < b[5] and box[5] > b[2]
            ):
                out.append(i)
        return out

    def is_free(self, box: AABB) -> bool:
        return not self.overlapping(box)

    def raycast(
        self, origin: Vec3, direction: Vec3, max_dist: float, skip: Optional[int] = None
    ) -> Optional[RayHit]:
        """Nearest static-geometry hit along the ray, or None.

        Walks the ray through grid cells rather than testing every brush, which matters
        because this runs once per bullet, several times per bot per think, and for every
        line-of-sight check.
        """
        ox, oy, oz = origin.x, origin.y, origin.z
        dx, dy, dz = direction.x, direction.y, direction.z
        best_t = max_dist
        best_i = -1

        for i in self._ray_candidates(ox, oz, dx, dz, max_dist):
            if i == skip:
                continue
            t = ray_aabb(ox, oy, oz, dx, dy, dz, self.boxes[i], best_t)
            if t >= 0.0 and t < best_t:
                best_t = t
                best_i = i

        if best_i < 0:
            return None
        point = Vec3(ox + dx * best_t, oy + dy * best_t, oz + dz * best_t)
        normal = aabb_normal_at(self.boxes[best_i], point.x, point.y, point.z)
        return RayHit(best_t, point, normal, self.materials[best_i], best_i)

    def _ray_candidates(
        self, ox: float, oz: float, dx: float, dz: float, max_dist: float
    ) -> Set[int]:
        """2-D DDA over the XZ grid, collecting brushes in the traversed cells."""
        out: Set[int] = set()
        grid = self._grid
        if not grid:
            return out

        cx = int(math.floor(ox / CELL_SIZE))
        cz = int(math.floor(oz / CELL_SIZE))

        # A ray that is (near) vertical never leaves its column.
        if abs(dx) < EPS and abs(dz) < EPS:
            cell = grid.get((cx, cz))
            if cell:
                out.update(cell)
            return out

        step_x = 1 if dx > 0 else -1
        step_z = 1 if dz > 0 else -1
        inv_dx = 1.0 / dx if abs(dx) > EPS else math.inf
        inv_dz = 1.0 / dz if abs(dz) > EPS else math.inf

        next_x = (cx + (1 if dx > 0 else 0)) * CELL_SIZE
        next_z = (cz + (1 if dz > 0 else 0)) * CELL_SIZE
        t_max_x = (next_x - ox) * inv_dx if math.isfinite(inv_dx) else math.inf
        t_max_z = (next_z - oz) * inv_dz if math.isfinite(inv_dz) else math.inf
        t_delta_x = abs(CELL_SIZE * inv_dx) if math.isfinite(inv_dx) else math.inf
        t_delta_z = abs(CELL_SIZE * inv_dz) if math.isfinite(inv_dz) else math.inf

        t = 0.0
        # Guard against pathological loops if the ray is enormous relative to the grid.
        for _ in range(4096):
            cell = grid.get((cx, cz))
            if cell:
                out.update(cell)
            if t_max_x < t_max_z:
                t = t_max_x
                if t > max_dist:
                    break
                cx += step_x
                t_max_x += t_delta_x
            else:
                t = t_max_z
                if t > max_dist:
                    break
                cz += step_z
                t_max_z += t_delta_z
        return out

    def line_of_sight(self, a: Vec3, b: Vec3) -> bool:
        """True when nothing static blocks the segment a→b."""
        delta = b - a
        dist = delta.length()
        if dist < EPS:
            return True
        hit = self.raycast(a, delta * (1.0 / dist), dist)
        return hit is None

    def drop_to_floor(self, p: Vec3, radius: float, height: float, max_drop: float = 20.0) -> Optional[Vec3]:
        """Find the standing position under ``p``, or None if there's no support.

        Used by the nav generator and by spawn validation. It raycasts down from head
        height rather than sampling, so a point over a pit correctly reports nothing.
        """
        hit = self.raycast(Vec3(p.x, p.y + height, p.z), Vec3(0.0, -1.0, 0.0), max_drop + height)
        if hit is None:
            return None
        pos = Vec3(p.x, hit.point.y + 0.02, p.z)
        box = (
            pos.x - radius, pos.y + 0.05, pos.z - radius,
            pos.x + radius, pos.y + height, pos.z + radius,
        )
        if not self.is_free(box):
            return None
        return pos

    def spawn_points(self, team: str) -> List[Tuple[Vec3, float]]:
        return self.spawns.get(team, [])

    def summary(self) -> str:
        return (
            f"map '{self.name}': {len(self.boxes)} brushes, "
            f"bounds {tuple(round(v, 1) for v in self.bounds)}, "
            f"spawns A={len(self.spawns.get('A', []))} B={len(self.spawns.get('B', []))}"
        )


def map_path(name: str) -> Path:
    return MAPS_DIR / f"{name}.json"


def nav_path(name: str) -> Path:
    return MAPS_DIR / f"{name}.nav.json"


def load_map_data(name: str) -> dict:
    path = map_path(name)
    if not path.exists():
        raise FileNotFoundError(
            f"map '{name}' not found at {path}. Run: python -m app.scripts.gen_map"
        )
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_world_cache: Dict[str, World] = {}


def load_world(name: str) -> World:
    """Maps are immutable and shared by every room, so one instance per name is enough."""
    world = _world_cache.get(name)
    if world is None:
        world = World(load_map_data(name))
        _world_cache[name] = world
    return world
