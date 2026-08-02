# path: backend/app/game/nav.py
"""Waypoint graph and A* for the bots.

A waypoint graph rather than a true navmesh: the map is boxes on a flat floor, so a dense
graph of walkable points gives paths that are just as good and is a fraction of the code
and CPU. `app/scripts/gen_nav.py` generates it from the map geometry.
"""

from __future__ import annotations

import heapq
import json
import math
import random
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .mathx import Vec3
from .world import World, nav_path


class NavNode:
    __slots__ = ("id", "pos", "cover", "neighbors")

    def __init__(self, node_id: int, pos: Vec3, cover: float = 0.0):
        self.id = node_id
        self.pos = pos
        self.cover = cover
        self.neighbors: List[int] = []


class NavGraph:
    def __init__(self, nodes: List[NavNode], links: Sequence[Sequence[int]]):
        self.nodes = nodes
        for i, adj in enumerate(links):
            if i < len(nodes):
                nodes[i].neighbors = list(adj)
        self._cost_cache: Dict[Tuple[int, int], float] = {}
        # Coarse spatial bucket so nearest-node lookups don't scan every node.
        self._bucket: Dict[Tuple[int, int], List[int]] = {}
        self._bucket_size = 6.0
        for n in nodes:
            key = (int(n.pos.x // self._bucket_size), int(n.pos.z // self._bucket_size))
            self._bucket.setdefault(key, []).append(n.id)

    # ── lookups ───────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self.nodes)

    def nearest(self, pos: Vec3, max_radius: float = 24.0) -> Optional[int]:
        """Nearest node id, searching outward in bucket rings."""
        if not self.nodes:
            return None
        bs = self._bucket_size
        bx = int(pos.x // bs)
        bz = int(pos.z // bs)
        best_id = None
        best_d = float("inf")
        ring = 0
        max_ring = int(max_radius / bs) + 1
        while ring <= max_ring:
            found_any = False
            for dx in range(-ring, ring + 1):
                for dz in range(-ring, ring + 1):
                    # only the perimeter of the ring is new
                    if ring > 0 and abs(dx) != ring and abs(dz) != ring:
                        continue
                    for nid in self._bucket.get((bx + dx, bz + dz), ()):
                        d = self.nodes[nid].pos.distance_sq(pos)
                        if d < best_d:
                            best_d = d
                            best_id = nid
                            found_any = True
            # Stop one ring after the first hit: a closer node cannot hide further out.
            if best_id is not None and (found_any is False or ring > 0):
                break
            ring += 1
        return best_id

    def node_pos(self, node_id: int) -> Vec3:
        return self.nodes[node_id].pos

    def random_node(self, rng: random.Random) -> Optional[int]:
        if not self.nodes:
            return None
        return rng.randrange(len(self.nodes))

    def random_node_far_from(
        self, rng: random.Random, pos: Vec3, min_dist: float, attempts: int = 12
    ) -> Optional[int]:
        if not self.nodes:
            return None
        min_sq = min_dist * min_dist
        best = None
        best_d = -1.0
        for _ in range(attempts):
            nid = rng.randrange(len(self.nodes))
            d = self.nodes[nid].pos.distance_sq(pos)
            if d >= min_sq:
                return nid
            if d > best_d:
                best_d = d
                best = nid
        return best

    def best_cover_near(self, pos: Vec3, radius: float) -> Optional[int]:
        """Highest-cover node within ``radius``. Used when a bot retreats."""
        best = None
        best_score = -1.0
        r_sq = radius * radius
        for n in self.nodes:
            d = n.pos.distance_sq(pos)
            if d > r_sq:
                continue
            # Prefer high cover, mildly prefer closer.
            score = n.cover - 0.02 * math.sqrt(d)
            if score > best_score:
                best_score = score
                best = n.id
        return best

    # ── pathfinding ───────────────────────────────────────────────────────────

    def find_path(self, start: int, goal: int, max_expansions: int = 4000) -> List[int]:
        """A* over the graph. Returns [start, ..., goal], or [] if unreachable.

        Euclidean distance is an admissible heuristic here because edge costs are the
        straight-line distances between linked nodes.
        """
        if start == goal:
            return [start]
        if not (0 <= start < len(self.nodes)) or not (0 <= goal < len(self.nodes)):
            return []

        nodes = self.nodes
        goal_pos = nodes[goal].pos

        open_heap: List[Tuple[float, int]] = [(0.0, start)]
        came: Dict[int, int] = {}
        g_score: Dict[int, float] = {start: 0.0}
        closed: set = set()
        expansions = 0

        while open_heap:
            _, current = heapq.heappop(open_heap)
            if current in closed:
                continue
            if current == goal:
                return self._reconstruct(came, current)
            closed.add(current)
            expansions += 1
            if expansions > max_expansions:
                break

            cur_pos = nodes[current].pos
            cur_g = g_score[current]
            for nb in nodes[current].neighbors:
                if nb in closed:
                    continue
                tentative = cur_g + cur_pos.distance(nodes[nb].pos)
                if tentative < g_score.get(nb, float("inf")):
                    g_score[nb] = tentative
                    came[nb] = current
                    f = tentative + nodes[nb].pos.distance(goal_pos)
                    heapq.heappush(open_heap, (f, nb))

        return []

    @staticmethod
    def _reconstruct(came: Dict[int, int], current: int) -> List[int]:
        path = [current]
        while current in came:
            current = came[current]
            path.append(current)
        path.reverse()
        return path

    def path_positions(self, path: Sequence[int]) -> List[Vec3]:
        return [self.nodes[i].pos for i in path]

    def smooth_path(self, world: World, path: Sequence[int], eye: float = 0.9) -> List[int]:
        """Drop intermediate nodes that are redundant given clear line of sight.

        Without this, bots visibly zig-zag between graph nodes on open ground.
        """
        if len(path) <= 2:
            return list(path)
        out = [path[0]]
        i = 0
        while i < len(path) - 1:
            # Reach as far ahead as line of sight allows.
            j = len(path) - 1
            while j > i + 1:
                a = self.nodes[path[i]].pos
                b = self.nodes[path[j]].pos
                if world.line_of_sight(Vec3(a.x, a.y + eye, a.z), Vec3(b.x, b.y + eye, b.z)):
                    break
                j -= 1
            out.append(path[j])
            i = j
        return out


def build_from_data(data: dict) -> NavGraph:
    nodes = [
        NavNode(int(n["id"]), Vec3.from_seq(n["p"]), float(n.get("cover", 0.0)))
        for n in data.get("nodes", [])
    ]
    links = data.get("links", [])
    return NavGraph(nodes, links)


_nav_cache: Dict[str, NavGraph] = {}


def load_nav(map_name: str) -> NavGraph:
    """Load (and cache) the waypoint graph for a map. Missing file → empty graph.

    An empty graph is not fatal: bots fall back to direct steering, they just navigate
    badly. The room logs a loud warning instead of refusing to start.
    """
    cached = _nav_cache.get(map_name)
    if cached is not None:
        return cached
    path: Path = nav_path(map_name)
    if not path.exists():
        graph = NavGraph([], [])
    else:
        with path.open("r", encoding="utf-8") as fh:
            graph = build_from_data(json.load(fh))
    _nav_cache[map_name] = graph
    return graph
