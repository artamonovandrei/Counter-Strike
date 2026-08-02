# path: backend/app/scripts/gen_map.py
"""Generate ``assets/maps/alley.json``.

The level is built from axis-aligned boxes in code rather than authored in a 3-D package.
Three reasons: the same JSON feeds the server's collision world and the client's Three.js
scene so they cannot disagree; a symmetric layout is trivial to guarantee by mirroring;
and the whole map is a few kilobytes of text that diffs cleanly in git.

Run: ``python -m app.scripts.gen_map``
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Tuple

from ..config import MAPS_DIR

MAP_NAME = "alley"
HALF = 28.0          # arena half-extent on X and Z
WALL_H = 7.0         # perimeter wall height
BUILDING_H = 4.2     # central building wall height
CRATE = 1.2          # standard crate edge length

Box = Dict[str, object]


def box(cx: float, cy: float, cz: float, sx: float, sy: float, sz: float, mat: str) -> Box:
    return {
        "p": [round(cx, 3), round(cy, 3), round(cz, 3)],
        "s": [round(sx, 3), round(sy, 3), round(sz, 3)],
        "m": mat,
    }


def crate(cx: float, cz: float, mat: str = "crate", size: float = CRATE, base: float = 0.0) -> Box:
    return box(cx, base + size * 0.5, cz, size, size, size, mat)


def mirrored(boxes: List[Box]) -> List[Box]:
    """Point-mirror through the origin so both halves of the map are identical.

    Team A approaches from -Z and team B from +Z; mirroring rather than reflecting keeps
    the *sequence* of cover identical for both, which a plain reflection does not.
    """
    out: List[Box] = []
    for b in boxes:
        p = b["p"]  # type: ignore[index]
        out.append(
            {
                "p": [-p[0], p[1], -p[2]],  # type: ignore[index]
                "s": list(b["s"]),  # type: ignore[arg-type]
                "m": b["m"],
            }
        )
    return out


def stairs_z(
    x0: float, z_edge: float, width: float, steps: int, rise: float, run: float, direction: int
) -> List[Box]:
    """A flight descending away from a platform edge along Z.

    ``z_edge`` is where the flight meets the platform and ``direction`` (+1/-1) is the way
    it runs outward. The step nearest the platform is full height and each one further out
    is lower, so a player approaching from open ground meets the *shortest* riser first.
    Getting this backwards builds a wall with decorative steps behind it — which is
    exactly what the first version of this map did.

    Every riser is below ``MoveConfig.step_height``, so the ordinary step-up in the
    movement code carries players up; no ramp or slope support is needed anywhere.
    """
    out: List[Box] = []
    for i in range(steps):
        h = rise * (steps - i)
        z = z_edge + direction * (run * i + run * 0.5)
        out.append(box(x0, h * 0.5, z, width, h, run, "concrete"))
    return out


def build() -> dict:
    boxes: List[Box] = []

    # ── ground and perimeter ──────────────────────────────────────────────────
    boxes.append(box(0, -0.5, 0, HALF * 2, 1.0, HALF * 2, "floor"))
    t = 1.0
    boxes.append(box(0, WALL_H / 2, -HALF - t / 2, HALF * 2 + t * 2, WALL_H, t, "wall"))
    boxes.append(box(0, WALL_H / 2, HALF + t / 2, HALF * 2 + t * 2, WALL_H, t, "wall"))
    boxes.append(box(-HALF - t / 2, WALL_H / 2, 0, t, WALL_H, HALF * 2, "wall"))
    boxes.append(box(HALF + t / 2, WALL_H / 2, 0, t, WALL_H, HALF * 2, "wall"))

    # ── central building: 16 x 12 with a doorway on each face ─────────────────
    bx, bz = 8.0, 6.0     # half-extents
    wt = 0.8              # wall thickness
    door = 1.6            # half-width of each doorway

    seg = (bx - door) / 2 + door / 2   # centre of each wall segment
    seg_len = bx - door
    for z in (-bz, bz):
        boxes.append(box(-seg, BUILDING_H / 2, z, seg_len, BUILDING_H, wt, "wall"))
        boxes.append(box(seg, BUILDING_H / 2, z, seg_len, BUILDING_H, wt, "wall"))
    seg_z = (bz - door) / 2 + door / 2
    seg_z_len = bz - door
    for x in (-bx, bx):
        boxes.append(box(x, BUILDING_H / 2, -seg_z, wt, BUILDING_H, seg_z_len, "wall"))
        boxes.append(box(x, BUILDING_H / 2, seg_z, wt, BUILDING_H, seg_z_len, "wall"))

    # Interior clutter: crates you can vault, and a pillar that breaks the sightline
    # straight through both doorways.
    boxes.append(box(0, 1.6, 0, 1.4, 3.2, 1.4, "metal"))
    boxes.append(crate(-4.5, -2.5))
    boxes.append(crate(4.5, 2.5))
    boxes.append(crate(-4.5, -1.2, base=CRATE))
    boxes.append(crate(4.0, -3.0))

    # ── side lanes: raised platforms with stairs at both ends ─────────────────
    for sign in (-1.0, 1.0):
        px = sign * 19.0
        boxes.append(box(px, 0.6, 0.0, 9.0, 1.2, 9.0, "concrete"))
        # Chest-high lip on the inward-facing edge only: cover when holding the angle
        # toward mid, while both Z ends stay open for the staircases.
        boxes.append(box(px - sign * 4.2, 1.75, 0.0, 0.6, 1.1, 9.0, "concrete"))
        boxes.extend(stairs_z(px, 4.5, 5.0, 4, 0.3, 0.8, +1))
        boxes.extend(stairs_z(px, -4.5, 5.0, 4, 0.3, 0.8, -1))

    # ── mirrored cover in the approach lanes ──────────────────────────────────
    half_layout: List[Box] = [
        crate(-12.0, -12.0),
        crate(-12.0, -13.2, base=CRATE),
        crate(-10.8, -12.0),
        crate(0.0, -11.0, "metal", 1.6),
        crate(6.0, -14.0),
        crate(-20.0, -12.0, "metal", 1.6),
        box(-4.0, 1.5, -18.0, 6.0, 3.0, 0.8, "wall"),
        box(9.0, 1.5, -19.0, 0.8, 3.0, 7.0, "wall"),
        crate(14.0, -8.0),
        crate(15.2, -8.0),
        crate(14.6, -8.0, base=CRATE),
        box(22.0, 1.25, -18.0, 5.0, 2.5, 0.8, "concrete"),
        crate(-24.0, -20.0, "metal", 1.6),
        crate(3.0, -23.0),
    ]
    boxes.extend(half_layout)
    boxes.extend(mirrored(half_layout))

    # ── spawns ────────────────────────────────────────────────────────────────
    # Team A starts at -Z looking toward +Z (yaw = pi), team B the reverse.
    spawn_a = [
        {"p": [x, 0.05, -24.5], "yaw": round(math.pi, 4)}
        for x in (-6.0, -3.0, 0.0, 3.0, 6.0)
    ]
    spawn_b = [
        {"p": [x, 0.05, 24.5], "yaw": 0.0}
        for x in (-6.0, -3.0, 0.0, 3.0, 6.0)
    ]

    materials = {
        "floor": {"color": "#3b3f45", "roughness": 0.95, "metalness": 0.02},
        "wall": {"color": "#6a6257", "roughness": 0.9, "metalness": 0.02},
        "concrete": {"color": "#7b7468", "roughness": 0.88, "metalness": 0.03},
        "crate": {"color": "#8a6136", "roughness": 0.8, "metalness": 0.02},
        "metal": {"color": "#5c6672", "roughness": 0.45, "metalness": 0.65},
    }

    lights = [
        {"p": [0.0, 9.0, 0.0], "color": "#fff2d8", "intensity": 1.1, "distance": 60.0},
        {"p": [-19.0, 7.0, -12.0], "color": "#cfe3ff", "intensity": 0.8, "distance": 40.0},
        {"p": [19.0, 7.0, 12.0], "color": "#cfe3ff", "intensity": 0.8, "distance": 40.0},
        {"p": [0.0, 8.0, -22.0], "color": "#ffd9b0", "intensity": 0.7, "distance": 40.0},
        {"p": [0.0, 8.0, 22.0], "color": "#ffd9b0", "intensity": 0.7, "distance": 40.0},
    ]

    return {
        "name": MAP_NAME,
        "version": 1,
        "bounds": [-HALF, -1.0, -HALF, HALF, WALL_H + 2.0, HALF],
        "ambient": "#404652",
        "sky": ["#1b2430", "#59657a"],
        "fog": {"color": "#2a323d", "near": 40.0, "far": 110.0},
        "materials": materials,
        "boxes": boxes,
        "spawns": {"A": spawn_a, "B": spawn_b},
        "lights": lights,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the WebStrike map JSON.")
    parser.add_argument("--out", type=Path, default=None, help="output path")
    args = parser.parse_args()

    data = build()
    out: Path = args.out or (MAPS_DIR / f"{MAP_NAME}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
        fh.write("\n")

    size_kb = out.stat().st_size / 1024.0
    print(f"wrote {out} — {len(data['boxes'])} brushes, {size_kb:.1f} kB")
    print(f"  bounds  {data['bounds']}")
    print(f"  spawns  A={len(data['spawns']['A'])} B={len(data['spawns']['B'])}")
    print("  next:   python -m app.scripts.gen_nav", MAP_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
