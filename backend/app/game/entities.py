# path: backend/app/game/entities.py
"""Entities: players and bots.

Both use the same class. A bot is a player whose input bitmask happens to be produced by
:mod:`app.game.bots` instead of arriving over a socket — which guarantees bots obey the
same physics, the same fire rates and the same collision rules as humans.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Deque, List, Optional, Tuple

from ..config import DEFAULT_PRIMARY, MOVE
from ..protocol import (
    F_ADS, F_AIRBORNE, F_BOT, F_DEAD, F_GROUNDED, F_MOVING, F_RELOADING, F_SPRINTING,
)
from .mathx import AABB, Vec3, angles_to_dir
from .weapons import Arsenal

# 1 s of history at 60 Hz is comfortably more than LAGCOMP_MAX_MS needs.
HISTORY_LEN = 64


class HistoryFrame:
    __slots__ = ("t", "x", "y", "z", "yaw", "pitch", "alive")

    def __init__(self, t: float, x: float, y: float, z: float, yaw: float, pitch: float, alive: bool):
        self.t = t
        self.x = x
        self.y = y
        self.z = z
        self.yaw = yaw
        self.pitch = pitch
        self.alive = alive


class Entity:
    __slots__ = (
        "eid", "name", "team", "is_bot", "sid",
        "pos", "vel", "yaw", "pitch",
        "health", "armor", "alive", "grounded",
        "kills", "deaths", "damage_dealt", "score",
        "respawn_at", "spawn_protect_until", "last_hurt_by", "last_hurt_at",
        "arsenal", "history", "primary",
        "last_input_seq", "input_queue", "pending_weapon", "prev_keys",
        "ping_ms", "last_seen", "chat_times", "brain", "connected",
    )

    def __init__(
        self,
        eid: int,
        name: str,
        team: str,
        is_bot: bool = False,
        sid: str = "",
        primary: str = DEFAULT_PRIMARY,
    ):
        self.eid = eid
        self.name = name
        self.team = team
        self.is_bot = is_bot
        self.sid = sid
        self.primary = primary
        self.connected = True

        self.pos = Vec3()
        self.vel = Vec3()
        self.yaw = 0.0
        self.pitch = 0.0

        self.health = MOVE.max_health
        self.armor = 0
        self.alive = False
        self.grounded = False

        self.kills = 0
        self.deaths = 0
        self.damage_dealt = 0.0
        self.score = 0

        self.respawn_at = 0.0
        self.spawn_protect_until = 0.0
        self.last_hurt_by: Optional[int] = None
        self.last_hurt_at = 0.0

        self.arsenal = Arsenal(primary)
        self.history: Deque[HistoryFrame] = deque(maxlen=HISTORY_LEN)

        self.last_input_seq = 0
        self.input_queue: Deque = deque()
        self.pending_weapon = 0
        self.prev_keys = 0

        self.ping_ms = 0.0
        self.last_seen = 0.0
        self.chat_times: Deque[float] = deque(maxlen=8)
        self.brain = None  # BotBrain, set by BotManager for bots

    # ── geometry ──────────────────────────────────────────────────────────────

    def eye_pos(self) -> Vec3:
        return Vec3(self.pos.x, self.pos.y + MOVE.eye_height, self.pos.z)

    def look_dir(self) -> Vec3:
        return angles_to_dir(self.yaw, self.pitch)

    def center(self) -> Vec3:
        return Vec3(self.pos.x, self.pos.y + MOVE.player_height * 0.5, self.pos.z)

    @staticmethod
    def hitboxes_at(x: float, y: float, z: float) -> Tuple[AABB, AABB]:
        """(body, head) boxes for a transform. Head is the top of the capsule.

        Slightly narrower than the collision radius: a shot that grazes the very edge of
        the collider shouldn't register, which keeps "I definitely hit that" complaints
        down without making targets feel bullet-proof.
        """
        r = MOVE.hit_radius
        head_min = y + MOVE.head_min
        body = (x - r, y, z - r, x + r, head_min, z + r)
        head = (x - r * 0.75, head_min, z - r * 0.75, x + r * 0.75, y + MOVE.player_height, z + r * 0.75)
        return body, head

    def hitboxes(self) -> Tuple[AABB, AABB]:
        return self.hitboxes_at(self.pos.x, self.pos.y, self.pos.z)

    # ── history / lag compensation ────────────────────────────────────────────

    def record_history(self, now: float) -> None:
        self.history.append(
            HistoryFrame(now, self.pos.x, self.pos.y, self.pos.z, self.yaw, self.pitch, self.alive)
        )

    def sample_history(self, t: float) -> Tuple[float, float, float, bool]:
        """Interpolated transform at server time ``t``.

        Falls back to the present when the buffer doesn't reach that far back, which is
        the correct behaviour for a player who just spawned.
        """
        hist = self.history
        if not hist:
            return self.pos.x, self.pos.y, self.pos.z, self.alive
        if t >= hist[-1].t:
            f = hist[-1]
            return f.x, f.y, f.z, f.alive
        if t <= hist[0].t:
            f = hist[0]
            return f.x, f.y, f.z, f.alive
        prev = hist[0]
        for frame in hist:
            if frame.t >= t:
                span = frame.t - prev.t
                if span <= 1e-9:
                    return frame.x, frame.y, frame.z, frame.alive
                a = (t - prev.t) / span
                return (
                    prev.x + (frame.x - prev.x) * a,
                    prev.y + (frame.y - prev.y) * a,
                    prev.z + (frame.z - prev.z) * a,
                    frame.alive and prev.alive,
                )
            prev = frame
        return self.pos.x, self.pos.y, self.pos.z, self.alive

    # ── state ─────────────────────────────────────────────────────────────────

    def spawn(self, pos: Vec3, yaw: float, now: float, protect: float = 1.0) -> None:
        self.pos = pos.copy()
        self.vel.set(0.0, 0.0, 0.0)
        self.yaw = yaw
        self.pitch = 0.0
        self.health = MOVE.max_health
        self.armor = 0
        self.alive = True
        self.grounded = True
        self.respawn_at = 0.0
        self.spawn_protect_until = now + protect
        self.last_hurt_by = None
        self.arsenal.reset(now, self.primary)
        self.history.clear()
        self.record_history(now)

    def apply_damage(self, amount: float, armor_pen: float, attacker: Optional[int], now: float) -> int:
        """Apply damage through armour. Returns the health actually removed."""
        if not self.alive:
            return 0
        if now < self.spawn_protect_until:
            return 0
        to_health = amount * armor_pen
        to_armor = amount - to_health
        if self.armor > 0:
            absorbed = min(self.armor, to_armor)
            self.armor -= int(math.ceil(absorbed))
            # Armour absorbs half of what it can't fully stop.
            to_health += (to_armor - absorbed) * 1.0
        else:
            to_health += to_armor
        dealt = int(round(to_health))
        if dealt < 1:
            dealt = 1
        before = self.health
        self.health -= dealt
        if attacker is not None and attacker != self.eid:
            self.last_hurt_by = attacker
            self.last_hurt_at = now
        if self.health <= 0:
            self.health = 0
            self.alive = False
        return min(dealt, before)

    def flags(self) -> int:
        f = 0
        if not self.alive:
            f |= F_DEAD
        if self.grounded:
            f |= F_GROUNDED
        else:
            f |= F_AIRBORNE
        if self.arsenal.reload_end_at:
            f |= F_RELOADING
        speed_sq = self.vel.x * self.vel.x + self.vel.z * self.vel.z
        if speed_sq > 0.25:
            f |= F_MOVING
        if speed_sq > MOVE.walk_speed * MOVE.walk_speed * 1.05:
            f |= F_SPRINTING
        if self.is_bot:
            f |= F_BOT
        # Half-raised counts as aiming: the remote animation should start early rather
        # than pop at the end of the transition.
        if self.arsenal.ads_progress > 0.4:
            f |= F_ADS
        return f

    def horizontal_speed(self) -> float:
        return math.sqrt(self.vel.x * self.vel.x + self.vel.z * self.vel.z)

    # ── serialisation ─────────────────────────────────────────────────────────

    def to_ent_state(self) -> dict:
        return {
            "id": self.eid,
            "t": self.team,
            "x": round(self.pos.x, 3),
            "y": round(self.pos.y, 3),
            "z": round(self.pos.z, 3),
            "a": round(self.yaw, 3),
            "p": round(self.pitch, 3),
            "hp": self.health,
            "f": self.flags(),
            "w": self.arsenal.current,
        }

    def to_self_state(self, now: float) -> dict:
        return {
            "x": round(self.pos.x, 4),
            "y": round(self.pos.y, 4),
            "z": round(self.pos.z, 4),
            "vx": round(self.vel.x, 3),
            "vy": round(self.vel.y, 3),
            "vz": round(self.vel.z, 3),
            "hp": self.health,
            "ar": self.armor,
            "w": self.arsenal.current,
            "am": self.arsenal.ammo(),
            "rs": self.arsenal.reserve(),
            "f": self.flags(),
            "rt": round(max(0.0, self.respawn_at - now), 2) if not self.alive else 0.0,
            "pg": int(self.ping_ms),
            "ap": round(self.arsenal.ads_progress, 2),
            "sl": [
                {"id": wid, "ammo": st.ammo, "reserve": st.reserve}
                for wid, st in self.arsenal.slots.items()
            ],
        }

    def to_score_row(self) -> dict:
        return {
            "id": self.eid,
            "name": self.name,
            "team": self.team,
            "kills": self.kills,
            "deaths": self.deaths,
            "bot": self.is_bot,
            "ping": int(self.ping_ms),
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        kind = "bot" if self.is_bot else "player"
        return f"<Entity {self.eid} {self.name!r} {kind} team={self.team} hp={self.health}>"
