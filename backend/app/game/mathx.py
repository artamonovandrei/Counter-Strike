# path: backend/app/game/mathx.py
"""Minimal 3-D maths. Deliberately dependency-free — numpy's per-call overhead is worse
than pure Python for the 3-element vectors used here, and the sim never batches."""

from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple

EPS = 1e-6


class Vec3:
    """Mutable 3-vector. ``__slots__`` keeps thousands of these cheap per tick."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    # ── construction ──────────────────────────────────────────────────────────
    @staticmethod
    def from_seq(s: Sequence[float]) -> "Vec3":
        return Vec3(s[0], s[1], s[2])

    def copy(self) -> "Vec3":
        return Vec3(self.x, self.y, self.z)

    def set(self, x: float, y: float, z: float) -> "Vec3":
        self.x = x
        self.y = y
        self.z = z
        return self

    def copy_from(self, o: "Vec3") -> "Vec3":
        self.x = o.x
        self.y = o.y
        self.z = o.z
        return self

    def as_tuple(self) -> Tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def as_list(self) -> List[float]:
        return [self.x, self.y, self.z]

    def rounded(self, nd: int = 3) -> List[float]:
        return [round(self.x, nd), round(self.y, nd), round(self.z, nd)]

    # ── arithmetic ────────────────────────────────────────────────────────────
    def __add__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: "Vec3") -> "Vec3":
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x * s, self.y * s, self.z * s)

    __rmul__ = __mul__

    def __neg__(self) -> "Vec3":
        return Vec3(-self.x, -self.y, -self.z)

    def iadd_scaled(self, o: "Vec3", s: float) -> "Vec3":
        self.x += o.x * s
        self.y += o.y * s
        self.z += o.z * s
        return self

    def scale(self, s: float) -> "Vec3":
        self.x *= s
        self.y *= s
        self.z *= s
        return self

    # ── products / norms ──────────────────────────────────────────────────────
    def dot(self, o: "Vec3") -> float:
        return self.x * o.x + self.y * o.y + self.z * o.z

    def cross(self, o: "Vec3") -> "Vec3":
        return Vec3(
            self.y * o.z - self.z * o.y,
            self.z * o.x - self.x * o.z,
            self.x * o.y - self.y * o.x,
        )

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def length_sq(self) -> float:
        return self.x * self.x + self.y * self.y + self.z * self.z

    def length_xz(self) -> float:
        return math.sqrt(self.x * self.x + self.z * self.z)

    def normalized(self) -> "Vec3":
        n = self.length()
        if n < EPS:
            return Vec3(0.0, 0.0, 0.0)
        inv = 1.0 / n
        return Vec3(self.x * inv, self.y * inv, self.z * inv)

    def normalize(self) -> "Vec3":
        n = self.length()
        if n < EPS:
            return self
        inv = 1.0 / n
        self.x *= inv
        self.y *= inv
        self.z *= inv
        return self

    def distance(self, o: "Vec3") -> float:
        dx = self.x - o.x
        dy = self.y - o.y
        dz = self.z - o.z
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def distance_sq(self, o: "Vec3") -> float:
        dx = self.x - o.x
        dy = self.y - o.y
        dz = self.z - o.z
        return dx * dx + dy * dy + dz * dz

    def distance_xz(self, o: "Vec3") -> float:
        dx = self.x - o.x
        dz = self.z - o.z
        return math.sqrt(dx * dx + dz * dz)

    def lerp(self, o: "Vec3", t: float) -> "Vec3":
        return Vec3(
            self.x + (o.x - self.x) * t,
            self.y + (o.y - self.y) * t,
            self.z + (o.z - self.z) * t,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Vec3({self.x:.3f}, {self.y:.3f}, {self.z:.3f})"

    def __eq__(self, o: object) -> bool:
        if not isinstance(o, Vec3):
            return NotImplemented
        return (
            abs(self.x - o.x) < EPS and abs(self.y - o.y) < EPS and abs(self.z - o.z) < EPS
        )


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def angles_to_dir(yaw: float, pitch: float) -> Vec3:
    """Convert view angles to a unit direction.

    Matches Three.js conventions: Y is up, yaw 0 looks down -Z, positive pitch looks up.
    """
    cp = math.cos(pitch)
    return Vec3(-math.sin(yaw) * cp, math.sin(pitch), -math.cos(yaw) * cp)


def forward_xz(yaw: float) -> Vec3:
    """Horizontal forward vector for a yaw (unit length)."""
    return Vec3(-math.sin(yaw), 0.0, -math.cos(yaw))


def right_xz(yaw: float) -> Vec3:
    """Horizontal right vector for a yaw (unit length)."""
    return Vec3(math.cos(yaw), 0.0, -math.sin(yaw))


def dir_to_angles(d: Vec3) -> Tuple[float, float]:
    """Inverse of :func:`angles_to_dir`. Returns ``(yaw, pitch)``."""
    horiz = math.sqrt(d.x * d.x + d.z * d.z)
    yaw = math.atan2(-d.x, -d.z)
    pitch = math.atan2(d.y, horiz)
    return yaw, pitch


def wrap_angle(a: float) -> float:
    """Normalise an angle to (-pi, pi]."""
    a = math.fmod(a + math.pi, math.tau)
    if a < 0.0:
        a += math.tau
    return a - math.pi


def angle_lerp(a: float, b: float, t: float) -> float:
    """Interpolate between two angles the short way around."""
    return a + wrap_angle(b - a) * t


def move_angle_towards(current: float, target: float, max_delta: float) -> float:
    """Step ``current`` toward ``target`` by at most ``max_delta`` radians."""
    diff = wrap_angle(target - current)
    if abs(diff) <= max_delta:
        return wrap_angle(target)
    return wrap_angle(current + math.copysign(max_delta, diff))


def rotate_dir(d: Vec3, yaw_off: float, pitch_off: float) -> Vec3:
    """Offset a direction by yaw/pitch deltas, going through angles.

    Used for spread and for bot aim error, where working in angle space is both cheaper
    and more meaningful (a cone in degrees) than building rotation matrices.
    """
    yaw, pitch = dir_to_angles(d)
    return angles_to_dir(yaw + yaw_off, clamp(pitch + pitch_off, -1.55, 1.55))


AABB = Tuple[float, float, float, float, float, float]  # minx,miny,minz,maxx,maxy,maxz


def aabb_from_center(center: Sequence[float], size: Sequence[float]) -> AABB:
    hx, hy, hz = size[0] * 0.5, size[1] * 0.5, size[2] * 0.5
    return (
        center[0] - hx, center[1] - hy, center[2] - hz,
        center[0] + hx, center[1] + hy, center[2] + hz,
    )


def aabb_overlap(a: AABB, b: AABB) -> bool:
    return (
        a[0] < b[3] and a[3] > b[0]
        and a[1] < b[4] and a[4] > b[1]
        and a[2] < b[5] and a[5] > b[2]
    )


def aabb_expand(a: AABB, m: float) -> AABB:
    return (a[0] - m, a[1] - m, a[2] - m, a[3] + m, a[4] + m, a[5] + m)


def aabb_union(boxes: Iterable[AABB]) -> AABB:
    it = iter(boxes)
    try:
        first = next(it)
    except StopIteration:
        return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    minx, miny, minz, maxx, maxy, maxz = first
    for b in it:
        minx = min(minx, b[0]); miny = min(miny, b[1]); minz = min(minz, b[2])
        maxx = max(maxx, b[3]); maxy = max(maxy, b[4]); maxz = max(maxz, b[5])
    return (minx, miny, minz, maxx, maxy, maxz)


def ray_aabb(
    ox: float, oy: float, oz: float,
    dx: float, dy: float, dz: float,
    box: AABB, max_t: float,
) -> float:
    """Slab test. Returns the entry distance along the ray, or -1.0 for a miss.

    Handles a component of the direction being exactly zero via infinities, which is the
    common case for axis-aligned shots down a corridor.
    """
    tmin = 0.0
    tmax = max_t

    # X slab
    if dx != 0.0:
        inv = 1.0 / dx
        t1 = (box[0] - ox) * inv
        t2 = (box[3] - ox) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return -1.0
    elif ox < box[0] or ox > box[3]:
        return -1.0

    # Y slab
    if dy != 0.0:
        inv = 1.0 / dy
        t1 = (box[1] - oy) * inv
        t2 = (box[4] - oy) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return -1.0
    elif oy < box[1] or oy > box[4]:
        return -1.0

    # Z slab
    if dz != 0.0:
        inv = 1.0 / dz
        t1 = (box[2] - oz) * inv
        t2 = (box[5] - oz) * inv
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return -1.0
    elif oz < box[2] or oz > box[5]:
        return -1.0

    return tmin


def aabb_normal_at(box: AABB, px: float, py: float, pz: float) -> Vec3:
    """Outward face normal of the box face nearest to a surface point."""
    cx = (box[0] + box[3]) * 0.5
    cy = (box[1] + box[4]) * 0.5
    cz = (box[2] + box[5]) * 0.5
    ex = (box[3] - box[0]) * 0.5
    ey = (box[4] - box[1]) * 0.5
    ez = (box[5] - box[2]) * 0.5
    dx = (px - cx) / ex if ex > EPS else 0.0
    dy = (py - cy) / ey if ey > EPS else 0.0
    dz = (pz - cz) / ez if ez > EPS else 0.0
    ax, ay, az = abs(dx), abs(dy), abs(dz)
    if ax >= ay and ax >= az:
        return Vec3(1.0 if dx > 0 else -1.0, 0.0, 0.0)
    if ay >= az:
        return Vec3(0.0, 1.0 if dy > 0 else -1.0, 0.0)
    return Vec3(0.0, 0.0, 1.0 if dz > 0 else -1.0)
