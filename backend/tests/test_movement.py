# path: backend/tests/test_movement.py
"""Movement integrator.

These assertions are the contract the TypeScript port in frontend/src/movement.ts has to
satisfy too. If one of them changes, client prediction changes with it.
"""

from __future__ import annotations

import math

import pytest

from app.config import MOVE
from app.game.mathx import Vec3
from app.game.movement import collide_and_slide, fall_damage, step_movement
from app.protocol import K_BACK, K_FORWARD, K_JUMP, K_LEFT, K_RIGHT, K_SPRINT
from conftest import make_world

DT = 1.0 / 60.0


def run(world, pos, vel, keys, ticks, yaw=0.0, grounded=True):
    result = None
    for _ in range(ticks):
        result = step_movement(world, pos, vel, yaw, keys, DT, grounded)
        grounded = result.grounded
    return result


def test_rests_on_floor(world):
    pos = Vec3(0.0, 2.0, 0.0)
    vel = Vec3()
    run(world, pos, vel, 0, 120, grounded=False)
    assert pos.y == pytest.approx(0.0, abs=0.03)
    assert vel.y == pytest.approx(0.0, abs=1e-6)


def test_gravity_accelerates_before_landing(world):
    pos = Vec3(0.0, 10.0, 0.0)
    vel = Vec3()
    step_movement(world, pos, vel, 0.0, 0, DT, False)
    assert vel.y == pytest.approx(-MOVE.gravity * DT, rel=1e-6)
    assert pos.y < 10.0


def test_jump_apex_matches_analytic_height(world):
    """v²/2g for the configured jump speed, within one tick of discretisation error."""
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3()
    grounded = True
    peak = 0.0
    for _ in range(120):
        res = step_movement(world, pos, vel, 0.0, K_JUMP if grounded else 0, DT, grounded)
        grounded = res.grounded
        peak = max(peak, pos.y)
    expected = MOVE.jump_speed ** 2 / (2 * MOVE.gravity)
    assert peak == pytest.approx(expected, abs=0.08)
    assert peak > 1.0, "must clear a 1 m crate"


def test_forward_walk_reaches_walk_speed(world):
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3()
    run(world, pos, vel, K_FORWARD, 60)
    speed = math.hypot(vel.x, vel.z)
    assert speed == pytest.approx(MOVE.walk_speed, rel=0.02)
    # yaw 0 looks down -Z
    assert pos.z < -1.0
    assert abs(pos.x) < 1e-6


def test_sprint_is_faster_and_forward_only(world):
    pos_a, vel_a = Vec3(), Vec3()
    run(world, pos_a, vel_a, K_FORWARD | K_SPRINT, 90)
    pos_b, vel_b = Vec3(), Vec3()
    run(world, pos_b, vel_b, K_FORWARD, 90)
    assert math.hypot(vel_a.x, vel_a.z) > math.hypot(vel_b.x, vel_b.z) + 1.0

    # Sprinting sideways gains nothing.
    pos_c, vel_c = Vec3(), Vec3()
    run(world, pos_c, vel_c, K_RIGHT | K_SPRINT, 90)
    assert math.hypot(vel_c.x, vel_c.z) == pytest.approx(MOVE.walk_speed, rel=0.03)


def test_diagonal_is_not_faster_than_straight(world):
    """Normalising the wish direction is what stops diagonal movement being 41% faster."""
    pos_d, vel_d = Vec3(), Vec3()
    run(world, pos_d, vel_d, K_FORWARD | K_RIGHT, 90)
    pos_s, vel_s = Vec3(), Vec3()
    run(world, pos_s, vel_s, K_FORWARD, 90)
    assert math.hypot(vel_d.x, vel_d.z) == pytest.approx(math.hypot(vel_s.x, vel_s.z), rel=0.02)


def test_friction_brings_player_to_rest(world):
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3()
    run(world, pos, vel, K_FORWARD, 60)
    assert math.hypot(vel.x, vel.z) > 4.0
    run(world, pos, vel, 0, 90)
    assert math.hypot(vel.x, vel.z) == pytest.approx(0.0, abs=1e-6)


