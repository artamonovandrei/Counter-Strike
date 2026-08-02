# path: backend/tests/test_combat.py
"""Raycasting, hit classification, damage model and lag compensation."""

from __future__ import annotations

import math

import pytest

from app.config import MOVE, WEAPONS
from app.game.combat import can_see, melee_targets, resolve_shot, rewind_seconds
from app.game.entities import Entity
from app.game.mathx import Vec3, angles_to_dir
from app.game.weapons import Arsenal, damage_at_range
from conftest import make_entity, make_world

RIFLE = WEAPONS["rifle"]
PISTOL = WEAPONS["pistol"]
KNIFE = WEAPONS["knife"]


def shooter_at(x, z, yaw, team="A", eid=1):
    ent = make_entity(eid, team, Vec3(x, 0.0, z))
    ent.yaw = yaw
    return ent


def aim_at(shooter: Entity, target_point: Vec3) -> Vec3:
    return (target_point - shooter.eye_pos()).normalized()


# ── world raycasting ──────────────────────────────────────────────────────────

def test_raycast_hits_wall_at_expected_distance():
    w = make_world([{"p": [0.0, 3.0, -10.0], "s": [10.0, 6.0, 1.0], "m": "wall"}])
    hit = w.raycast(Vec3(0.0, 1.6, 0.0), Vec3(0.0, 0.0, -1.0), 50.0)
    assert hit is not None
    assert hit.t == pytest.approx(9.5, abs=0.01)
    assert hit.material == "wall"
    assert hit.normal.z == pytest.approx(1.0)


def test_raycast_misses_when_nothing_in_the_way():
    w = make_world()
    assert w.raycast(Vec3(0.0, 3.0, 0.0), Vec3(0.0, 1.0, 0.0), 5.0) is None


def test_line_of_sight_is_blocked_by_geometry():
    w = make_world([{"p": [0.0, 3.0, 0.0], "s": [4.0, 6.0, 1.0], "m": "wall"}])
    assert not w.line_of_sight(Vec3(0.0, 1.6, -5.0), Vec3(0.0, 1.6, 5.0))
    assert w.line_of_sight(Vec3(10.0, 1.6, -5.0), Vec3(10.0, 1.6, 5.0))


# ── hit classification ────────────────────────────────────────────────────────

def test_body_shot_deals_base_damage_at_close_range():
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0)
    t = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))
    direction = aim_at(s, Vec3(0.0, 1.0, -10.0))

    res = resolve_shot(w, s, [s, t], RIFLE, s.eye_pos(), direction, now=1.0)
    assert res.victim is t
    assert not res.headshot
    assert res.damage == int(round(RIFLE.damage))


def test_headshot_applies_multiplier():
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0)
    t = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))
    head_y = MOVE.head_min + 0.15
    direction = aim_at(s, Vec3(0.0, head_y, -10.0))

    res = resolve_shot(w, s, [s, t], RIFLE, s.eye_pos(), direction, now=1.0)
    assert res.victim is t
    assert res.headshot
    assert res.damage == pytest.approx(RIFLE.damage * RIFLE.headshot_mult, abs=1.0)


def test_wall_between_shooter_and_target_blocks_the_shot():
    w = make_world([{"p": [0.0, 3.0, -5.0], "s": [6.0, 6.0, 1.0], "m": "wall"}])
    s = shooter_at(0.0, 0.0, 0.0)
    t = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))
    direction = aim_at(s, Vec3(0.0, 1.0, -10.0))

    res = resolve_shot(w, s, [s, t], RIFLE, s.eye_pos(), direction, now=1.0)
    assert res.victim is None
    assert res.impact_material == "wall"


def test_teammates_are_never_hit():
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0, team="A")
    mate = make_entity(2, "A", Vec3(0.0, 0.0, -5.0))
    enemy = make_entity(3, "B", Vec3(0.0, 0.0, -10.0))
    direction = aim_at(s, Vec3(0.0, 1.0, -10.0))

    res = resolve_shot(w, s, [s, mate, enemy], RIFLE, s.eye_pos(), direction, now=1.0)
    assert res.victim is enemy, "a teammate must not absorb or block the bullet"


