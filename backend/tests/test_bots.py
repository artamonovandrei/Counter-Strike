# path: backend/tests/test_bots.py
"""Pathfinding and bot behaviour."""

from __future__ import annotations

import math
import random

import pytest

from app.config import BOT_TUNING
from app.game.bots import BotBrain, BotManager, BotState
from app.game.mathx import Vec3
from app.game.nav import NavGraph, NavNode, build_from_data
from app.protocol import K_BACK, K_FIRE, K_FORWARD, K_LEFT, K_RELOAD, K_RIGHT
from conftest import make_entity, make_world


def grid_graph(n: int = 6, spacing: float = 2.0) -> NavGraph:
    """A flat n×n lattice with 4-way links — costs are trivial to reason about."""
    nodes = []
    index = {}
    for i in range(n):
        for j in range(n):
            nid = len(nodes)
            index[(i, j)] = nid
            nodes.append(NavNode(nid, Vec3(i * spacing, 0.0, j * spacing)))
    links = [[] for _ in nodes]
    for (i, j), nid in index.items():
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nb = index.get((i + di, j + dj))
            if nb is not None:
                links[nid].append(nb)
    return NavGraph(nodes, links)


# ── A* ────────────────────────────────────────────────────────────────────────

def test_path_between_adjacent_nodes():
    g = grid_graph()
    path = g.find_path(0, 1)
    assert path == [0, 1]


def test_path_to_self_is_trivial():
    g = grid_graph()
    assert g.find_path(5, 5) == [5]


def test_path_is_manhattan_optimal_on_a_lattice():
    g = grid_graph(n=6, spacing=2.0)
    start = 0                       # (0,0)
    goal = len(g.nodes) - 1         # (5,5)
    path = g.find_path(start, goal)
    assert path[0] == start and path[-1] == goal
    # 4-connected lattice: the shortest route is 10 steps -> 11 nodes.
    assert len(path) == 11


def test_unreachable_goal_returns_empty_path():
    g = grid_graph(n=3)
    orphan = NavNode(len(g.nodes), Vec3(100.0, 0.0, 100.0))
    g.nodes.append(orphan)
    g._bucket.setdefault((16, 16), []).append(orphan.id)
    assert g.find_path(0, orphan.id) == []


def test_path_out_of_range_indices_is_empty():
    g = grid_graph()
    assert g.find_path(0, 9999) == []
    assert g.find_path(-1, 0) == []


def test_nearest_finds_the_closest_node():
    g = grid_graph(n=5, spacing=2.0)
    nid = g.nearest(Vec3(4.1, 0.0, 6.2))
    assert nid is not None
    assert g.node_pos(nid).distance(Vec3(4.0, 0.0, 6.0)) < 0.5


def test_nearest_on_empty_graph_is_none():
    assert NavGraph([], []).nearest(Vec3()) is None


def test_smoothing_removes_redundant_nodes_in_open_space():
    world = make_world()
    g = grid_graph(n=6, spacing=2.0)
    raw = g.find_path(0, 5)  # straight line along one edge of the lattice
    assert len(raw) == 6
    smoothed = g.smooth_path(world, raw)
    assert len(smoothed) < len(raw)
    assert smoothed[0] == raw[0] and smoothed[-1] == raw[-1]


def test_smoothing_keeps_corners_when_geometry_blocks():
    # A wall down the middle forces the path to stay bent.
    world = make_world([{"p": [4.0, 3.0, 5.0], "s": [0.6, 6.0, 8.0], "m": "wall"}])
    g = grid_graph(n=6, spacing=2.0)
    raw = [0, 6, 12, 13, 14]
    smoothed = g.smooth_path(world, raw)
    assert smoothed[0] == 0 and smoothed[-1] == 14


def test_cover_scoring_prefers_enclosed_nodes():
    nodes = [
        NavNode(0, Vec3(0.0, 0.0, 0.0), cover=0.1),
        NavNode(1, Vec3(2.0, 0.0, 0.0), cover=0.9),
        NavNode(2, Vec3(30.0, 0.0, 0.0), cover=1.0),
    ]
    g = NavGraph(nodes, [[1], [0], []])
    best = g.best_cover_near(Vec3(0.0, 0.0, 0.0), radius=5.0)
    assert best == 1, "should pick high cover within range, not the far-away node"


def test_build_from_data_roundtrip():
    data = {
        "map": "x",
        "nodes": [
            {"id": 0, "p": [0, 0, 0], "cover": 0.5},
            {"id": 1, "p": [1, 0, 0], "cover": 0.0},
        ],
        "links": [[1], [0]],
    }
    g = build_from_data(data)
    assert len(g) == 2
    assert g.nodes[0].neighbors == [1]
    assert g.nodes[0].cover == 0.5


# ── brain behaviour ───────────────────────────────────────────────────────────

def make_brain(world, graph, ent, difficulty="normal"):
    brain = BotBrain(ent, world, graph, BOT_TUNING[difficulty], seed=1)
    ent.brain = brain
    brain.reset_on_spawn(0.0)
    return brain


def test_patrolling_bot_produces_movement_keys():
    world = make_world()
    graph = grid_graph(n=6, spacing=2.0)
    ent = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    brain = make_brain(world, graph, ent)

    keys = 0
    for i in range(60):
        keys, yaw, pitch, _ = brain.update(i / 60.0, 1 / 60.0, [], 0.1)
        if keys & (K_FORWARD | K_BACK | K_LEFT | K_RIGHT):
            break
    assert keys & (K_FORWARD | K_BACK | K_LEFT | K_RIGHT), "a patrolling bot must try to move"
    assert brain.state is BotState.PATROL


