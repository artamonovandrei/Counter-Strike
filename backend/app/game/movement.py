# path: backend/app/game/movement.py
"""The movement integrator.

**This file is mirrored, line for line, by frontend/src/movement.ts.** The client predicts
locally with the TS copy and the server re-simulates with this one; any behavioural
difference between the two shows up immediately as rubber-banding. Change both together.

Model: Quake-style ground friction + acceleration with a hard cap on air control, then
axis-separated AABB collision with a step-up retry. No swept tests — at 60 Hz and 7 m/s a
player moves 12 cm per tick, an order of magnitude below the collider radius, so
discrete resolution never tunnels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from ..config import MOVE, MoveConfig
from ..protocol import K_ADS, K_BACK, K_CROUCH, K_FORWARD, K_JUMP, K_LEFT, K_RIGHT, K_SPRINT
from .mathx import EPS, Vec3, forward_xz, right_xz
from .world import World

# Overlaps shallower than this are treated as contact rather than penetration. 0.1 mm is
# far below anything a player can perceive and far above floating-point noise.
PENETRATION_EPS = 1e-4


@dataclass
class MoveResult:
    grounded: bool
    landed: bool
    land_speed: float  # downward speed at the moment of landing, 0 otherwise
    blocked: bool


def _player_box(pos: Vec3, radius: float, height: float) -> Tuple[float, float, float, float, float, float]:
    return (
        pos.x - radius, pos.y, pos.z - radius,
        pos.x + radius, pos.y + height, pos.z + radius,
    )


def _resolve_axis(world: World, pos: Vec3, radius: float, height: float, axis: int) -> bool:
    """Push the player out of any brush it overlaps, moving only along ``axis``.

    Returns True if a correction was applied. Two passes: the first fixes the deepest
    penetration, the second catches the case where that push moved the player into a
    second brush (inside corners).

    Three rules here exist because of a bug that dropped players through the world:

    * **A graze is not a collision.** Brushes that merely touch — overlapping by less than
      ``PENETRATION_EPS`` — are ignored. This is the one that actually mattered: a player
      standing flush against a container overlapped it by 1e-10 on Z purely from floating
      point, which was enough for the Y resolver to fire and eject them 1.8 m downwards.
    * **Vertical pushes prefer "up".** A player only gets pushed *down* out of a brush if
      their feet started below it, i.e. they jumped into a ceiling. Otherwise they are
      standing on or in something and belong on top of it — you cannot sink through a
      surface you are standing on.
    * **The second pass may not reverse the first**, or the two passes can shove a player
      back and forth between adjacent brushes and land them somewhere worse.
    """
    corrected = False
    push_sign = 0.0  # locked in by the first applied correction
    for _ in range(2):
        box = _player_box(pos, radius, height)
        best_push = 0.0
        for i in world.overlapping(box):
            b = world.boxes[i]

            # Penetration depth on every axis. If the shallowest is negligible the boxes
            # are touching, not intersecting, and resolving would be a violent no-op.
            pen_x = min(b[3] - box[0], box[3] - b[0])
            pen_y = min(b[4] - box[1], box[4] - b[1])
            pen_z = min(b[5] - box[2], box[5] - b[2])
            if pen_x <= PENETRATION_EPS or pen_y <= PENETRATION_EPS or pen_z <= PENETRATION_EPS:
                continue

            lo_idx = axis
            hi_idx = axis + 3
            exit_pos = b[hi_idx] - box[lo_idx]  # distance to leave in the + direction
            exit_neg = box[hi_idx] - b[lo_idx]  # distance to leave in the - direction
            if axis == 1:
                came_from_below = box[1] < b[1] - PENETRATION_EPS
                push = -exit_neg if came_from_below else exit_pos
            else:
                push = exit_pos if exit_pos < exit_neg else -exit_neg
            if push_sign != 0.0 and push * push_sign < 0.0:
                continue
            if abs(push) > abs(best_push):
                best_push = push
        if abs(best_push) < EPS:
            break
        if axis == 0:
            pos.x += best_push
        elif axis == 1:
            pos.y += best_push
        else:
            pos.z += best_push
        push_sign = 1.0 if best_push > 0.0 else -1.0
        corrected = True
    return corrected


def _is_free(world: World, pos: Vec3, radius: float, height: float) -> bool:
    # Lift the box off the floor by a hair so resting on ground isn't "blocked".
    box = _player_box(pos, radius, height)
    return world.is_free((box[0], box[1] + 0.02, box[2], box[3], box[4], box[5]))


def collide_and_slide(
    world: World,
    pos: Vec3,
    vel: Vec3,
    dt: float,
    was_grounded: bool,
    cfg: MoveConfig = MOVE,
) -> MoveResult:
    """Integrate position from velocity, resolving collisions. Mutates ``pos``/``vel``."""
    radius = cfg.player_radius
    height = cfg.player_height
    start_x, start_y, start_z = pos.x, pos.y, pos.z

    # ── horizontal ────────────────────────────────────────────────────────────
    pos.x += vel.x * dt
    blocked_x = _resolve_axis(world, pos, radius, height, 0)
    pos.z += vel.z * dt
    blocked_z = _resolve_axis(world, pos, radius, height, 2)
    blocked = blocked_x or blocked_z

    if blocked and was_grounded and cfg.step_height > 0.0:
        # Retry the same horizontal motion from step_height higher, then settle back down.
        stepped = Vec3(start_x, start_y + cfg.step_height, start_z)
        if _is_free(world, stepped, radius, height):
            stepped.x += vel.x * dt
            _resolve_axis(world, stepped, radius, height, 0)
            stepped.z += vel.z * dt
            _resolve_axis(world, stepped, radius, height, 2)
            # Drop back onto whatever is underneath.
            stepped.y -= cfg.step_height
            _resolve_axis(world, stepped, radius, height, 1)

            gained = (stepped.x - start_x) ** 2 + (stepped.z - start_z) ** 2
            current = (pos.x - start_x) ** 2 + (pos.z - start_z) ** 2
            # Only take the stepped-up result if it is genuinely better *and* leaves the
            # player somewhere legal. Skipping the free check is how a player ends up
            # embedded in a crate, which the axis resolver then has to rescue them from.
            if (
                gained > current + 1e-4
                and stepped.y >= start_y - EPS
                and _is_free(world, stepped, radius, height)
            ):
                pos.copy_from(stepped)
                blocked = False

    if blocked_x and abs(pos.x - start_x) < abs(vel.x * dt) * 0.5:
        vel.x = 0.0
    if blocked_z and abs(pos.z - start_z) < abs(vel.z * dt) * 0.5:
        vel.z = 0.0

    # ── vertical ──────────────────────────────────────────────────────────────
    falling_speed = vel.y
    pos.y += vel.y * dt
    hit_vertical = _resolve_axis(world, pos, radius, height, 1)

    grounded = False
    landed = False
    land_speed = 0.0
    if hit_vertical:
        if vel.y <= 0.0:
            grounded = True
            if not was_grounded:
                landed = True
                land_speed = -falling_speed
        vel.y = 0.0
    else:
        # Ground probe: still "grounded" while resting a hair above a surface, so walking
        # across brush seams doesn't flicker the jump state.
        probe = Vec3(pos.x, pos.y - 0.06, pos.z)
        if vel.y <= 0.0 and not _is_free(world, probe, radius, height):
            grounded = True

    return MoveResult(grounded=grounded, landed=landed, land_speed=land_speed, blocked=blocked)


def _accelerate(vel: Vec3, wish_x: float, wish_z: float, wish_speed: float, accel: float, dt: float) -> None:
    """Quake acceleration: only ever adds speed along the wish direction, and only up to
    ``wish_speed`` *along that axis* — which is what makes strafe-jumping and air control
    feel the way players expect."""
    current = vel.x * wish_x + vel.z * wish_z
    add_speed = wish_speed - current
    if add_speed <= 0.0:
        return
    accel_speed = accel * dt * wish_speed
    if accel_speed > add_speed:
        accel_speed = add_speed
    vel.x += wish_x * accel_speed
    vel.z += wish_z * accel_speed


def _friction(vel: Vec3, dt: float, cfg: MoveConfig) -> None:
    speed = math.sqrt(vel.x * vel.x + vel.z * vel.z)
    if speed < EPS:
        vel.x = 0.0
        vel.z = 0.0
        return
    control = speed if speed > cfg.stop_speed else cfg.stop_speed
    drop = control * cfg.friction * dt
    new_speed = speed - drop
    if new_speed < 0.0:
        new_speed = 0.0
    scale = new_speed / speed
    vel.x *= scale
    vel.z *= scale


def step_movement(
    world: World,
    pos: Vec3,
    vel: Vec3,
    yaw: float,
    keys: int,
    dt: float,
    was_grounded: bool,
    cfg: MoveConfig = MOVE,
) -> MoveResult:
    """One authoritative movement step. Mutates ``pos`` and ``vel`` in place.

    ``keys`` is the protocol bitmask, so the exact same function serves human inputs and
    bot inputs — bots synthesise a bitmask rather than getting a privileged path, which
    means they are subject to identical physics.
    """
    # ── desired direction in world space ──────────────────────────────────────
    move_f = (1.0 if keys & K_FORWARD else 0.0) - (1.0 if keys & K_BACK else 0.0)
    move_r = (1.0 if keys & K_RIGHT else 0.0) - (1.0 if keys & K_LEFT else 0.0)

    wish_x = 0.0
    wish_z = 0.0
    if move_f or move_r:
        fwd = forward_xz(yaw)
        rgt = right_xz(yaw)
        wish_x = fwd.x * move_f + rgt.x * move_r
        wish_z = fwd.z * move_f + rgt.z * move_r
        norm = math.sqrt(wish_x * wish_x + wish_z * wish_z)
        if norm > EPS:
            wish_x /= norm
            wish_z /= norm

    # Sprinting is forward-only and grounded-only; it is not a speed multiplier you can
    # carry into the air by jumping the moment you press shift.
    #
    # Priority matters: aiming down sights overrides sprint, so holding shift while
    # scoped does not quietly give you rifle mobility with sniper accuracy.
    ads = bool(keys & K_ADS)
    sprinting = bool(keys & K_SPRINT) and move_f > 0.0 and was_grounded and not ads
    crouching = bool(keys & K_CROUCH)
    if ads:
        wish_speed = cfg.ads_speed
    elif crouching:
        wish_speed = cfg.crouch_speed
    elif sprinting:
        wish_speed = cfg.sprint_speed
    else:
        wish_speed = cfg.walk_speed
    if move_f == 0.0 and move_r == 0.0:
        wish_speed = 0.0

    # ── acceleration ──────────────────────────────────────────────────────────
    grounded = was_grounded
    if grounded:
        _friction(vel, dt, cfg)
        _accelerate(vel, wish_x, wish_z, wish_speed, cfg.ground_accel, dt)
        if keys & K_JUMP:
            vel.y = cfg.jump_speed
            grounded = False
    else:
        air_wish = wish_speed if wish_speed < cfg.air_cap else cfg.air_cap
        _accelerate(vel, wish_x, wish_z, air_wish, cfg.air_accel, dt)

    if not grounded:
        vel.y -= cfg.gravity * dt
        if vel.y < -cfg.max_fall_speed:
            vel.y = -cfg.max_fall_speed

    result = collide_and_slide(world, pos, vel, dt, grounded, cfg)

    # Keep players inside the arena even if geometry has a gap.
    b = world.bounds
    margin = cfg.player_radius + 0.1
    if pos.x < b[0] + margin:
        pos.x = b[0] + margin
        vel.x = max(0.0, vel.x)
    elif pos.x > b[3] - margin:
        pos.x = b[3] - margin
        vel.x = min(0.0, vel.x)
    if pos.z < b[2] + margin:
        pos.z = b[2] + margin
        vel.z = max(0.0, vel.z)
    elif pos.z > b[5] - margin:
        pos.z = b[5] - margin
        vel.z = min(0.0, vel.z)

    return result


def fall_damage(land_speed: float, cfg: MoveConfig = MOVE) -> int:
    """Damage for landing at ``land_speed`` m/s. Zero below the free-fall threshold."""
    if land_speed <= cfg.fall_damage_speed:
        return 0
    return int((land_speed - cfg.fall_damage_speed) * cfg.fall_damage_scale)