def test_nearest_target_wins_when_two_are_lined_up():
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0)
    near = make_entity(2, "B", Vec3(0.0, 0.0, -6.0))
    far = make_entity(3, "B", Vec3(0.0, 0.0, -12.0))
    direction = aim_at(s, Vec3(0.0, 1.0, -12.0))

    res = resolve_shot(w, s, [s, near, far], RIFLE, s.eye_pos(), direction, now=1.0)
    assert res.victim is near


def test_dead_entities_are_not_hittable():
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0)
    t = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))
    t.alive = False
    direction = aim_at(s, Vec3(0.0, 1.0, -10.0))
    assert resolve_shot(w, s, [s, t], RIFLE, s.eye_pos(), direction, now=1.0).victim is None


def test_shot_beyond_weapon_range_misses():
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0)
    t = make_entity(2, "B", Vec3(0.0, 0.0, -3.0))
    direction = aim_at(s, Vec3(0.0, 1.0, -3.0))
    # Knife range is under 2 m.
    assert resolve_shot(w, s, [s, t], KNIFE, s.eye_pos(), direction, now=1.0).victim is None


# ── damage model ──────────────────────────────────────────────────────────────

def test_damage_falloff_is_monotonic():
    close = damage_at_range(RIFLE, RIFLE.falloff_start)
    mid = damage_at_range(RIFLE, (RIFLE.falloff_start + RIFLE.falloff_end) / 2)
    far = damage_at_range(RIFLE, RIFLE.falloff_end + 20.0)
    assert close == RIFLE.damage
    assert close > mid > far
    assert far == pytest.approx(RIFLE.damage * RIFLE.falloff_min)


def test_armor_absorbs_part_of_the_damage():
    unarmored = make_entity(1, "A")
    armored = make_entity(2, "A")
    armored.armor = 100

    unarmored.apply_damage(50, RIFLE.armor_pen, None, now=1.0)
    armored.apply_damage(50, RIFLE.armor_pen, None, now=1.0)
    assert armored.health > unarmored.health
    assert armored.armor < 100


def test_spawn_protection_blocks_damage():
    ent = make_entity(1, "A")
    ent.spawn(Vec3(), 0.0, now=0.0, protect=1.0)
    assert ent.apply_damage(50, 1.0, None, now=0.5) == 0
    assert ent.health == MOVE.max_health
    assert ent.apply_damage(50, 1.0, None, now=1.5) > 0


def test_lethal_damage_marks_entity_dead():
    ent = make_entity(1, "A")
    ent.apply_damage(500, 1.0, 2, now=1.0)
    assert not ent.alive
    assert ent.health == 0
    assert ent.last_hurt_by == 2


# ── lag compensation ──────────────────────────────────────────────────────────

def test_rewind_window_is_capped():
    assert rewind_seconds(0.0, 100, 250) == pytest.approx(0.1)
    assert rewind_seconds(120.0, 100, 250) == pytest.approx(0.16)
    assert rewind_seconds(2000.0, 100, 250) == pytest.approx(0.25), "must clamp"


def test_lag_compensation_hits_where_the_target_used_to_be():
    """The canonical case: the shooter's screen showed the target 150 ms ago."""
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0)
    t = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))

    # Build history: target stood at x=0 until t=1.0, then sprinted to x=5.
    t.history.clear()
    for i in range(61):
        now = i / 60.0
        t.pos.set(0.0 if now <= 1.0 else (now - 1.0) * 20.0, 0.0, -10.0)
        t.record_history(now)
    t.pos.set(5.0, 0.0, -10.0)
    t.record_history(1.25)

    direction = aim_at(s, Vec3(0.0, 1.0, -10.0))  # aimed at the OLD position

    # No rewind: the target has moved, so this misses.
    live = resolve_shot(w, s, [s, t], RIFLE, s.eye_pos(), direction, now=1.25, rewind=0.0)
    assert live.victim is None

    # Rewind 250 ms: the world is restored to what the shooter saw, and it hits.
    lagged = resolve_shot(w, s, [s, t], RIFLE, s.eye_pos(), direction, now=1.25, rewind=0.25)
    assert lagged.victim is t


def test_history_sampling_interpolates():
    ent = make_entity(1, "A")
    ent.history.clear()
    ent.pos.set(0.0, 0.0, 0.0)
    ent.record_history(0.0)
    ent.pos.set(10.0, 0.0, 0.0)
    ent.record_history(1.0)
    x, _, _, alive = ent.sample_history(0.5)
    assert x == pytest.approx(5.0)
    assert alive


# ── melee and perception ──────────────────────────────────────────────────────

