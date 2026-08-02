# path: backend/app/scripts/gen_nav.py
"""Generate the bot waypoint graph for a map.

Method: sample a grid over the arena, find *every* standable height in each column, keep
the ones where a player collider actually fits, then link neighbours that a player could
genuinely walk between. Finally, score each node for "cover" so retreating bots have
somewhere sensible to go.

── Why the column scan ───────────────────────────────────────────────────────────────
The first version raycast straight down from the sky and took the first surface it hit.
That works for a flat map and breaks completely the moment anything has a roof: on the
current map every sample inside the warehouse landed on its *roof*, so the ground floor
and the balcony got no nodes at all and bots simply never entered the middle of the map.
Scanning the whole column instead finds all of them, at the cost of a fraction of a
second in an offline script.

The other expensive correctness detail is the link test. Line of sight alone produces
links that cut diagonally past the corner of a crate — a bot following one grinds along
the corner and looks broken. So every candidate link is also swept with the player's
collision box at a few intermediate points, which is what ``CLEARANCE`` controls.

Run: ``python -m app.scripts.gen_nav alley``
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from ..config import MOVE
from ..game.mathx import Vec3
from ..game.world import World, load_map_data, nav_path

# Spacing trades bot pathing detail against A* cost, and A* cost is paid every time a bot
# re-plans — on the room's tick budget. Sampling at 1.2 m produced a 2200-node graph whose
# worst-case search pushed a tick to 15 ms against a 16.7 ms budget; 1.8 m keeps the graph
# under a thousand nodes and the worst tick in single digits.
#
# Density used to matter for a second reason — staircases could only be linked if samples
# landed close enough together to stay within MAX_LINK_RISE — but authored nav_paths handle
# stairs now, so the sampler no longer has to be fine enough to discover them.
SPACING = 1.8          # metres between grid samples
SCAN_STEP = 0.25       # vertical resolution of the column scan
LEVEL_SEPARATION = 0.8 # collapse standable heights closer together than this
MAX_LINK_DIST = 4.2    # metres; longer links are handled by path smoothing at runtime
MAX_LINK_RISE = 0.8    # metres of height a single link may span (stairs, not cliffs)
SWEEP_RISE_TOLERANCE = 0.45  # how far the real floor may sit from the interpolated line
CLEARANCE = 0.06       # shrink the sweep box slightly so doorways aren't rejected
SWEEP_SAMPLES = 4
COVER_RAYS = 12
COVER_RANGE = 3.5
EYE = 0.95             # height used for link line-of-sight


def _fits(world: World, x: float, y: float, z: float, radius: float, height: float) -> bool:
    """Can a player stand with their feet at (x, y, z)?"""
    box = (x - radius, y + 0.05, z - radius, x + radius, y + height, z + radius)
    return world.is_free(box)


def _supported(world: World, x: float, y: float, z: float, radius: float) -> bool:
    """Is there ground immediately under (x, y, z)?"""
    box = (x - radius * 0.8, y - 0.12, z - radius * 0.8, x + radius * 0.8, y - 0.01, z + radius * 0.8)
    return not world.is_free(box)


def standable_heights(world: World, x: float, z: float, radius: float, height: float) -> List[float]:
    """Every height in this column where a player could stand, highest first.

    A vertical scan rather than a raycast chain: raycasting down repeatedly has to handle
    starting *inside* a brush, which is fiddly to get right and easy to turn into an
    infinite loop. Stepping down and testing "fits here, and something solid is directly
    below" is slower and obviously correct.
    """
    top = world.bounds[4]
    bottom = world.bounds[1]
    out: List[float] = []
    y = top
    while y > bottom:
        if _supported(world, x, y, z, radius) and _fits(world, x, y, z, radius, height):
            if not out or (out[-1] - y) > LEVEL_SEPARATION:
                out.append(y)
        y -= SCAN_STEP
    return out


def sample_nodes(world: World) -> List[Tuple[Vec3, float]]:
    """Grid-sample walkable positions across every floor. Returns (position, cover) pairs."""
    minx, _, minz, maxx, _, maxz = world.bounds
    radius = MOVE.player_radius - CLEARANCE
    height = MOVE.player_height

    nodes: List[Tuple[Vec3, float]] = []
    x = minx + SPACING
    while x < maxx:
        z = minz + SPACING
        while z < maxz:
            for y in standable_heights(world, x, z, radius, height):
                pos = Vec3(x, y, z)
                nodes.append((pos, cover_score(world, pos)))
            z += SPACING
        x += SPACING
    return nodes


def cover_score(world: World, pos: Vec3) -> float:
    """Fraction of horizontal directions blocked within ``COVER_RANGE``.

    A node in the open scores ~0; one tucked behind a crate against a wall scores high.
    Bots use this when retreating, which is the difference between backing into cover and
    backing into the middle of a lane.
    """
    origin = Vec3(pos.x, pos.y + EYE, pos.z)
    blocked = 0
    for i in range(COVER_RAYS):
        a = (i / COVER_RAYS) * math.tau
        d = Vec3(math.cos(a), 0.0, math.sin(a))
        if world.raycast(origin, d, COVER_RANGE) is not None:
            blocked += 1
    return round(blocked / COVER_RAYS, 3)


def walkable_between(world: World, a: Vec3, b: Vec3) -> bool:
    """True when a player could actually walk the straight line from a to b."""
    if abs(b.y - a.y) > MAX_LINK_RISE:
        return False
    eye_a = Vec3(a.x, a.y + EYE, a.z)
    eye_b = Vec3(b.x, b.y + EYE, b.z)
    if not world.line_of_sight(eye_a, eye_b):
        return False

    radius = MOVE.player_radius - CLEARANCE
    height = MOVE.player_height
    for i in range(1, SWEEP_SAMPLES + 1):
        t = i / (SWEEP_SAMPLES + 1)
        mid = a.lerp(b, t)
        # Find the floor closest to where the player would be at this point, rather than
        # the topmost one — otherwise a link on the ground floor is validated against the
        # roof three metres above it.
        floor = _nearest_floor(world, mid.x, mid.z, mid.y, radius, height)
        if floor is None:
            return False
        # The straight line between two stair nodes runs slightly above or below the
        # actual treads; allow one riser of slack, but no more, or links start jumping
        # between floors through solid geometry.
        if abs(floor - mid.y) > SWEEP_RISE_TOLERANCE:
            return False
        if not _fits(world, mid.x, floor, mid.z, radius, height):
            return False
    return True


def _nearest_floor(
    world: World, x: float, z: float, y_ref: float, radius: float, height: float
) -> Optional[float]:
    best: Optional[float] = None
    for y in standable_heights(world, x, z, radius, height):
        if best is None or abs(y - y_ref) < abs(best - y_ref):
            best = y
    return best


def add_nav_paths(
    world: World, data: dict, nodes: List[Tuple[Vec3, float]], links: List[List[int]]
) -> int:
    """Insert authored routes (stairs) into the graph and stitch them to the sampled nodes.

    These are trusted: the map generator placed the staircase, so it knows the route
    exists, and no amount of collider sampling is going to rediscover it reliably. Nodes
    are laid along the polyline and chained, then each end is welded to whatever sampled
    nodes are nearby at a compatible height.

    Returns the number of nodes added.
    """
    paths = data.get("nav_paths") or []
    if not paths:
        return 0

    added = 0
    for path in paths:
        a = Vec3.from_seq(path["a"])
        b = Vec3.from_seq(path["b"])
        length = a.distance(b)
        if length < 0.2:
            continue

        steps = max(2, int(math.ceil(length / 0.9)))
        chain: List[int] = []
        for i in range(steps + 1):
            t = i / steps
            pos = a.lerp(b, t)
            nodes.append((pos, 0.35))  # stairs are partial cover by nature
            links.append([])
            idx = len(nodes) - 1
            chain.append(idx)
            added += 1

        for i in range(len(chain) - 1):
            links[chain[i]].append(chain[i + 1])
            links[chain[i + 1]].append(chain[i])

        # Weld both ends into the surrounding graph. Only the ends: welding the middle of
        # a flight to the floor beside it would let bots walk through the staircase.
        for end in (chain[0], chain[-1]):
            ep = nodes[end][0]
            for j, (q, _) in enumerate(nodes):
                if j == end or j in chain:
                    continue
                if abs(q.y - ep.y) > 0.6:
                    continue
                if ep.distance_xz(q) > 2.4:
                    continue
                if j not in links[end]:
                    links[end].append(j)
                    links[j].append(end)
    return added


def build_links(world: World, nodes: Sequence[Tuple[Vec3, float]]) -> List[List[int]]:
    """Link nearby nodes, using a spatial hash so this stays O(n) rather than O(n²)."""
    cell = MAX_LINK_DIST
    buckets: Dict[Tuple[int, int, int], List[int]] = {}
    for i, (p, _) in enumerate(nodes):
        # Bucket by height too, or a node on the balcony is compared against every node on
        # the floor below it.
        key = (int(p.x // cell), int(p.y // cell), int(p.z // cell))
        buckets.setdefault(key, []).append(i)

    links: List[List[int]] = [[] for _ in nodes]
    max_sq = MAX_LINK_DIST * MAX_LINK_DIST

    for i, (p, _) in enumerate(nodes):
        bx, by, bz = int(p.x // cell), int(p.y // cell), int(p.z // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in buckets.get((bx + dx, by + dy, bz + dz), ()):
                        if j <= i:
                            continue
                        q = nodes[j][0]
                        if p.distance_sq(q) > max_sq:
                            continue
                        if walkable_between(world, p, q):
                            links[i].append(j)
                            links[j].append(i)
    return links


def largest_component(links: Sequence[Sequence[int]]) -> List[int]:
    """Node indices of the biggest connected component.

    Isolated pockets (the warehouse roof, a node on top of a crate) are dropped: a bot
    that paths into one has no way out and will stand there for the rest of the round.
    """
    seen = [False] * len(links)
    best: List[int] = []
    for start in range(len(links)):
        if seen[start]:
            continue
        stack = [start]
        seen[start] = True
        comp = []
        while stack:
            n = stack.pop()
            comp.append(n)
            for nb in links[n]:
                if not seen[nb]:
                    seen[nb] = True
                    stack.append(nb)
        if len(comp) > len(best):
            best = comp
    return sorted(best)


def generate(map_name: str) -> dict:
    data = load_map_data(map_name)
    world = World(data)
    print(world.summary())

    raw_nodes = sample_nodes(world)
    levels = sorted({round(p.y, 1) for p, _ in raw_nodes})
    print(f"sampled {len(raw_nodes)} walkable points at {SPACING} m spacing")
    print(f"  distinct floor heights: {levels}")

    links = build_links(world, raw_nodes)

    stair_nodes = add_nav_paths(world, data, raw_nodes, links)
    if stair_nodes:
        print(f"added {stair_nodes} nodes from {len(data.get('nav_paths', []))} authored routes")
    keep = largest_component(links)
    dropped = len(raw_nodes) - len(keep)
    if dropped:
        print(f"dropped {dropped} nodes not in the main component (roofs, isolated ledges)")

    remap = {old: new for new, old in enumerate(keep)}
    nodes_out = []
    links_out = []
    for old in keep:
        pos, cover = raw_nodes[old]
        nodes_out.append(
            {"id": remap[old], "p": [round(pos.x, 3), round(pos.y, 3), round(pos.z, 3)], "cover": cover}
        )
        links_out.append(sorted(remap[n] for n in links[old] if n in remap))

    kept_levels: Dict[float, int] = {}
    for n in nodes_out:
        kept_levels[round(n["p"][1], 1)] = kept_levels.get(round(n["p"][1], 1), 0) + 1
    total_links = sum(len(l) for l in links_out)
    avg = total_links / len(links_out) if links_out else 0.0
    print(f"graph: {len(nodes_out)} nodes, {total_links // 2} edges, {avg:.1f} avg degree")
    print(f"  nodes per floor height: {dict(sorted(kept_levels.items()))}")
    if nodes_out and avg < 2.0:
        print("WARNING: very sparse graph — bots will path badly. Lower SPACING or CLEARANCE.")
    if len(kept_levels) < 2:
        print("NOTE: only one floor height survived — upper storeys may be unreachable.")

    return {"map": map_name, "nodes": nodes_out, "links": links_out}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a bot waypoint graph.")
    parser.add_argument("map", nargs="?", default="alley")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    data = generate(args.map)
    out: Path = args.out or nav_path(args.map)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
        fh.write("\n")
    print(f"wrote {out} — {out.stat().st_size / 1024.0:.1f} kB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
