# path: backend/app/game/bots.py
"""Bot AI.

A bot produces the *same* thing a human client produces: a key bitmask plus view angles.
It gets no privileged access to physics, no teleporting, no perfect knowledge — perception
goes through the same raycaster the bullets do. That makes bots a genuine test of the
simulation, and it means anything that feels wrong about bot movement is a real bug in
movement rather than a bug in the AI.

Cost control: pathfinding and target selection run at ``BOT_LOGIC_HZ`` (default 10) and
are staggered across bots by seeding each one's phase, so a room of 10 bots re-plans one
bot per tick instead of all ten on the same tick.
"""

from __future__ import annotations

import math
import random
from enum import Enum
from typing import List, Optional, Sequence, Tuple

from ..config import BotTuning, DEG, MOVE, WEAPONS
from ..protocol import (
    InputCmd, K_BACK, K_FIRE, K_FORWARD, K_JUMP, K_LEFT, K_RELOAD, K_RIGHT, K_SPRINT,
)
from .combat import can_see
from .entities import Entity
from .mathx import Vec3, angles_to_dir, clamp, dir_to_angles, forward_xz, move_angle_towards, right_xz, wrap_angle
from .nav import NavGraph
from .world import World


class BotState(Enum):
    IDLE = "idle"
    PATROL = "patrol"
    SEEK = "seek"
    ENGAGE = "engage"
    RETREAT = "retreat"


WAYPOINT_REACH = 1.3
STUCK_DISTANCE = 0.45
STUCK_TIME = 1.1


