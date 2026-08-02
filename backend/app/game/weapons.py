# path: backend/app/game/weapons.py
"""Per-entity weapon state: ammo, reload, fire timing, spread, recoil and ADS.

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
    MELEE_WEAPON,
    PATTERNS,
    SECONDARY_WEAPON,
    WEAPONS,
    WeaponDef,
    make_loadout,
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
    """Everything an entity carries, plus the timers that gate firing.

    Slots are by *category*, not by weapon: slot 1 is whatever primary you picked, slot 2
    is the pistol, slot 3 the knife. That keeps the number keys meaning the same thing for
    every player regardless of loadout.
    """

    __slots__ = (
        "slots", "order", "current", "primary", "next_fire_at", "reload_end_at",
        "switch_end_at", "burst_count", "last_shot_at", "spread_extra", "trigger_held",
        "dropped", "ads", "ads_progress",
    )

    def __init__(self, primary: Optional[str] = None):
        self.primary = primary
        self.slots: Dict[str, WeaponSlotState] = {}
        self.order: List[str] = []
        self.ads = False
        self.ads_progress = 0.0  # 0 = hip, 1 = fully sighted
        self._install(make_loadout(primary))
        self.next_fire_at: float = 0.0
        self.reload_end_at: float = 0.0
        self.switch_end_at: float = 0.0
        self.burst_count: int = 0
        self.last_shot_at: float = -99.0
        self.spread_extra: float = 0.0  # degrees, decays over time
        self.trigger_held: bool = False
        self.dropped: List[str] = []

    def _install(self, loadout: List[str]) -> None:
        self.order = list(loadout)
        self.slots = {wid: WeaponSlotState(wid) for wid in loadout}
        self.current = loadout[0]

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

    def can_ads(self) -> bool:
        return self.definition.ads_fov > 0.0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def reset(self, now: float, primary: Optional[str] = None) -> None:
        """Full refill on spawn, including re-granting anything that was dropped."""
        if primary is not None:
            self.primary = primary
        self._install(make_loadout(self.primary))
        self.next_fire_at = now
        self.reload_end_at = 0.0
        self.switch_end_at = now
        self.burst_count = 0
        self.last_shot_at = -99.0
        self.spread_extra = 0.0
        self.trigger_held = False
        self.dropped = []
        self.ads = False
        self.ads_progress = 0.0

    def update(self, now: float, dt: float) -> Optional[str]:
        """Advance timers. Returns an event name when something completed this tick."""
        # Spread recovery.
        if self.spread_extra > 0.0:
            self.spread_extra -= self.definition.spread_decay * dt
            if self.spread_extra < 0.0:
                self.spread_extra = 0.0

        # Sights raise and lower over ads_time rather than snapping, so quick-scoping has
        # to actually wait for the sight picture.
        d = self.definition
        if d.ads_time > 0.0:
            step = dt / d.ads_time
        else:
            step = 1.0
        if self.ads and self.can_ads():
            self.ads_progress = min(1.0, self.ads_progress + step)
        else:
            self.ads_progress = max(0.0, self.ads_progress - step)

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

    def set_ads(self, want: bool) -> None:
        self.ads = want and self.can_ads()

    def select_slot(self, slot: int, now: float) -> bool:
        for wid in self.order:
            if WEAPONS[wid].slot == slot:
                return self.select(wid, now)
        return False

    def select(self, wid: str, now: float) -> bool:
        if wid not in self.slots or wid == self.current:
            return False
        # Switching cancels a reload in progress — same as every shooter of this shape.
        self.reload_end_at = 0.0
        self.current = wid
        self.burst_count = 0
        self.spread_extra = 0.0
        self.ads = False
        self.ads_progress = 0.0
        d = WEAPONS[wid]
        self.switch_end_at = now + d.switch_time
        self.next_fire_at = max(self.next_fire_at, self.switch_end_at)
        return True

    def next_available(self, direction: int = 1) -> Optional[str]:
        order = [w for w in self.order if w in self.slots]
        if len(order) < 2:
            return None
        i = order.index(self.current)
        return order[(i + direction) % len(order)]

    def drop_current(self, now: float) -> Optional[str]:
        """Drop the held weapon. The knife can't be dropped — you always keep a fallback."""
        if self.current == MELEE_WEAPON or len(self.slots) <= 1:
            return None
        dropped = self.current
        del self.slots[dropped]
        self.dropped.append(dropped)
        fallback = next((w for w in self.order if w in self.slots), None)
        if fallback is None:
            return None
        self.current = fallback
        self.switch_end_at = now + WEAPONS[fallback].switch_time
        self.next_fire_at = max(self.next_fire_at, self.switch_end_at)
        self.reload_end_at = 0.0
        self.ads = False
        self.ads_progress = 0.0
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
        # Reloading drops you out of the sights; you cannot scope through a reload.
        self.ads = False
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
        """Cone half-angle in degrees for the next shot.

        ADS is applied *last* and multiplicatively, scaled by how far the sights have
        actually come up. That is what makes the sniper unusable from the hip and precise
        when scoped, from one number rather than two separate code paths.
        """
        d = self.definition
        if d.melee:
            return 0.0
        spread = d.spread_base + self.spread_extra
        if not grounded:
            spread += d.spread_air
        elif speed > 0.1:
            spread += d.spread_move * min(1.0, speed / max(0.1, max_speed))
        spread = min(spread, d.spread_max + d.spread_air + d.spread_move)

        if self.ads_progress > 0.0:
            mult = 1.0 + (d.ads_spread_mult - 1.0) * self.ads_progress
            spread *= mult
        return spread

    def recoil_kick(self) -> Tuple[float, float]:
        """View kick (yaw, pitch) in radians for the shot just fired.

        Automatic weapons follow a fixed pattern so they are learnable; everything else
        just kicks up. The kick is sent to the client, which applies it to the camera —
        meaning the player has to physically pull back to control a spray.
        """
        d = self.definition
        if d.recoil_pitch <= 0.0:
            return 0.0, 0.0
        idx = max(0, self.burst_count - 1)
        pattern = PATTERNS.get(d.id)
        if pattern is not None:
            pitch_pat, yaw_pat = pattern
            p = pitch_pat[min(idx, len(pitch_pat) - 1)]
            y = yaw_pat[min(idx, len(yaw_pat) - 1)]
        else:
            p = min(1.0, 0.5 + idx * 0.2)
            y = 0.0
        # Sights steady the weapon a little, as they should.
        steady = 1.0 - 0.25 * self.ads_progress
        return d.recoil_yaw * y * DEG * steady, d.recoil_pitch * p * DEG * steady

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

    def pellet_directions(
        self, yaw: float, pitch: float, spread_deg: float, rng: random.Random
    ) -> List[Tuple[float, float]]:
        """Angles for every projectile in one trigger pull.

        Single-projectile weapons get one sample from the cone. Shotguns get ``pellets``
        samples, with the first one biased toward the centre so aiming still rewards you
        at the edge of the weapon's range.
        """
        d = self.definition
        if d.pellets <= 1:
            return [self.apply_spread(yaw, pitch, spread_deg, rng)]
        out = [self.apply_spread(yaw, pitch, spread_deg * 0.25, rng)]
        for _ in range(d.pellets - 1):
            out.append(self.apply_spread(yaw, pitch, spread_deg, rng))
        return out


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
