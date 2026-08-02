# path: backend/app/scripts/gen_nav.py
"""Generate the bot waypoint graph for a map.

Method: sample a grid over the arena, drop each sample onto the floor, keep the ones where
a player collider actually fits, then link neighbours that a player could genuinely walk
between. Finally, score each node for "cover" so retreating bots have somewhere sensible
to go.

The expensive correctness detail is the link test. Line of sight alone produces links that
cut diagonally past the corner of a crate — a bot following one grinds along the corner
and looks broken. So every candidate link is also swept with the player's collision box at
a few intermediate points, which is what ``CLEARANCE`` controls.

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

SPACING = 1.8          # metres between grid samples
MAX_LINK_DIST = 4.2    # metres; longer links are handled by path smoothing at runtime
MAX_LINK_RISE = 0.45   # metres; above this a player can't walk up, only jump
CLEARANCE = 0.06       # shrink the sweep box slightly so doorways aren't rejected
SWEEP_SAMPLES = 4
COVER_RAYS = 12
COVER_RANGE = 3.5
EYE = 0.95             # height used for link line-of-sight


def _fits(world: World, pos: Vec3, radius: float, height: float) -> bool:
    box = (
        pos.x - radius, pos.y + 0.05, pos.z - radius,
        pos.x + radius, pos.y + height, pos.z + radius,
    )
    return world.is_free(box)


def sample_nodes(world: World) -> List[Tuple[Vec3, float]]:
    """Grid-sample walkable positions. Returns (position, cover) pairs."""
    minx, _, minz, maxx, _, maxz = world.bounds
    radius = MOVE.player_radius - CLEARANCE
    height = MOVE.player_height

    nodes: List[Tuple[Vec3, float]] = []
    x = minx + SPACING
    while x < maxx:
        z = minz + SPACING
        while z < maxz:
            probe = Vec3(x, world.bounds[4] - 0.5, z)
            floor = world.drop_to_floor(probe, radius, height, max_drop=40.0)
            if floor is not None and _fits(world, floor, radius, height):
                nodes.append((floor, cover_score(world, floor)))
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
        # Allow the floor to change a little between samples (stairs).
        floor = world.drop_to_floor(Vec3(mid.x, mid.y + 0.6, mid.z), radius, height, max_drop=1.2)
        if floor is None:
            return False
        if not _fits(world, floor, radius, height):
            return False
    return True


def build_links(world: World, nodes: Sequence[Tuple[Vec3, float]]) -> List[List[int]]:
    """Link nearby nodes, using a spatial hash so this stays O(n) rather than O(n²)."""
    cell = MAX_LINK_DIST
    buckets: Dict[Tuple[int, int], List[int]] = {}
    for i, (p, _) in enumerate(nodes):
        buckets.setdefault((int(p.x // cell), int(p.z // cell)), []).append(i)

    links: List[List[int]] = [[] for _ in nodes]
    max_sq = MAX_LINK_DIST * MAX_LINK_DIST

    for i, (p, _) in enumerate(nodes):
        bx, bz = int(p.x // cell), int(p.z // cell)
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for j in buckets.get((bx + dx, bz + dz), ()):
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

    Isolated pockets (a node on top of a crate, say) are dropped: a bot that paths into
    one has no way out and will stand there for the rest of the round.
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
    world = World(load_map_data(map_name))
    print(world.summary())

    raw_nodes = sample_nodes(world)
    print(f"sampled {len(raw_nodes)} walkable points at {SPACING} m spacing")

    links = build_links(world, raw_nodes)
    keep = largest_component(links)
    dropped = len(raw_nodes) - len(keep)
    if dropped:
        print(f"dropped {dropped} nodes not in the main component")

    remap = {old: new for new, old in enumerate(keep)}
    nodes_out = []
    links_out = []
    for old in keep:
        pos, cover = raw_nodes[old]
        nodes_out.append(
            {"id": remap[old], "p": [round(pos.x, 3), round(pos.y, 3), round(pos.z, 3)], "cover": cover}
        )
        links_out.append(sorted(remap[n] for n in links[old] if n in remap))

    total_links = sum(len(l) for l in links_out)
    avg = total_links / len(links_out) if links_out else 0.0
    print(f"graph: {len(nodes_out)} nodes, {total_links // 2} edges, {avg:.1f} avg degree")
    if nodes_out and avg < 2.0:
        print("WARNING: very sparse graph — bots will path badly. Lower SPACING or CLEARANCE.")

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
