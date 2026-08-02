# path: backend/app/game/combat.py
"""Hitscan resolution with lag compensation.

The rule this implements: *what you saw on your screen is what the server checks*. When a
client fires, the server rewinds every other entity by roughly the age of the world state
that client was looking at — half its round-trip time plus the client's interpolation
delay — resolves the ray against those historical hitboxes, then returns to the present.

The rewind is capped (``LAGCOMP_MAX_MS``). Without a cap, a player on a 900 ms connection
could shoot at where you stood a second ago, which is unbearable for the victim. The cap
trades "high-ping players must lead their shots" for "low-ping players don't get killed
around corners", which is the right trade for a shooter.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from ..config import WeaponDef
from .entities import Entity
from .mathx import Vec3, angles_to_dir, ray_aabb
from .weapons import damage_at_range
from .world import World


@dataclass
class ShotResult:
    origin: Vec3
    direction: Vec3
    end: Vec3
    distance: float
    victim: Optional[Entity] = None
    damage: int = 0
    headshot: bool = False
    killed: bool = False
    impact_normal: Optional[Vec3] = None
    impact_material: str = ""


def rewind_seconds(ping_ms: float, interp_delay_ms: int, cap_ms: int) -> float:
    """How far back to move the world for a shooter with this ping."""
    total = ping_ms * 0.5 + interp_delay_ms
    if total < 0.0:
        total = 0.0
    if total > cap_ms:
        total = float(cap_ms)
    return total / 1000.0


def resolve_shot(
    world: World,
    shooter: Entity,
    others: Iterable[Entity],
    weapon: WeaponDef,
    origin: Vec3,
    direction: Vec3,
    now: float,
    rewind: float = 0.0,
    friendly_fire: bool = False,
) -> ShotResult:
    """Trace one bullet. Does not apply damage — the room decides what to do with it."""
    max_dist = weapon.range

    wall_hit = world.raycast(origin, direction, max_dist)
    best_t = wall_hit.t if wall_hit else max_dist
    best_victim: Optional[Entity] = None
    best_head = False

    rewind_time = now - rewind
    ox, oy, oz = origin.x, origin.y, origin.z
    dx, dy, dz = direction.x, direction.y, direction.z

    for ent in others:
        if ent is shooter or not ent.alive:
            continue
        if not friendly_fire and ent.team == shooter.team:
            # Teammates neither take damage nor block the bullet. Blocking without damage
            # is worse: it silently eats shots and reads as a netcode bug to the shooter.
            continue

        if rewind > 0.0 and ent.history:
            hx, hy, hz, was_alive = ent.sample_history(rewind_time)
            if not was_alive:
                continue
        else:
            hx, hy, hz = ent.pos.x, ent.pos.y, ent.pos.z

        # Cheap reject: skip the per-box slab tests when the ray can't reach this entity.
        to_x = hx - ox
        to_y = hy + 0.9 - oy
        to_z = hz - oz
        along = to_x * dx + to_y * dy + to_z * dz
        if along < -1.5 or along > best_t + 1.5:
            continue
        perp_sq = (to_x * to_x + to_y * to_y + to_z * to_z) - along * along
        if perp_sq > 4.0:  # more than 2 m off-axis: no hitbox can reach
            continue

        body, head = Entity.hitboxes_at(hx, hy, hz)

        t_head = ray_aabb(ox, oy, oz, dx, dy, dz, head, best_t)
        t_body = ray_aabb(ox, oy, oz, dx, dy, dz, body, best_t)

        if t_head >= 0.0 and (t_body < 0.0 or t_head <= t_body):
            if t_head < best_t:
                best_t = t_head
                best_victim = ent
                best_head = True
        elif t_body >= 0.0 and t_body < best_t:
            best_t = t_body
            best_victim = ent
            best_head = False

    end = Vec3(ox + dx * best_t, oy + dy * best_t, oz + dz * best_t)
    result = ShotResult(origin=origin, direction=direction, end=end, distance=best_t)

    if best_victim is not None:
        base = damage_at_range(weapon, best_t)
        if best_head:
            base *= weapon.headshot_mult
        result.victim = best_victim
        result.damage = int(round(base))
        result.headshot = best_head
    elif wall_hit is not None and abs(best_t - wall_hit.t) < 1e-6:
        result.impact_normal = wall_hit.normal
        result.impact_material = wall_hit.material

    return result


def melee_targets(
    shooter: Entity, others: Iterable[Entity], weapon: WeaponDef, world: World,
    friendly_fire: bool = False,
) -> Optional[Entity]:
    """Nearest enemy inside the knife's arc, with line of sight.

    A cone rather than a ray, because a pure ray makes melee feel broken at close range
    where small aim errors are large angles.
    """
    origin = shooter.eye_pos()
    fwd = angles_to_dir(shooter.yaw, shooter.pitch)
    best: Optional[Entity] = None
    best_d = weapon.range
    for ent in others:
        if ent is shooter or not ent.alive:
            continue
        if not friendly_fire and ent.team == shooter.team:
            continue
        to = ent.center() - origin
        d = to.length()
        if d > best_d or d < 1e-3:
            continue
        if to.normalized().dot(fwd) < 0.65:  # ~49 degree half-angle
            continue
        if not world.line_of_sight(origin, ent.center()):
            continue
        best = ent
        best_d = d
    return best


def can_see(world: World, observer: Entity, target: Entity, fov_cos: float, max_range: float) -> bool:
    """Perception test used by bots: range, then field of view, then line of sight.

    Ordered cheapest-first, and the LoS raycast tries the chest before the head so a bot
    peeking over a crate at a target's head doesn't lose them to a single blocked ray.
    """
    eye = observer.eye_pos()
    to = target.center() - eye
    dist = to.length()
    if dist > max_range or dist < 1e-4:
        return dist <= max_range
    if to.normalized().dot(angles_to_dir(observer.yaw, observer.pitch)) < fov_cos:
        return False
    if world.line_of_sight(eye, target.center()):
        return True
    return world.line_of_sight(eye, target.eye_pos())
