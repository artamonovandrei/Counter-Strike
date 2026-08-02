# path: backend/tests/test_map.py
"""Tests against the *shipped* map, not a synthetic one.

Everything else in the suite builds a small world so a failure points at the physics. These
tests do the opposite: they check that the level someone actually plays is not broken, which
is a different and surprisingly easy thing to get wrong.

Both of the bugs these were written for were authoring mistakes, invisible in code review
and obvious the moment a player walked into them:

* a staircase whose flight ran through the wall it was supposed to lead to, and
* a 1.2 m crate parked at the foot of another flight, which the 0.35 m step-up cannot clear,
  turning the only route to the upper floor into a dead end.

Neither broke a single unit test. Walking the routes catches both.
"""

from __future__ import annotations

import math

import pytest

from app.config import MOVE
from app.game.mathx import Vec3
from app.game.movement import step_movement
from app.game.nav import load_nav
from app.game.world import load_map_data, load_world
from app.protocol import K_FORWARD

DT = 1.0 / 60.0
MAP = "alley"


@pytest.fixture(scope="module")
def world():
    return load_world(MAP)


@pytest.fixture(scope="module")
def map_data():
    return load_map_data(MAP)


def player_fits(world, pos: Vec3) -> bool:
    box = (
        pos.x - MOVE.player_radius, pos.y + 0.05, pos.z - MOVE.player_radius,
        pos.x + MOVE.player_radius, pos.y + MOVE.player_height, pos.z + MOVE.player_radius,
    )
    return world.is_free(box)


# ── geometry sanity ───────────────────────────────────────────────────────────

def test_map_has_the_expected_shape(map_data):
    assert map_data["boxes"], "no geometry"
    assert map_data["spawns"]["A"] and map_data["spawns"]["B"]
    assert len(map_data["spawns"]["A"]) == len(map_data["spawns"]["B"]), "unfair spawn count"


def test_every_spawn_point_is_standable(world, map_data):
    for team, spawns in map_data["spawns"].items():
        for i, spawn in enumerate(spawns):
            pos = Vec3.from_seq(spawn["p"])
            assert player_fits(world, pos), f"team {team} spawn {i} at {spawn['p']} is inside geometry"


def test_spawns_are_not_visible_to_each_other(world, map_data):
    """No spawn may see an enemy spawn. Being shot before you can move is not a fight."""
    eye = MOVE.eye_height
    for a in map_data["spawns"]["A"]:
        pa = Vec3(a["p"][0], a["p"][1] + eye, a["p"][2])
        for b in map_data["spawns"]["B"]:
            pb = Vec3(b["p"][0], b["p"][1] + eye, b["p"][2])
            assert not world.line_of_sight(pa, pb), f"spawn {a['p']} can see {b['p']}"


def test_spawns_are_far_apart(map_data):
    for a in map_data["spawns"]["A"]:
        for b in map_data["spawns"]["B"]:
            d = math.dist(a["p"], b["p"])
            assert d > 30.0, f"spawns only {d:.1f} m apart"


def test_map_is_symmetric(map_data):
    """Every brush must have a partner mirrored through the origin.

    Rotational symmetry is the cheapest guarantee that neither team has an advantage, and
    it is very easy to break by nudging one crate.
    """
    def key(b):
        return (
            tuple(round(v, 3) for v in b["p"]),
            tuple(round(v, 3) for v in b["s"]),
            b["m"],
        )

    present = {key(b) for b in map_data["boxes"]}
    asymmetric = []
    for b in map_data["boxes"]:
        p = b["p"]
        mirror = (
            (round(-p[0], 3), round(p[1], 3), round(-p[2], 3)),
            tuple(round(v, 3) for v in b["s"]),
            b["m"],
        )
        if mirror not in present:
            asymmetric.append((b["m"], p))
    assert not asymmetric, f"{len(asymmetric)} brushes have no mirror partner: {asymmetric[:5]}"


# ── the routes actually work ──────────────────────────────────────────────────

def test_authored_routes_are_walkable(world, map_data):
    """Walk every staircase the map declares and check the player gets to the top.

    This is the test that would have caught both authoring bugs. It drives the real
    movement integrator, so anything that blocks the route — geometry in the way, a riser
    taller than the step-up, a flight that ends inside a wall — shows up as a player who
    stops climbing.
    """
    paths = map_data.get("nav_paths") or []
    assert paths, "the map declares no routes; stairs would be undiscoverable"

    # Routes must be mirrored along with the geometry they describe. Mirroring a structure
    # that contains a staircase without mirroring its route leaves one team's flight
    # invisible to the AI — which is exactly what happened.
    keys = {(tuple(p["a"]), tuple(p["b"])) for p in paths}
    for a, b in keys:
        mirror = ((-a[0], a[1], -a[2]), (-b[0], b[1], -b[2]))
        assert mirror in keys, f"route {a}->{b} has no mirrored counterpart"

    for path in paths:
        bottom = Vec3.from_seq(path["a"])
        top = Vec3.from_seq(path["b"])
        rise = top.y - bottom.y
        assert rise > 0.5, f"route {path} barely climbs"

        assert player_fits(world, bottom), f"bottom of route {path['note']} at {path['a']} is blocked"

        # Aim from the bottom toward the top and walk.
        delta = Vec3(top.x - bottom.x, 0.0, top.z - bottom.z)
        yaw = math.atan2(-delta.x, -delta.z)

        pos = bottom.copy()
        vel = Vec3()
        grounded = True
        peak = pos.y
        for _ in range(600):
            result = step_movement(world, pos, vel, yaw, K_FORWARD, DT, grounded)
            grounded = result.grounded
            peak = max(peak, pos.y)
            if peak >= top.y - 0.2:
                break

        assert peak >= top.y - 0.2, (
            f"route '{path['note']}' from {path['a']} to {path['b']} is not climbable — "
            f"got stuck at y={peak:.2f}, needed {top.y:.2f}. Something is blocking it."
        )


def test_nav_graph_covers_every_floor(world):
    """The waypoint graph must reach the upper floors, or bots never leave the ground."""
    nav = load_nav(MAP)
    assert len(nav) > 200, "nav graph is suspiciously small"

    heights = sorted({round(n.pos.y, 1) for n in nav.nodes})
    assert len(heights) >= 3, f"only {heights} — upper storeys are unreachable"
    assert max(heights) > 2.0, f"nothing above 2 m in the nav graph: {heights}"


def test_bots_can_path_between_floors():
    nav = load_nav(MAP)
    ground = [n for n in nav.nodes if n.pos.y < 0.5]
    upper = [n for n in nav.nodes if n.pos.y > 2.0]
    assert ground and upper

    import random

    rng = random.Random(4)
    reached = sum(
        1 for _ in range(15) if nav.find_path(rng.choice(ground).id, rng.choice(upper).id)
    )
    assert reached >= 12, f"only {reached}/15 paths from the ground to an upper floor"


def test_spawns_connect_to_the_nav_graph(world, map_data):
    """A spawn that isn't near a waypoint strands every bot that uses it."""
    nav = load_nav(MAP)
    for team, spawns in map_data["spawns"].items():
        for spawn in spawns:
            pos = Vec3.from_seq(spawn["p"])
            node = nav.nearest(pos)
            assert node is not None, f"team {team} spawn {spawn['p']} has no nearby waypoint"
            assert nav.node_pos(node).distance(pos) < 4.0, (
                f"team {team} spawn {spawn['p']} is {nav.node_pos(node).distance(pos):.1f} m "
                f"from the nearest waypoint"
            )