def test_wall_blocks_horizontal_movement():
    w = make_world([{"p": [3.0, 3.0, 0.0], "s": [0.5, 6.0, 12.0], "m": "wall"}])
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3()
    # yaw = -pi/2 faces +X
    run(w, pos, vel, K_FORWARD, 120, yaw=-math.pi / 2)
    assert pos.x < 3.0 - 0.25 + 1e-3
    assert pos.x == pytest.approx(3.0 - 0.25 - MOVE.player_radius, abs=0.05)


def test_air_control_is_limited():
    """Airborne acceleration must be a fraction of ground acceleration."""
    w = make_world()
    pos, vel = Vec3(0.0, 5.0, 0.0), Vec3()
    for _ in range(20):
        step_movement(w, pos, vel, 0.0, K_FORWARD, DT, False)
    air_speed = math.hypot(vel.x, vel.z)

    pos2, vel2 = Vec3(0.0, 0.0, 0.0), Vec3()
    for _ in range(20):
        step_movement(w, pos2, vel2, 0.0, K_FORWARD, DT, True)
    ground_speed = math.hypot(vel2.x, vel2.z)

    assert air_speed < ground_speed * 0.5


def test_step_up_over_low_ledge():
    """A 0.3 m step is walkable; movement stays continuous over it."""
    # Ledge occupies z in [-6, -2] at height 0.3. Stop while still on top of it.
    w = make_world([{"p": [0.0, 0.15, -4.0], "s": [8.0, 0.3, 4.0], "m": "concrete"}])
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3()
    run(w, pos, vel, K_FORWARD, 45)
    assert -6.0 < pos.z < -2.4, f"should be standing on the ledge, got z={pos.z}"
    assert pos.y == pytest.approx(0.3, abs=0.05)


def test_tall_ledge_blocks_without_jump():
    w = make_world([{"p": [0.0, 0.75, -4.0], "s": [8.0, 1.5, 4.0], "m": "concrete"}])
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3()
    run(w, pos, vel, K_FORWARD, 120)
    assert pos.y < 0.1, "1.5 m wall is not a step"
    assert pos.z > -2.4


def test_cannot_leave_bounds(world):
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3()
    run(world, pos, vel, K_FORWARD | K_SPRINT, 600)
    b = world.bounds
    assert pos.z >= b[2] - 1e-6
    assert pos.z <= b[5] + 1e-6


def test_no_tunnelling_at_max_speed():
    """A thin wall must stop a player moving at terminal horizontal speed."""
    w = make_world([{"p": [2.0, 3.0, 0.0], "s": [0.2, 6.0, 12.0], "m": "wall"}])
    pos = Vec3(0.0, 0.0, 0.0)
    vel = Vec3(MOVE.sprint_speed * 3.0, 0.0, 0.0)
    for _ in range(30):
        collide_and_slide(w, pos, vel, DT, True)
    assert pos.x < 2.0


def test_fall_damage_thresholds():
    assert fall_damage(5.0) == 0
    assert fall_damage(MOVE.fall_damage_speed) == 0
    assert fall_damage(MOVE.fall_damage_speed + 10.0) == 40
    assert fall_damage(60.0) > fall_damage(30.0)


def test_backwards_and_strafe_keys_are_opposite(world):
    pos_f, vel_f = Vec3(), Vec3()
    run(world, pos_f, vel_f, K_FORWARD, 30)
    pos_b, vel_b = Vec3(), Vec3()
    run(world, pos_b, vel_b, K_BACK, 30)
    assert vel_f.z < 0 < vel_b.z

    pos_r, vel_r = Vec3(), Vec3()
    run(world, pos_r, vel_r, K_RIGHT, 30)
    pos_l, vel_l = Vec3(), Vec3()
    run(world, pos_l, vel_l, K_LEFT, 30)
    assert vel_r.x > 0 > vel_l.x


def test_opposing_keys_cancel(world):
    pos, vel = Vec3(), Vec3()
    run(world, pos, vel, K_FORWARD | K_BACK, 60)
    assert math.hypot(vel.x, vel.z) == pytest.approx(0.0, abs=1e-6)