class BotBrain:
    """Per-bot state. One instance lives on ``Entity.brain``."""

    def __init__(self, entity: Entity, world: World, nav: NavGraph, tuning: BotTuning, seed: int):
        self.ent = entity
        self.world = world
        self.nav = nav
        self.t = tuning
        self.rng = random.Random(seed)

        self.state = BotState.IDLE
        self.think_interval = 0.1
        # Stagger: each bot thinks on a different phase of the logic clock.
        self.next_think = self.rng.random() * self.think_interval

        self.target: Optional[Entity] = None
        self.target_last_seen_at = -99.0
        self.last_known: Optional[Vec3] = None
        self.first_seen_at = -99.0

        self.path: List[int] = []
        self.path_index = 0
        self.goal_node: Optional[int] = None
        self.repath_at = 0.0

        self.aim_error_deg = tuning.aim_error_deg
        self.aim_offset = (0.0, 0.0)
        self.desired_yaw = entity.yaw
        self.desired_pitch = 0.0

        self.burst_left = 0
        self.next_burst_at = 0.0
        self.strafe_dir = 1
        self.next_strafe_at = 0.0

        self.stuck_ref = entity.pos.copy()
        self.stuck_since = 0.0
        self.jump_until = 0.0
        self.wants_move = False
        self.fire_pressed = False

    # ── public API ────────────────────────────────────────────────────────────

    def reset_on_spawn(self, now: float) -> None:
        self.state = BotState.PATROL
        self.target = None
        self.last_known = None
        self.path = []
        self.path_index = 0
        self.goal_node = None
        self.repath_at = 0.0
        self.aim_error_deg = self.t.aim_error_deg
        self.desired_yaw = self.ent.yaw
        self.desired_pitch = 0.0
        self.burst_left = 0
        self.next_burst_at = now
        self.stuck_ref = self.ent.pos.copy()
        self.stuck_since = now
        self.fire_pressed = False

    def update(
        self, now: float, dt: float, enemies: Sequence[Entity], think_interval: float
    ) -> Tuple[int, float, float, int]:
        """Advance the AI and return ``(keys, yaw, pitch, weapon_slot)``."""
        self.think_interval = think_interval
        ent = self.ent
        if not ent.alive:
            self.fire_pressed = False
            return 0, ent.yaw, ent.pitch, 0

        if now >= self.next_think:
            self.next_think = now + think_interval
            self._think(now, enemies)

        self._track_aim(now, dt)
        keys = self._steer(now, dt)
        keys |= self._combat_keys(now)
        slot = self._weapon_choice()
        return keys, self.ent.yaw, self.ent.pitch, slot

    # ── perception & decision ─────────────────────────────────────────────────

    def _think(self, now: float, enemies: Sequence[Entity]) -> None:
        ent = self.ent
        fov_cos = math.cos(self.t.fov_deg * 0.5 * DEG)

        visible: List[Tuple[float, Entity]] = []
        for e in enemies:
            if not e.alive:
                continue
            d = ent.pos.distance(e.pos)
            if d > self.t.sight_range:
                continue
            if can_see(self.world, ent, e, fov_cos, self.t.sight_range):
                visible.append((d, e))

        # Someone shooting you from behind is a target even if you can't see them yet.
        if not visible and ent.last_hurt_by is not None and (now - ent.last_hurt_at) < 2.0:
            for e in enemies:
                if e.eid == ent.last_hurt_by and e.alive:
                    self.last_known = e.pos.copy()
                    if self.state in (BotState.PATROL, BotState.IDLE):
                        self._enter_seek(now)
                    break

        if visible:
            visible.sort(key=lambda pair: pair[0])
            chosen = visible[0][1]
            if chosen is not self.target:
                # New acquisition resets the aim cone and starts the reaction clock.
                self.target = chosen
                self.first_seen_at = now
                self.aim_error_deg = self.t.aim_error_deg
                self._new_aim_offset()
            self.target_last_seen_at = now
            self.last_known = chosen.pos.copy()

            low_health = ent.health <= self.t.retreat_health
            if low_health and self.state is not BotState.RETREAT:
                self._enter_retreat(now)
            elif not low_health:
                self.state = BotState.ENGAGE
        else:
            if self.target is not None and (now - self.target_last_seen_at) > 0.4:
                self.target = None
            if self.state is BotState.ENGAGE or self.state is BotState.RETREAT:
                if self.last_known is not None and (now - self.target_last_seen_at) < 5.0:
                    self._enter_seek(now)
                else:
                    self._enter_patrol(now)
            elif self.state is BotState.SEEK:
                arrived = self.last_known is not None and ent.pos.distance(self.last_known) < 2.5
                if arrived or (now - self.target_last_seen_at) > 5.0:
                    self._enter_patrol(now)
            elif self.state in (BotState.IDLE, BotState.PATROL):
                if not self.path or self.path_index >= len(self.path):
                    self._enter_patrol(now)

        if self.state is BotState.ENGAGE:
            self._plan_engage(now)
        elif self.state is BotState.RETREAT and now >= self.repath_at:
            self._enter_retreat(now)

    # ── state entry ───────────────────────────────────────────────────────────

    def _enter_patrol(self, now: float) -> None:
        self.state = BotState.PATROL
        if len(self.nav) == 0:
            return
        goal = self.nav.random_node_far_from(self.rng, self.ent.pos, 18.0)
        if goal is not None:
            self._set_path(goal)
        self.repath_at = now + 4.0

    def _enter_seek(self, now: float) -> None:
        self.state = BotState.SEEK
        if self.last_known is not None and len(self.nav):
            node = self.nav.nearest(self.last_known)
            if node is not None:
                self._set_path(node)
        self.repath_at = now + 1.5

    def _enter_retreat(self, now: float) -> None:
        self.state = BotState.RETREAT
        self.repath_at = now + 2.0
        if len(self.nav) == 0:
            return
        threat = self.last_known or (self.target.pos if self.target else None)
        if threat is None:
            self._enter_patrol(now)
            return
        # Move to cover that is further from the threat than we currently are.
        away = (self.ent.pos - threat)
        away.y = 0.0
        away = away.normalized()
        probe = self.ent.pos + away * 12.0
        node = self.nav.best_cover_near(probe, 10.0)
        if node is None:
            node = self.nav.nearest(probe)
        if node is not None:
            self._set_path(node)

    def _plan_engage(self, now: float) -> None:
        """While engaging, path toward or away from the target to hold preferred range."""
        target = self.target
        if target is None or len(self.nav) == 0:
            return
        dist = self.ent.pos.distance(target.pos)
        pref = self.t.preferred_range
        if dist > pref * 1.6:
            node = self.nav.nearest(target.pos)
            if node is not None and now >= self.repath_at:
                self._set_path(node)
                self.repath_at = now + 1.0
        elif dist < pref * 0.45:
            away = (self.ent.pos - target.pos)
            away.y = 0.0
            probe = self.ent.pos + away.normalized() * 8.0
            node = self.nav.nearest(probe)
            if node is not None and now >= self.repath_at:
                self._set_path(node)
                self.repath_at = now + 1.2
        else:
            # In the pocket: stop path-following and just strafe.
            self.path = []
            self.path_index = 0

    def _set_path(self, goal_node: int) -> None:
        if len(self.nav) == 0:
            return
        start = self.nav.nearest(self.ent.pos)
        if start is None:
            return
        raw = self.nav.find_path(start, goal_node)
        if not raw:
            self.path = []
            self.path_index = 0
            self.goal_node = None
            return
        self.path = self.nav.smooth_path(self.world, raw)
        self.path_index = 1 if len(self.path) > 1 else 0
        self.goal_node = goal_node

    # ── aiming ────────────────────────────────────────────────────────────────

    def _new_aim_offset(self) -> None:
        theta = self.rng.random() * math.tau
        r = math.sqrt(self.rng.random())
        self.aim_offset = (math.cos(theta) * r, math.sin(theta) * r)

    def _track_aim(self, now: float, dt: float) -> None:
        ent = self.ent
        target = self.target

        if target is not None and target.alive:
            # Aim at the chest, drifting toward the head as the bot settles on target.
            settle = clamp((now - self.first_seen_at) / 1.2, 0.0, 1.0)
            aim_h = MOVE.player_height * (0.55 + 0.30 * settle)
            aim_point = Vec3(target.pos.x, target.pos.y + aim_h, target.pos.z)

            # Lead a moving target slightly; hitscan means this is only about the tracking
            # delay, so a small factor is enough to stop bots trailing behind strafers.
            lead = clamp(ent.pos.distance(target.pos) / 60.0, 0.0, 0.25)
            aim_point.x += target.vel.x * lead
            aim_point.z += target.vel.z * lead

            to = aim_point - ent.eye_pos()
            yaw, pitch = dir_to_angles(to.normalized())

            self.aim_error_deg = max(
                self.t.aim_min_deg, self.aim_error_deg - self.t.aim_converge * dt
            )
            err = self.aim_error_deg * DEG
            self.desired_yaw = yaw + self.aim_offset[0] * err
            self.desired_pitch = clamp(pitch + self.aim_offset[1] * err, -1.4, 1.4)
        else:
            # Look where we're going.
            look_at = self._current_waypoint() or self.last_known
            if look_at is not None:
                to = Vec3(look_at.x - ent.pos.x, 0.0, look_at.z - ent.pos.z)
                if to.length_sq() > 0.05:
                    self.desired_yaw = dir_to_angles(to.normalized())[0]
            self.desired_pitch *= 0.9

        turn = self.t.aim_speed * dt
        ent.yaw = move_angle_towards(ent.yaw, self.desired_yaw, turn)
        ent.pitch = ent.pitch + clamp(self.desired_pitch - ent.pitch, -turn, turn)

    # ── movement ──────────────────────────────────────────────────────────────

    def _current_waypoint(self) -> Optional[Vec3]:
        if not self.path or self.path_index >= len(self.path):
            return None
        return self.nav.node_pos(self.path[self.path_index])

    def _steer(self, now: float, dt: float) -> int:
        ent = self.ent
        move = Vec3()
        self.wants_move = False

        wp = self._current_waypoint()
        if wp is not None:
            to = Vec3(wp.x - ent.pos.x, 0.0, wp.z - ent.pos.z)
            if to.length_xz() < WAYPOINT_REACH:
                self.path_index += 1
                wp = self._current_waypoint()
                if wp is not None:
                    to = Vec3(wp.x - ent.pos.x, 0.0, wp.z - ent.pos.z)
            if wp is not None and to.length_sq() > 1e-4:
                move = to.normalized()
                self.wants_move = True

        # Strafe while engaging, whether or not we're path-following.
        if self.state is BotState.ENGAGE and self.target is not None:
            if now >= self.next_strafe_at:
                self.next_strafe_at = now + self.t.strafe_period * (0.7 + self.rng.random() * 0.6)
                self.strafe_dir = -self.strafe_dir
            to_target = Vec3(self.target.pos.x - ent.pos.x, 0.0, self.target.pos.z - ent.pos.z)
            if to_target.length_sq() > 1e-4:
                side = Vec3(-to_target.z, 0.0, to_target.x).normalized() * float(self.strafe_dir)
                move = (move + side * 1.2) if self.wants_move else side
                self.wants_move = True

        keys = 0
        if self.wants_move and move.length_sq() > 1e-6:
            move = move.normalized()
            fwd = forward_xz(ent.yaw)
            rgt = right_xz(ent.yaw)
            f = move.x * fwd.x + move.z * fwd.z
            r = move.x * rgt.x + move.z * rgt.z
            if f > 0.35:
                keys |= K_FORWARD
            elif f < -0.35:
                keys |= K_BACK
            if r > 0.35:
                keys |= K_RIGHT
            elif r < -0.35:
                keys |= K_LEFT
            # Sprint only when running a path with no target — never while shooting, since
            # sprinting wrecks accuracy and looks wrong.
            if self.target is None and f > 0.8 and self.state in (BotState.PATROL, BotState.SEEK):
                keys |= K_SPRINT

        keys |= self._unstick(now, keys)
        return keys

    def _unstick(self, now: float, keys: int) -> int:
        """Detect a bot grinding against geometry and do something about it."""
        ent = self.ent
        extra = 0
        if not self.wants_move:
            self.stuck_ref.copy_from(ent.pos)
            self.stuck_since = now
            return 0

        if ent.pos.distance_xz(self.stuck_ref) > STUCK_DISTANCE:
            self.stuck_ref.copy_from(ent.pos)
            self.stuck_since = now
            return 0

        if (now - self.stuck_since) > STUCK_TIME:
            # Jump first (most obstructions here are crates), then re-plan.
            if ent.grounded and now >= self.jump_until:
                extra |= K_JUMP
                self.jump_until = now + 0.6
            if (now - self.stuck_since) > STUCK_TIME * 2.0:
                self.stuck_since = now
                self.path = []
                self.path_index = 0
                self._enter_patrol(now)
        return extra

    # ── combat ────────────────────────────────────────────────────────────────

    def _combat_keys(self, now: float) -> int:
        ent = self.ent
        ars = ent.arsenal
        keys = 0

        d = ars.definition
        st = ars.slots.get(ars.current)
        if st is not None and not d.melee:
            low = st.ammo <= max(1, int(d.mag_size * self.t.reload_threshold))
            if low and st.reserve > 0 and not ars.is_reloading(now):
                # Only reload out of contact, or when completely dry.
                if self.target is None or st.ammo == 0:
                    self.fire_pressed = False
                    return K_RELOAD

        target = self.target
        if target is None or not target.alive or not ent.alive:
            self.fire_pressed = False
            self.burst_left = 0
            return keys

        if (now - self.first_seen_at) < self.t.reaction_time:
            self.fire_pressed = False
            return keys

        # Don't fire until the barrel is actually pointing at them.
        to = (target.center() - ent.eye_pos())
        dist = to.length()
        aim_dot = to.normalized().dot(angles_to_dir(ent.yaw, ent.pitch))
        needed = math.cos(clamp(2.5 + self.aim_error_deg, 2.0, 25.0) * DEG)
        if aim_dot < needed:
            self.fire_pressed = False
            return keys

        if d.melee and dist > d.range:
            self.fire_pressed = False
            return keys

        if self.burst_left <= 0:
            if now < self.next_burst_at:
                self.fire_pressed = False
                return keys
            self.burst_left = self.rng.randint(self.t.burst_min, self.t.burst_max)

        # The room turns a held FIRE bit into shots at the weapon's rate; the bot presses
        # and releases the same way a player does so semi-auto weapons behave correctly.
        if self.fire_pressed and not d.auto:
            self.fire_pressed = False
            return keys

        self.fire_pressed = True
        return keys | K_FIRE

    def note_shot_fired(self, now: float) -> None:
        """Called by the room after a shot actually leaves the barrel."""
        if self.burst_left > 0:
            self.burst_left -= 1
            if self.burst_left <= 0:
                self.next_burst_at = now + self.t.burst_pause * (0.7 + self.rng.random() * 0.6)

    def _weapon_choice(self) -> int:
        """Pick the best usable weapon; returns a slot number or 0 for 'no change'."""
        ars = self.ent.arsenal
        for wid, slot in (("rifle", 1), ("pistol", 2)):
            st = ars.slots.get(wid)
            if st and (st.ammo > 0 or st.reserve > 0):
                return 0 if ars.current == wid else slot
        return 0 if ars.current == "knife" else 3


