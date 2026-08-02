# path: backend/app/game/weapons.py
"""Per-entity weapon state: ammo, reload, fire timing, spread and recoil accumulation.

All timing is against the room clock (seconds since room start) rather than wall clock,
so a paused/lagging room stays internally consistent and tests can drive time by hand.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from ..config import (
    BURST_RESET_TIME,
    DEG,
    DEFAULT_LOADOUT,
    RIFLE_PATTERN,
    RIFLE_YAW_PATTERN,
    SLOT_TO_WEAPON,
    WEAPONS,
    WeaponDef,
)


class WeaponSlotState:
    __slots__ = ("wid", "ammo", "reserve")

    def __init__(self, wid: str):
        self.wid = wid
        d = WEAPONS[wid]
        self.ammo = d.mag_size
        self.reserve = d.reserve_max

    def reset(self) -> None:
        d = WEAPONS[self.wid]
        self.ammo = d.mag_size
        self.reserve = d.reserve_max


class Arsenal:
    """Everything an entity carries, plus the timers that gate firing."""

    __slots__ = (
        "slots", "current", "next_fire_at", "reload_end_at", "switch_end_at",
        "burst_count", "last_shot_at", "spread_extra", "trigger_held", "dropped",
    )

    def __init__(self, loadout: Optional[List[str]] = None):
        self.slots: Dict[str, WeaponSlotState] = {}
        for wid in (loadout or DEFAULT_LOADOUT):
            self.slots[wid] = WeaponSlotState(wid)
        self.current: str = (loadout or DEFAULT_LOADOUT)[0]
        self.next_fire_at: float = 0.0
        self.reload_end_at: float = 0.0
        self.switch_end_at: float = 0.0
        self.burst_count: int = 0
        self.last_shot_at: float = -99.0
        self.spread_extra: float = 0.0  # degrees, decays over time
        self.trigger_held: bool = False
        self.dropped: List[str] = []

    # ── introspection ─────────────────────────────────────────────────────────

    @property
    def definition(self) -> WeaponDef:
        return WEAPONS[self.current]

    @property
    def state(self) -> WeaponSlotState:
        return self.slots[self.current]

    def is_reloading(self, now: float) -> bool:
        return now < self.reload_end_at

    def is_switching(self, now: float) -> bool:
        return now < self.switch_end_at

    def ammo(self) -> int:
        st = self.slots.get(self.current)
        return st.ammo if st else 0

    def reserve(self) -> int:
        st = self.slots.get(self.current)
        return st.reserve if st else 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def reset(self, now: float, loadout: Optional[List[str]] = None) -> None:
        """Full refill on spawn, including re-granting anything that was dropped."""
        wanted = loadout or DEFAULT_LOADOUT
        self.slots = {wid: WeaponSlotState(wid) for wid in wanted}
        self.current = wanted[0]
        self.next_fire_at = now
        self.reload_end_at = 0.0
        self.switch_end_at = now
        self.burst_count = 0
        self.last_shot_at = -99.0
        self.spread_extra = 0.0
        self.trigger_held = False
        self.dropped = []

    def update(self, now: float, dt: float) -> Optional[str]:
        """Advance timers. Returns an event name when something completed this tick."""
        # Spread recovery.
        if self.spread_extra > 0.0:
            self.spread_extra -= self.definition.spread_decay * dt
            if self.spread_extra < 0.0:
                self.spread_extra = 0.0

        # Burst/pattern reset once the trigger has been off long enough.
        if self.burst_count and (now - self.last_shot_at) > BURST_RESET_TIME:
            self.burst_count = 0

        if self.reload_end_at and now >= self.reload_end_at:
            self._finish_reload()
            self.reload_end_at = 0.0
            return "reload_done"
        return None

    def _finish_reload(self) -> None:
        st = self.slots.get(self.current)
        if st is None:
            return
        d = WEAPONS[st.wid]
        needed = d.mag_size - st.ammo
        take = min(needed, st.reserve)
        st.ammo += take
        st.reserve -= take

    # ── actions ───────────────────────────────────────────────────────────────

    def select_slot(self, slot: int, now: float) -> bool:
        wid = SLOT_TO_WEAPON.get(slot)
        if wid is None or wid not in self.slots or wid == self.current:
            return False
        return self.select(wid, now)

    def select(self, wid: str, now: float) -> bool:
        if wid not in self.slots or wid == self.current:
            return False
        # Switching cancels a reload in progress — same as every shooter of this shape.
        self.reload_end_at = 0.0
        self.current = wid
        self.burst_count = 0
        self.spread_extra = 0.0
        d = WEAPONS[wid]
        self.switch_end_at = now + d.switch_time
        self.next_fire_at = max(self.next_fire_at, self.switch_end_at)
        return True

    def next_available(self, direction: int = 1) -> Optional[str]:
        order = [w for w in DEFAULT_LOADOUT if w in self.slots]
        if len(order) < 2:
            return None
        i = order.index(self.current)
        return order[(i + direction) % len(order)]

    def drop_current(self, now: float) -> Optional[str]:
        """Drop the held weapon. The knife can't be dropped — you always keep a fallback."""
        if self.current == "knife" or len(self.slots) <= 1:
            return None
        dropped = self.current
        del self.slots[dropped]
        self.dropped.append(dropped)
        fallback = next((w for w in DEFAULT_LOADOUT if w in self.slots), None)
        if fallback is None:
            return None
        self.current = fallback
        self.switch_end_at = now + WEAPONS[fallback].switch_time
        self.next_fire_at = max(self.next_fire_at, self.switch_end_at)
        self.reload_end_at = 0.0
        return dropped

    def begin_reload(self, now: float) -> bool:
        d = self.definition
        st = self.slots.get(self.current)
        if st is None or d.melee:
            return False
        if self.is_reloading(now) or self.is_switching(now):
            return False
        if st.ammo >= d.mag_size or st.reserve <= 0:
            return False
        self.reload_end_at = now + d.reload_time
        self.burst_count = 0
        self.spread_extra = 0.0
        return True

    def can_fire(self, now: float, trigger_down: bool) -> bool:
        d = self.definition
        st = self.slots.get(self.current)
        if st is None:
            return False
        if not trigger_down:
            return False
        if not d.auto and self.trigger_held:
            # Semi-auto: one shot per press, no matter how the client spams the bit.
            return False
        if now < self.next_fire_at or self.is_reloading(now) or self.is_switching(now):
            return False
        if not d.melee and st.ammo <= 0:
            return False
        return True

    def consume_shot(self, now: float) -> None:
        d = self.definition
        st = self.slots[self.current]
        if not d.melee:
            st.ammo -= 1
        self.next_fire_at = now + d.shot_interval
        self.last_shot_at = now
        self.burst_count += 1
        self.spread_extra = min(d.spread_max, self.spread_extra + d.spread_per_shot)

    def auto_reload_needed(self) -> bool:
        d = self.definition
        st = self.slots.get(self.current)
        return bool(st and not d.melee and st.ammo <= 0 and st.reserve > 0)

    # ── ballistics ────────────────────────────────────────────────────────────

    def current_spread_deg(self, speed: float, grounded: bool, max_speed: float) -> float:
        """Cone half-angle in degrees for the next shot."""
        d = self.definition
        if d.melee:
            return 0.0
        spread = d.spread_base + self.spread_extra
        if not grounded:
            spread += d.spread_air
        elif speed > 0.1:
            spread += d.spread_move * min(1.0, speed / max(0.1, max_speed))
        return min(spread, d.spread_max + d.spread_air + d.spread_move)

    def recoil_kick(self) -> Tuple[float, float]:
        """View kick (yaw, pitch) in radians for the shot just fired.

        The rifle follows a fixed pattern so it is learnable; the pistol just kicks up.
        The kick is sent to the client, which applies it to the camera — meaning the
        player has to physically pull down to control a spray.
        """
        d = self.definition
        if d.recoil_pitch <= 0.0:
            return 0.0, 0.0
        idx = max(0, self.burst_count - 1)
        if d.id == "rifle":
            p = RIFLE_PATTERN[min(idx, len(RIFLE_PATTERN) - 1)]
            y = RIFLE_YAW_PATTERN[min(idx, len(RIFLE_YAW_PATTERN) - 1)]
        else:
            p = min(1.0, 0.5 + idx * 0.2)
            y = 0.0
        pitch = d.recoil_pitch * p * DEG
        yaw = d.recoil_yaw * y * DEG
        return yaw, pitch

    def apply_spread(
        self, yaw: float, pitch: float, spread_deg: float, rng: random.Random
    ) -> Tuple[float, float]:
        """Perturb aim angles inside a cone of ``spread_deg``.

        Uniform over the disc (sqrt of the radius sample), not over the radius — otherwise
        shots cluster in the centre far more than the cone implies.
        """
        if spread_deg <= 0.0:
            return yaw, pitch
        r = math.sqrt(rng.random()) * spread_deg * DEG
        theta = rng.random() * math.tau
        return yaw + math.cos(theta) * r, pitch + math.sin(theta) * r


def damage_at_range(d: WeaponDef, distance: float) -> float:
    """Linear falloff between ``falloff_start`` and ``falloff_end``."""
    if distance <= d.falloff_start:
        return d.damage
    if distance >= d.falloff_end:
        return d.damage * d.falloff_min
    span = d.falloff_end - d.falloff_start
    if span <= 0.0:
        return d.damage * d.falloff_min
    t = (distance - d.falloff_start) / span
    return d.damage * (1.0 - t * (1.0 - d.falloff_min))
