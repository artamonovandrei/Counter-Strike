# path: backend/app/scripts/gen_map.py
"""Generate ``assets/maps/alley.json``.

The level is built from axis-aligned boxes in code rather than authored in a 3-D package.
Three reasons: the same JSON feeds the server's collision world and the client's Three.js
scene so they cannot disagree; a symmetric layout is trivial to guarantee by mirroring;
and the whole map is a few kilobytes of text that diffs cleanly in git.

── Layout ────────────────────────────────────────────────────────────────────────────
A 60 m square with rotational symmetry about the origin, so both teams face an identical
sequence of cover. Three routes connect the spawns, which is the minimum that makes
rotating meaningful:

    ┌──────────────── B spawn (+Z) ────────────────┐
    │   west lane      mid / warehouse     east    │
    │      │                 │              │      │
    │   [catwalk]    ┌───────────────┐   [yard]    │
    │      │         │  two floors,  │      │      │
    │      │         │  4 doors,     │      │      │
    │   [crates]     │  balcony      │   [pipes]   │
    │      │         └───────────────┘      │      │
    │   connector ────────┴──────── connector      │
    └──────────────── A spawn (-Z) ────────────────┘

* **Mid** is the warehouse: two floors, four ground entrances, a balcony overlooking the
  hall, and stairs at either end. Whoever holds the balcony holds the map, which gives
  the sniper and the shotgun somewhere each is clearly the right pick.
* **Flanks** are the long lanes. Each has a raised platform with hard cover, so they are
  fightable without being a shooting gallery.
* **Connectors** cut across near each spawn, so a losing fight in mid is recoverable and
  spawn-camping a single lane does not win the round.

Sightlines are deliberately broken by staggered cover rather than left open: the longest
uninterrupted line is the west lane at about 40 m, which is sniper range but not
map-spanning.

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
HALF = 30.0          # arena half-extent on X and Z
WALL_H = 8.0         # perimeter wall height
CRATE = 1.2          # standard crate edge length
# The upper floor's height is a three-way compromise: high enough to walk under (the slab
# underside must clear the 1.8 m player), low enough that a staircase with legal risers and
# standable treads fits between the balcony edge and the far wall, and low enough to leave
# headroom under the roof. 2.9 m satisfies all three; 3.4 m did not, and the staircase it
# forced ran straight into the south wall.
DECK_Y = 2.9         # the surface you actually walk on upstairs
SLAB_T = 0.3         # thickness of the upper-floor slab
CEIL_H = 6.4         # warehouse roof height

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


# Explicit navigation hints: polylines the bots are told they can walk, emitted alongside
# the geometry.
#
# Stairs cannot be discovered reliably by sampling. A player's collider is 0.8 m across,
# so on any staircase with a sensible tread depth the box always overlaps the riser above,
# every sample fails the "does a player fit here" test, and the whole upper floor silently
# drops out of the graph. Real level tools solve this with authored ladder/stair links, and
# so does this one: the code that *places* a staircase knows exactly where it goes, so it
# says so instead of making the generator guess.
NAV_PATHS: List[Dict[str, object]] = []


def nav_path_hint(a: Tuple[float, float, float], b: Tuple[float, float, float], note: str) -> None:
    NAV_PATHS.append(
        {
            "a": [round(v, 3) for v in a],
            "b": [round(v, 3) for v in b],
            "note": note,
        }
    )


def mirror_nav_paths_since(mark: int) -> None:
    """Point-mirror every route added since ``mark``.

    :func:`mirrored` copies geometry but knows nothing about navigation, so mirroring a
    structure that contains a staircase silently produces a flight no bot can see. Anything
    that mirrors stairs has to mirror their routes too.
    """
    for path in list(NAV_PATHS[mark:]):
        a = path["a"]  # type: ignore[index]
        b = path["b"]  # type: ignore[index]
        NAV_PATHS.append(
            {
                "a": [-a[0], a[1], -a[2]],  # type: ignore[index]
                "b": [-b[0], b[1], -b[2]],  # type: ignore[index]
                "note": path["note"],
            }
        )


def stairs_z(
    x0: float, z_edge: float, width: float, steps: int, rise: float, run: float, direction: int,
    mat: str = "concrete",
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
        out.append(box(x0, h * 0.5, z, width, h, run, mat))
    # Bottom of the flight to the top landing, so the bots know this is a route. The
    # bottom point must clear the last step by a full player radius, or the "route starts
    # here" node is buried inside the staircase it describes.
    bottom_z = z_edge + direction * (run * steps + 0.55)
    nav_path_hint(
        (x0, 0.05, bottom_z),
        (x0, rise * steps + 0.05, z_edge - direction * 0.5),
        "stairs",
    )
    return out


def stairs_x(
    z0: float, x_edge: float, width: float, steps: int, rise: float, run: float, direction: int,
    mat: str = "concrete",
) -> List[Box]:
    """As :func:`stairs_z`, but climbing along X."""
    out: List[Box] = []
    for i in range(steps):
        h = rise * (steps - i)
        x = x_edge + direction * (run * i + run * 0.5)
        out.append(box(x, h * 0.5, z0, run, h, width, mat))
    return out


def wall_with_gap(
    axis: str, fixed: float, span: Tuple[float, float], gap: Tuple[float, float],
    y0: float, height: float, thickness: float, mat: str,
) -> List[Box]:
    """A wall along ``axis`` with a doorway cut out of it.

    Expressing openings as "wall minus gap" rather than as hand-placed segments means the
    doorway width is stated once and cannot drift when the wall moves.
    """
    out: List[Box] = []
    lo, hi = span
    g0, g1 = gap
    for a, b in ((lo, g0), (g1, hi)):
        if b - a <= 0.01:
            continue
        centre = (a + b) / 2
        length = b - a
        if axis == "x":
            out.append(box(centre, y0 + height / 2, fixed, length, height, thickness, mat))
        else:
            out.append(box(fixed, y0 + height / 2, centre, thickness, height, length, mat))
    return out


def build() -> dict:
    boxes: List[Box] = []
    NAV_PATHS.clear()

    # ── ground and perimeter ──────────────────────────────────────────────────
    boxes.append(box(0, -0.5, 0, HALF * 2, 1.0, HALF * 2, "floor"))
    t = 1.0
    boxes.append(box(0, WALL_H / 2, -HALF - t / 2, HALF * 2 + t * 2, WALL_H, t, "brick"))
    boxes.append(box(0, WALL_H / 2, HALF + t / 2, HALF * 2 + t * 2, WALL_H, t, "brick"))
    boxes.append(box(-HALF - t / 2, WALL_H / 2, 0, t, WALL_H, HALF * 2, "brick"))
    boxes.append(box(HALF + t / 2, WALL_H / 2, 0, t, WALL_H, HALF * 2, "brick"))

    # ══ MID: the warehouse ════════════════════════════════════════════════════
    # Footprint x ∈ [-11, 11], z ∈ [-9, 9]. Four ground doors, an upper floor over the
    # northern half, and a balcony rail overlooking the hall.
    wx, wz = 11.0, 9.0
    wt = 0.7
    door = 1.8  # half-width of each doorway

    # North and south faces: one central door each.
    boxes.extend(wall_with_gap("x", -wz, (-wx, wx), (-door, door), 0, CEIL_H, wt, "wall"))
    boxes.extend(wall_with_gap("x", wz, (-wx, wx), (-door, door), 0, CEIL_H, wt, "wall"))
    # East and west faces: doors offset toward the far end, so entering mid from a lane
    # does not put you in the same place as the team coming from the other lane.
    boxes.extend(wall_with_gap("z", -wx, (-wz, wz), (2.0, 5.6), 0, CEIL_H, wt, "wall"))
    boxes.extend(wall_with_gap("z", wx, (-wz, wz), (-5.6, -2.0), 0, CEIL_H, wt, "wall"))

    # Roof, so the warehouse is genuinely enclosed and reads as interior space.
    boxes.append(box(0, CEIL_H + 0.15, 0, wx * 2 + wt, 0.3, wz * 2 + wt, "metalfloor"))

    # ── two galleries, one per team's half ────────────────────────────────────
    # A single balcony over the north half would hand whichever team spawns on that side a
    # permanent height advantage in the most important room on the map. Two mirrored
    # galleries with the middle open to the roof keeps the fight over height symmetrical.
    #
    # Staircase dimensions are constrained from three directions at once and it is worth
    # being explicit: risers must stay under MoveConfig.step_height (0.35) or nobody can
    # climb; treads must be deeper than the 0.8 m player collider, or the collider straddles
    # two steps and the step-up never fires; and the flight plus a body's width of landing
    # has to fit inside the hall. Nine steps of 0.32 × 0.85 is 7.65 m long.
    stair_steps = 9
    stair_rise = DECK_Y / stair_steps
    stair_run = 0.85
    # The galleries are only 4.5 m deep, and that depth is load-bearing: a staircase is
    # 7.65 m long, and if it runs under the *opposite* gallery the step-up probe (which
    # needs the player's height plus 0.35 m of clearance) hits the underside of the slab
    # and the whole flight becomes unclimbable from halfway up. Keeping the flights inside
    # the open middle avoids the problem entirely rather than fighting the clearance.
    deck_far = 9.0
    deck_near = 4.5
    deck_depth = deck_far - deck_near
    nav_mark = len(NAV_PATHS)
    gallery: List[Box] = []
    gallery.append(
        box(0, DECK_Y - SLAB_T / 2, -(deck_near + deck_depth / 2), wx * 2 - wt, SLAB_T,
            deck_depth, "metalfloor")
    )
    # Rail along the open edge, chest high — hold the angle without being fully exposed.
    # It spans only the middle; the ends stay clear for the staircase to arrive.
    gallery.append(box(0, DECK_Y + 0.55, -(deck_near - 0.15), 13.8, 1.1, 0.25, "railing"))
    gallery.extend(
        stairs_z(-8.6, -deck_near, 3.4, stair_steps, stair_rise, stair_run, +1, "metalfloor")
    )
    boxes.extend(gallery)
    boxes.extend(mirrored(gallery))
    mirror_nav_paths_since(nav_mark)

    # Ground-floor clutter: pillars break the straight line between opposite doors, so no
    # single angle covers the whole hall.
    for px in (-5.0, 5.0):
        boxes.append(box(px, CEIL_H / 2, -4.0, 0.9, CEIL_H, 0.9, "concrete"))
        boxes.append(box(px, CEIL_H / 2, 4.0, 0.9, CEIL_H, 0.9, "concrete"))
    # Keep clutter clear of x = ±8.6, where the staircases land — a 1.2 m crate at the foot
    # of a flight is an invisible wall, since the step-up only clears 0.35 m.
    hall_clutter: List[Box] = [
        crate(-2.4, 6.6, "crate", 1.6),
        crate(-4.4, 7.0),
        crate(2.6, 6.0, "metal", 1.6),
        crate(2.6, 6.0, "crate", 1.1, base=1.6),
    ]
    boxes.extend(hall_clutter)
    boxes.extend(mirrored(hall_clutter))

    # ══ FLANK LANES ═══════════════════════════════════════════════════════════
    # Raised platforms at x = ±21, connected to the ground at both ends.
    for sign in (-1.0, 1.0):
        px = sign * 21.0
        boxes.append(box(px, 0.6, 0.0, 10.0, 1.2, 11.0, "concrete"))
        # Hard cover facing mid; the outer side stays open so the platform can be flanked.
        boxes.append(box(px - sign * 4.6, 1.85, 0.0, 0.7, 1.3, 11.0, "concrete"))
        boxes.extend(stairs_z(px, 5.5, 5.0, 4, 0.3, 0.85, +1))
        boxes.extend(stairs_z(px, -5.5, 5.0, 4, 0.3, 0.85, -1))
        # A container on the platform: cover on cover, and something to break the sightline
        # straight down the lane. Its Z offset follows the sign too, or the two platforms
        # stop being mirror images of each other.
        boxes.append(box(px + sign * 2.6, 1.2 + 1.3, sign * 2.4, 3.4, 2.6, 2.4, "metal"))

    # ══ CONNECTORS ════════════════════════════════════════════════════════════
    # Cross-passages near each spawn, walled on the spawn side so they are corridors
    # rather than open ground.
    for sign in (-1.0, 1.0):
        cz = sign * 17.5
        boxes.append(box(0.0, 1.6, cz + sign * 3.2, 34.0, 3.2, 0.7, "brick"))
        # Gaps in that wall let you peek toward mid without committing.
        boxes.append(box(-8.0, 1.6, cz - sign * 2.0, 0.7, 3.2, 4.0, "brick"))
        boxes.append(box(8.0, 1.6, cz - sign * 2.0, 0.7, 3.2, 4.0, "brick"))

    # ══ ASYMMETRIC-LOOKING, SYMMETRIC-PLAYING COVER ═══════════════════════════
    half_layout: List[Box] = [
        # Approach from A spawn into mid.
        crate(-3.4, -12.5),
        crate(-3.4, -13.7, base=CRATE),
        crate(-2.2, -12.5),
        box(3.2, 0.75, -12.0, 3.6, 1.5, 1.0, "concrete"),
        crate(6.4, -14.6, "metal", 1.6),
        # West lane furniture.
        box(-15.0, 1.1, -8.0, 1.2, 2.2, 5.0, "pipes"),
        crate(-14.6, -14.0),
        crate(-15.8, -14.0),
        crate(-15.2, -14.0, base=CRATE),
        box(-24.5, 1.5, -9.0, 3.0, 3.0, 0.8, "brick"),
        # East lane furniture.
        box(15.5, 0.9, -6.5, 2.6, 1.8, 2.6, "crate"),
        crate(14.2, -16.0, "metal", 1.6),
        box(24.0, 1.25, -14.0, 4.0, 2.5, 0.8, "concrete"),
        # Spawn-side cover so you are not shot the instant you appear.
        box(-6.0, 1.0, -24.0, 5.0, 2.0, 0.8, "concrete"),
        crate(9.5, -24.0, "metal", 1.6),
    ]
    boxes.extend(half_layout)
    boxes.extend(mirrored(half_layout))

    # ── spawns ────────────────────────────────────────────────────────────────
    # Team A starts at -Z looking toward +Z (yaw = pi), team B the reverse. Spread across
    # the width so a single grenade-shaped sightline cannot cover the whole team.
    spawn_a = [
        {"p": [x, 0.05, -26.5], "yaw": round(math.pi, 4)}
        for x in (-13.0, -7.0, 0.0, 7.0, 13.0)
    ]
    spawn_b = [
        {"p": [x, 0.05, 26.5], "yaw": 0.0}
        for x in (-13.0, -7.0, 0.0, 7.0, 13.0)
    ]

    # Surface colours are read as-is by the renderer and multiplied by a near-white detail
    # texture, so what you write here is roughly what you see. They are deliberately in the
    # mid-to-light range: a competitive shooter needs players to be readable against the
    # background, and dark scenery hides them far more effectively than it looks moody.
    materials = {
        "floor": {"color": "#8d949d", "roughness": 0.95, "metalness": 0.02},
        "wall": {"color": "#b3a893", "roughness": 0.9, "metalness": 0.02},
        "brick": {"color": "#a9705a", "roughness": 0.92, "metalness": 0.02},
        "concrete": {"color": "#bdb4a4", "roughness": 0.88, "metalness": 0.03},
        "crate": {"color": "#c08b4e", "roughness": 0.8, "metalness": 0.02},
        "metal": {"color": "#9aa6b5", "roughness": 0.45, "metalness": 0.55},
        "metalfloor": {"color": "#8e97a3", "roughness": 0.55, "metalness": 0.5},
        "railing": {"color": "#7f8894", "roughness": 0.5, "metalness": 0.6},
        "pipes": {"color": "#8d9aa6", "roughness": 0.4, "metalness": 0.7},
    }

    lights = [
        # Warehouse interior — the roof blocks the sun, so this is the only light in there.
        {"p": [0.0, CEIL_H - 0.6, -4.5], "color": "#ffeccc", "intensity": 1.8, "distance": 26.0},
        {"p": [0.0, CEIL_H - 0.6, 4.5], "color": "#ffeccc", "intensity": 1.8, "distance": 26.0},
        {"p": [-7.5, DECK_Y - 0.5, -6.0], "color": "#ffe0b8", "intensity": 1.0, "distance": 16.0},
        {"p": [7.5, DECK_Y - 0.5, -6.0], "color": "#ffe0b8", "intensity": 1.0, "distance": 16.0},
        # Lanes and spawns.
        {"p": [-21.0, 7.0, -12.0], "color": "#dceaff", "intensity": 1.0, "distance": 50.0},
        {"p": [21.0, 7.0, 12.0], "color": "#dceaff", "intensity": 1.0, "distance": 50.0},
        {"p": [0.0, 8.0, -24.0], "color": "#ffe3c2", "intensity": 0.9, "distance": 50.0},
        {"p": [0.0, 8.0, 24.0], "color": "#ffe3c2", "intensity": 0.9, "distance": 50.0},
    ]

    return {
        "name": MAP_NAME,
        "version": 3,
        "bounds": [-HALF, -1.0, -HALF, HALF, WALL_H + 2.0, HALF],
        # Overcast daylight rather than dusk. The ambient term is what fills the insides
        # of the building and the shadow side of every crate.
        "ambient": "#93a1b5",
        "sky": ["#8fa3bb", "#c7d8ec"],
        # Fog starts beyond the longest sightline on the map, so it adds depth without
        # greying out targets you are trying to shoot.
        "fog": {"color": "#b9c8da", "near": 70.0, "far": 200.0},
        "materials": materials,
        "boxes": boxes,
        "spawns": {"A": spawn_a, "B": spawn_b},
        "lights": lights,
        "nav_paths": list(NAV_PATHS),
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
    print(f"  materials {', '.join(sorted(data['materials']))}")
    print("  next:   python -m app.scripts.gen_nav", MAP_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