def test_knife_only_reaches_targets_in_front_and_close():
    w = make_world()
    s = shooter_at(0.0, 0.0, 0.0)
    near = make_entity(2, "B", Vec3(0.0, 0.0, -1.2))
    behind = make_entity(3, "B", Vec3(0.0, 0.0, 1.2))
    far = make_entity(4, "B", Vec3(0.0, 0.0, -5.0))

    assert melee_targets(s, [s, near, behind, far], KNIFE, w) is near
    assert melee_targets(s, [s, behind, far], KNIFE, w) is None


def test_can_see_respects_field_of_view():
    w = make_world()
    observer = shooter_at(0.0, 0.0, 0.0)
    front = make_entity(2, "B", Vec3(0.0, 0.0, -10.0))
    behind = make_entity(3, "B", Vec3(0.0, 0.0, 10.0))
    fov_cos = math.cos(math.radians(55.0))
    assert can_see(w, observer, front, fov_cos, 60.0)
    assert not can_see(w, observer, behind, fov_cos, 60.0)


# ── weapon state machine ──────────────────────────────────────────────────────

def test_fire_rate_is_enforced():
    ars = Arsenal()
    ars.reset(now=0.0)
    ars.switch_end_at = 0.0
    assert ars.can_fire(0.0, True)
    ars.consume_shot(0.0)
    assert not ars.can_fire(0.05, True), "600 RPM is one shot per 100 ms"
    assert ars.can_fire(0.11, True)


def test_semi_auto_requires_trigger_release():
    ars = Arsenal()
    ars.reset(now=0.0)
    ars.switch_end_at = 0.0
    ars.select("pistol", 0.0)
    ars.switch_end_at = 0.0
    assert ars.can_fire(1.0, True)
    ars.consume_shot(1.0)
    ars.trigger_held = True
    assert not ars.can_fire(5.0, True), "holding the button must not auto-fire a pistol"
    ars.trigger_held = False
    assert ars.can_fire(5.0, True)


def test_reload_refills_from_reserve():
    ars = Arsenal()
    ars.reset(now=0.0)
    ars.switch_end_at = 0.0
    for i in range(10):
        ars.consume_shot(i * 0.2)
    assert ars.ammo() == RIFLE.mag_size - 10
    assert ars.begin_reload(3.0)
    ars.update(3.0 + RIFLE.reload_time + 0.01, 0.016)
    assert ars.ammo() == RIFLE.mag_size
    assert ars.reserve() == RIFLE.reserve_max - 10


def test_cannot_fire_while_reloading_or_switching():
    ars = Arsenal()
    ars.reset(now=0.0)
    ars.switch_end_at = 0.0
    ars.consume_shot(0.0)
    ars.begin_reload(0.5)
    assert not ars.can_fire(1.0, True)
    ars.select("pistol", 5.0)
    assert not ars.can_fire(5.1, True), "still drawing the pistol"
    assert ars.can_fire(5.0 + PISTOL.switch_time + 0.01, True)


def test_spread_grows_with_movement_and_spray():
    ars = Arsenal()
    ars.reset(now=0.0)
    still = ars.current_spread_deg(0.0, True, MOVE.sprint_speed)
    running = ars.current_spread_deg(MOVE.sprint_speed, True, MOVE.sprint_speed)
    airborne = ars.current_spread_deg(0.0, False, MOVE.sprint_speed)
    assert running > still
    assert airborne > running

    for i in range(8):
        ars.consume_shot(i * 0.11)
    sprayed = ars.current_spread_deg(0.0, True, MOVE.sprint_speed)
    assert sprayed > still


def test_recoil_pattern_climbs_then_plateaus():
    ars = Arsenal()
    ars.reset(now=0.0)
    kicks = []
    for i in range(10):
        ars.consume_shot(i * 0.11)
        kicks.append(ars.recoil_kick()[1])
    assert kicks[0] < kicks[3], "recoil should ramp up over the first shots"
    assert max(kicks) == pytest.approx(max(kicks[3:8]), abs=1e-9)
    assert all(k > 0 for k in kicks)


def test_knife_cannot_be_dropped():
    ars = Arsenal()
    ars.reset(now=0.0)
    assert ars.drop_current(0.0) == "rifle"
    assert ars.current in ("pistol", "knife")
    ars.select("knife", 0.0)
    assert ars.drop_current(1.0) is None