def test_bot_engages_a_visible_enemy():
    world = make_world()
    graph = grid_graph(n=6, spacing=2.0)
    bot = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    enemy = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))
    brain = make_brain(world, graph, bot)

    for i in range(120):
        brain.update(i / 60.0, 1 / 60.0, [enemy], 0.05)
    assert brain.state is BotState.ENGAGE
    assert brain.target is enemy
    # It should have turned to face the enemy (which is at -Z, i.e. yaw ~ 0).
    assert abs(math.atan2(math.sin(bot.yaw), math.cos(bot.yaw))) < 0.4


def test_bot_does_not_see_through_walls():
    world = make_world([{"p": [0.0, 3.0, -5.0], "s": [12.0, 6.0, 1.0], "m": "wall"}])
    graph = grid_graph(n=6, spacing=2.0)
    bot = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    enemy = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))
    brain = make_brain(world, graph, bot)

    for i in range(60):
        brain.update(i / 60.0, 1 / 60.0, [enemy], 0.05)
    assert brain.target is None
    assert brain.state is not BotState.ENGAGE


def test_bot_eventually_fires_at_an_exposed_enemy():
    world = make_world()
    graph = grid_graph(n=6, spacing=2.0)
    bot = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    enemy = make_entity(2, "B", Vec3(0.0, 0.0, -8.0))
    brain = make_brain(world, graph, bot, difficulty="expert")

    fired = False
    for i in range(240):
        keys, _, _, _ = brain.update(i / 60.0, 1 / 60.0, [enemy], 0.05)
        if keys & K_FIRE:
            fired = True
            break
    assert fired, "an expert bot with a clear shot must pull the trigger"


def test_bot_respects_reaction_time():
    world = make_world()
    graph = grid_graph(n=6, spacing=2.0)
    bot = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    enemy = make_entity(2, "B", Vec3(0.0, 0.0, -8.0))
    tuning = BOT_TUNING["easy"]
    brain = make_brain(world, graph, bot, difficulty="easy")

    # Within the reaction window, no shot may be fired.
    ticks = int(tuning.reaction_time * 60 * 0.5)
    for i in range(max(1, ticks)):
        keys, _, _, _ = brain.update(i / 60.0, 1 / 60.0, [enemy], 0.05)
        assert not (keys & K_FIRE)


def test_low_health_bot_retreats():
    world = make_world()
    graph = grid_graph(n=8, spacing=2.0)
    bot = make_entity(1, "A", Vec3(6.0, 0.0, 6.0))
    enemy = make_entity(2, "B", Vec3(6.0, 0.0, -4.0))
    brain = make_brain(world, graph, bot)
    bot.health = BOT_TUNING["normal"].retreat_health - 5

    for i in range(60):
        brain.update(i / 60.0, 1 / 60.0, [enemy], 0.05)
    assert brain.state is BotState.RETREAT


def test_bot_reloads_when_magazine_is_empty():
    world = make_world()
    graph = grid_graph(n=6, spacing=2.0)
    bot = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    brain = make_brain(world, graph, bot)
    bot.arsenal.slots["rifle"].ammo = 0

    keys = 0
    for i in range(30):
        keys, _, _, _ = brain.update(i / 60.0, 1 / 60.0, [], 0.05)
        if keys & K_RELOAD:
            break
    assert keys & K_RELOAD


def test_bot_switches_off_a_dry_weapon():
    world = make_world()
    graph = grid_graph(n=6, spacing=2.0)
    bot = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    brain = make_brain(world, graph, bot)
    bot.arsenal.slots["rifle"].ammo = 0
    bot.arsenal.slots["rifle"].reserve = 0

    _, _, _, slot = brain.update(0.0, 1 / 60.0, [], 0.05)
    assert slot == 2, "should reach for the pistol"

    bot.arsenal.slots["pistol"].ammo = 0
    bot.arsenal.slots["pistol"].reserve = 0
    _, _, _, slot = brain.update(0.1, 1 / 60.0, [], 0.05)
    assert slot == 3, "out of bullets entirely -> knife"


def test_dead_bot_produces_no_input():
    world = make_world()
    graph = grid_graph()
    bot = make_entity(1, "A", Vec3(0.0, 0.0, 0.0))
    brain = make_brain(world, graph, bot)
    bot.alive = False
    keys, _, _, slot = brain.update(1.0, 1 / 60.0, [], 0.05)
    assert keys == 0 and slot == 0


def test_manager_queues_one_input_per_bot_per_tick():
    world = make_world()
    graph = grid_graph()
    manager = BotManager(world, graph, BOT_TUNING["normal"], logic_hz=10, seed=3)
    bots = [make_entity(i, "A" if i % 2 else "B", Vec3(float(i), 0.0, 0.0), is_bot=True) for i in (1, 2, 3)]
    for b in bots:
        manager.attach(b)
        b.brain.reset_on_spawn(0.0)

    manager.update(0.0, 1 / 60.0, bots, bots)
    assert all(len(b.input_queue) == 1 for b in bots)
    manager.update(1 / 60.0, 1 / 60.0, bots, bots)
    assert all(len(b.input_queue) == 2 for b in bots)


def test_bot_think_is_staggered_across_instances():
    """Two bots created together must not think on the same tick, or the AI cost spikes."""
    world = make_world()
    graph = grid_graph()
    manager = BotManager(world, graph, BOT_TUNING["normal"], logic_hz=10, seed=11)
    a = make_entity(1, "A", Vec3(0.0, 0.0, 0.0), is_bot=True)
    b = make_entity(2, "A", Vec3(2.0, 0.0, 0.0), is_bot=True)
    manager.attach(a)
    manager.attach(b)
    assert a.brain.next_think != b.brain.next_think