class BotManager:
    """Owns the bots in one room: creation, removal, and the per-tick AI pass."""

    def __init__(self, world: World, nav: NavGraph, tuning: BotTuning, logic_hz: int, seed: int = 0):
        self.world = world
        self.nav = nav
        self.tuning = tuning
        self.think_interval = 1.0 / max(1, logic_hz)
        self.rng = random.Random(seed or 1337)

    def attach(self, entity: Entity) -> BotBrain:
        brain = BotBrain(entity, self.world, self.nav, self.tuning, seed=self.rng.randrange(1 << 30))
        entity.brain = brain
        return brain

    def update(self, now: float, dt: float, bots: Sequence[Entity], all_entities: Sequence[Entity]) -> None:
        """Queue one input command per bot, exactly as a connected client would.

        Going through the input queue rather than mutating the entity directly is the
        whole point: bots and humans then share one code path in the room, so a movement
        or fire-rate change can't accidentally apply to only one of them.
        """
        by_team: dict = {"A": [], "B": []}
        for e in all_entities:
            by_team.setdefault(e.team, []).append(e)

        for bot in bots:
            brain: Optional[BotBrain] = bot.brain
            if brain is None:
                continue
            enemies = by_team["B"] if bot.team == "A" else by_team["A"]
            keys, yaw, pitch, slot = brain.update(now, dt, enemies, self.think_interval)
            bot.input_queue.append(
                InputCmd(bot.last_input_seq + 1, dt, keys, yaw, pitch, slot)
            )
