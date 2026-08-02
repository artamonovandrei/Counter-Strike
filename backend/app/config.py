# path: backend/app/config.py
"""Settings (env-driven) plus the game tuning tables.

The tuning tables are the single runtime source of truth: the client receives them in the
``welcome`` message rather than hard-coding its own copy, which is what keeps client-side
prediction agreeing with the server.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

VERSION = "1.0.0"

# repo_root/backend/app/config.py -> repo_root
REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = Path(os.environ.get("ASSETS_DIR", REPO_ROOT / "assets"))
MAPS_DIR = ASSETS_DIR / "maps"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"
    cors_origins: str = "*"

    tick_hz: int = Field(default=60, ge=20, le=128)
    snapshot_hz: int = Field(default=30, ge=10, le=64)
    map_name: str = "alley"

    room_max_players: int = Field(default=10, ge=2, le=32)
    room_max_rooms: int = Field(default=8, ge=1, le=64)
    bots_per_team: int = Field(default=5, ge=0, le=16)
    bot_difficulty: str = "normal"
    bot_logic_hz: int = Field(default=10, ge=2, le=60)
    bot_fill: bool = True

    round_seconds: int = Field(default=360, ge=30)
    score_limit: int = Field(default=50, ge=1)
    warmup_seconds: int = Field(default=5, ge=0)
    intermission_seconds: int = Field(default=10, ge=1)
    respawn_seconds: float = Field(default=3.0, ge=0.0)

    lagcomp_max_ms: int = Field(default=250, ge=0, le=1000)
    interp_delay_ms: int = Field(default=100, ge=0, le=500)
    max_inputs_per_tick: int = Field(default=8, ge=1, le=64)

    redis_url: str = ""

    @field_validator("bot_difficulty")
    @classmethod
    def _known_difficulty(cls, v: str) -> str:
        v = v.lower().strip()
        return v if v in BOT_TUNING else "normal"

    @property
    def cors_list(self) -> List[str]:
        if self.cors_origins.strip() in ("*", ""):
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def tick_dt(self) -> float:
        return 1.0 / self.tick_hz

    @property
    def snapshot_every(self) -> int:
        """Send a snapshot every N ticks (>=1)."""
        return max(1, round(self.tick_hz / self.snapshot_hz))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# ─── Movement tuning ──────────────────────────────────────────────────────────
# Mirrored by frontend/src/movement.ts. Units are metres, seconds, radians.


@dataclass(frozen=True)
class MoveConfig:
    player_radius: float = 0.40
    player_height: float = 1.80
    eye_height: float = 1.62
    # Head hitbox occupies the top of the capsule; body is everything below.
    head_min: float = 1.45
    hit_radius: float = 0.36

    gravity: float = 22.0
    jump_speed: float = 7.0  # -> apex of 1.11 m, clears a 1 m crate
    walk_speed: float = 5.2
    sprint_speed: float = 7.2
    crouch_speed: float = 2.6
    ground_accel: float = 70.0
    air_accel: float = 14.0
    air_cap: float = 1.2  # ceiling on wishspeed while airborne = limited air control
    friction: float = 9.0
    stop_speed: float = 1.5
    step_height: float = 0.35
    max_fall_speed: float = 60.0

    max_health: int = 100
    max_armor: int = 100
    fall_damage_speed: float = 16.0  # below this, falling is free
    fall_damage_scale: float = 4.0


MOVE = MoveConfig()


# ─── Weapons ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WeaponDef:
    id: str
    slot: int
    name: str
    mag_size: int
    reserve_max: int
    rpm: float
    auto: bool
    damage: float
    headshot_mult: float
    armor_pen: float  # fraction of damage that bypasses armour
    range: float
    falloff_start: float
    falloff_end: float
    falloff_min: float  # damage multiplier at/after falloff_end
    reload_time: float
    # All spread/recoil angles are in degrees; converted once at fire time.
    spread_base: float
    spread_move: float  # extra spread at full running speed
    spread_air: float
    spread_per_shot: float
    spread_max: float
    spread_decay: float  # degrees recovered per second
    recoil_pitch: float
    recoil_yaw: float
    switch_time: float
    melee: bool = False

    @property
    def shot_interval(self) -> float:
        return 60.0 / self.rpm


WEAPONS: Dict[str, WeaponDef] = {
    "rifle": WeaponDef(
        id="rifle",
        slot=1,
        name="MR-9 Rifle",
        mag_size=30,
        reserve_max=90,
        rpm=600.0,
        auto=True,
        damage=33.0,
        headshot_mult=4.0,
        armor_pen=0.75,
        range=200.0,
        falloff_start=25.0,
        falloff_end=90.0,
        falloff_min=0.55,
        reload_time=2.4,
        spread_base=0.30,
        spread_move=2.20,
        spread_air=6.00,
        spread_per_shot=0.42,
        spread_max=5.00,
        spread_decay=9.0,
        recoil_pitch=0.55,
        recoil_yaw=0.22,
        switch_time=0.55,
    ),
    "pistol": WeaponDef(
        id="pistol",
        slot=2,
        name="SD-11 Pistol",
        mag_size=12,
        reserve_max=60,
        rpm=400.0,
        auto=False,
        damage=26.0,
        headshot_mult=4.0,
        armor_pen=0.55,
        range=120.0,
        falloff_start=12.0,
        falloff_end=50.0,
        falloff_min=0.45,
        reload_time=1.9,
        spread_base=0.45,
        spread_move=2.80,
        spread_air=7.00,
        spread_per_shot=0.55,
        spread_max=4.00,
        spread_decay=12.0,
        recoil_pitch=0.75,
        recoil_yaw=0.30,
        switch_time=0.35,
    ),
    "knife": WeaponDef(
        id="knife",
        slot=3,
        name="Field Knife",
        mag_size=0,
        reserve_max=0,
        rpm=130.0,
        auto=False,
        damage=55.0,
        headshot_mult=1.6,
        armor_pen=0.90,
        range=1.9,
        falloff_start=1.9,
        falloff_end=1.9,
        falloff_min=1.0,
        reload_time=0.0,
        spread_base=0.0,
        spread_move=0.0,
        spread_air=0.0,
        spread_per_shot=0.0,
        spread_max=0.0,
        spread_decay=0.0,
        recoil_pitch=0.0,
        recoil_yaw=0.0,
        switch_time=0.25,
        melee=True,
    ),
}

SLOT_TO_WEAPON: Dict[int, str] = {w.slot: w.id for w in WEAPONS.values()}
DEFAULT_LOADOUT: List[str] = ["rifle", "pistol", "knife"]

# Simplified vertical recoil pattern for the rifle, sampled per shot index. Values are
# multipliers on WeaponDef.recoil_pitch: climbs fast, then plateaus and drifts sideways.
RIFLE_PATTERN: List[float] = [
    0.35, 0.55, 0.80, 1.00, 1.15, 1.25, 1.30, 1.30, 1.25, 1.20,
    1.10, 1.05, 1.00, 0.95, 0.92, 0.90, 0.88, 0.86, 0.85, 0.85,
]
# Horizontal component: alternates sides so a long spray traces an arc rather than a line.
RIFLE_YAW_PATTERN: List[float] = [
    0.0, 0.1, -0.2, 0.35, -0.45, 0.6, -0.75, 0.85, -0.9, 1.0,
    -0.85, 0.7, -0.55, 0.4, -0.3, 0.25, -0.2, 0.15, -0.1, 0.1,
]

BURST_RESET_TIME = 0.35  # seconds without firing before the pattern resets


# ─── Bots ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BotTuning:
    aim_error_deg: float
    aim_converge: float  # degrees of error removed per second while tracking
    aim_min_deg: float
    aim_speed: float  # radians/second the bot can turn
    reaction_time: float
    fov_deg: float
    sight_range: float
    burst_min: int
    burst_max: int
    burst_pause: float
    strafe_period: float
    retreat_health: int
    reload_threshold: float  # reload when magazine falls below this fraction
    preferred_range: float
    accuracy_move_penalty: float


BOT_TUNING: Dict[str, BotTuning] = {
    "easy": BotTuning(
        aim_error_deg=9.0, aim_converge=2.0, aim_min_deg=2.5, aim_speed=3.2,
        reaction_time=0.55, fov_deg=100.0, sight_range=45.0,
        burst_min=2, burst_max=4, burst_pause=0.55, strafe_period=1.4,
        retreat_health=25, reload_threshold=0.15, preferred_range=16.0,
        accuracy_move_penalty=1.6,
    ),
    "normal": BotTuning(
        aim_error_deg=6.0, aim_converge=3.5, aim_min_deg=1.4, aim_speed=4.6,
        reaction_time=0.34, fov_deg=110.0, sight_range=60.0,
        burst_min=3, burst_max=6, burst_pause=0.38, strafe_period=1.0,
        retreat_health=30, reload_threshold=0.2, preferred_range=14.0,
        accuracy_move_penalty=1.3,
    ),
    "hard": BotTuning(
        aim_error_deg=3.5, aim_converge=6.0, aim_min_deg=0.7, aim_speed=6.5,
        reaction_time=0.20, fov_deg=120.0, sight_range=75.0,
        burst_min=4, burst_max=8, burst_pause=0.26, strafe_period=0.8,
        retreat_health=35, reload_threshold=0.25, preferred_range=12.0,
        accuracy_move_penalty=1.1,
    ),
    "expert": BotTuning(
        aim_error_deg=2.0, aim_converge=9.0, aim_min_deg=0.3, aim_speed=9.0,
        reaction_time=0.12, fov_deg=140.0, sight_range=90.0,
        burst_min=5, burst_max=10, burst_pause=0.18, strafe_period=0.6,
        retreat_health=40, reload_threshold=0.3, preferred_range=10.0,
        accuracy_move_penalty=1.0,
    ),
}

BOT_NAMES: List[str] = [
    "Ash", "Bishop", "Cinder", "Dagger", "Echo", "Flint", "Ghost", "Halo",
    "Iris", "Jackal", "Koda", "Lynx", "Mako", "Nomad", "Onyx", "Pike",
    "Quill", "Raven", "Sable", "Talon", "Umber", "Vex", "Wraith", "Yara",
]


# ─── Client-visible config bundle ─────────────────────────────────────────────


def client_config(settings: Optional[Settings] = None) -> dict:
    s = settings or get_settings()
    return {
        "tickHz": s.tick_hz,
        "snapshotHz": s.snapshot_hz,
        "interpDelayMs": s.interp_delay_ms,
        "playerRadius": MOVE.player_radius,
        "playerHeight": MOVE.player_height,
        "eyeHeight": MOVE.eye_height,
        "gravity": MOVE.gravity,
        "jumpSpeed": MOVE.jump_speed,
        "walkSpeed": MOVE.walk_speed,
        "sprintSpeed": MOVE.sprint_speed,
        "crouchSpeed": MOVE.crouch_speed,
        "groundAccel": MOVE.ground_accel,
        "airAccel": MOVE.air_accel,
        "airCap": MOVE.air_cap,
        "friction": MOVE.friction,
        "stopSpeed": MOVE.stop_speed,
        "stepHeight": MOVE.step_height,
        "maxFallSpeed": MOVE.max_fall_speed,
        "maxHealth": MOVE.max_health,
        "respawnSeconds": s.respawn_seconds,
        "scoreLimit": s.score_limit,
        "roundSeconds": s.round_seconds,
    }


def client_weapons() -> List[dict]:
    out = []
    for wid in DEFAULT_LOADOUT:
        w = WEAPONS[wid]
        out.append(
            {
                "id": w.id,
                "slot": w.slot,
                "name": w.name,
                "magSize": w.mag_size,
                "reserveMax": w.reserve_max,
                "rpm": w.rpm,
                "auto": w.auto,
                "damage": w.damage,
                "headshotMult": w.headshot_mult,
                "range": w.range,
                "reloadTime": w.reload_time,
                "spreadBase": w.spread_base,
                "spreadMove": w.spread_move,
                "spreadAir": w.spread_air,
                "spreadPerShot": w.spread_per_shot,
                "spreadMax": w.spread_max,
                "spreadDecay": w.spread_decay,
                "recoilPitch": w.recoil_pitch,
                "recoilYaw": w.recoil_yaw,
                "switchTime": w.switch_time,
                "melee": w.melee,
            }
        )
    return out


DEG = math.pi / 180.0
