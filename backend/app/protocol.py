# path: backend/app/protocol.py
"""Wire protocol. Authoritative counterpart of shared/protocol.ts.

Hot-path messages (``input``, ``snapshot``) are plain dicts with terse keys: they are
serialised 30-60 times a second per player and pydantic validation there would dominate
the tick budget. They get hand-rolled, allocation-light validation instead
(:func:`parse_input`). Handshake messages, which happen once per connection, use pydantic.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "1.1.0"

NS_LOBBY = "/lobby"
NS_GAME = "/game"

# ─── Key bitmask (client → server) ────────────────────────────────────────────
K_FORWARD = 1 << 0
K_BACK = 1 << 1
K_LEFT = 1 << 2
K_RIGHT = 1 << 3
K_JUMP = 1 << 4
K_SPRINT = 1 << 5
K_FIRE = 1 << 6
K_RELOAD = 1 << 7
K_CROUCH = 1 << 8
K_ADS = 1 << 9

K_ALL = (
    K_FORWARD | K_BACK | K_LEFT | K_RIGHT | K_JUMP | K_SPRINT | K_FIRE | K_RELOAD
    | K_CROUCH | K_ADS
)

# ─── Entity state flags (server → client) ─────────────────────────────────────
F_DEAD = 1 << 0
F_GROUNDED = 1 << 1
F_RELOADING = 1 << 2
F_SPRINTING = 1 << 3
F_MOVING = 1 << 4
F_BOT = 1 << 5
F_ADS = 1 << 6
F_AIRBORNE = 1 << 7

Team = Literal["A", "B"]
Phase = Literal["warmup", "live", "intermission"]

TEAMS: tuple = ("A", "B")

TEAM_NAMES: Dict[str, str] = {"A": "Vanguard", "B": "Insurgents"}


def other_team(team: str) -> str:
    return "B" if team == "A" else "A"


# ─── Handshake models ─────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"[^A-Za-z0-9 _\-\.\[\]]")
MAX_NAME_LEN = 16
MAX_CHAT_LEN = 120


def sanitize_name(raw: Any, fallback: str = "Recruit") -> str:
    """Strip anything that isn't safe to render, collapse whitespace, clamp length."""
    if not isinstance(raw, str):
        return fallback
    cleaned = _NAME_RE.sub("", raw).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned[:MAX_NAME_LEN].strip()
    return cleaned or fallback


def sanitize_chat(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    cleaned = "".join(ch for ch in raw if ch.isprintable())
    return cleaned.strip()[:MAX_CHAT_LEN]


class FindMatchRequest(BaseModel):
    protocol: str = ""
    name: str = "Recruit"
    team: Optional[Literal["A", "B"]] = None
    primary: Optional[str] = None


class JoinRequest(BaseModel):
    protocol: str = ""
    ticket: str = ""


class ChatRequest(BaseModel):
    msg: str = Field(default="", max_length=512)


# ─── Hot-path input parsing ───────────────────────────────────────────────────

MAX_INPUT_DT_MS = 100.0
MIN_INPUT_DT_MS = 1.0
_HALF_PI = math.pi * 0.5
_TWO_PI = math.pi * 2.0


class InputCmd:
    """A single client command. ``__slots__`` because thousands exist per second."""

    __slots__ = ("seq", "dt", "keys", "yaw", "pitch", "weapon")

    def __init__(self, seq: int, dt: float, keys: int, yaw: float, pitch: float, weapon: int):
        self.seq = seq
        self.dt = dt
        self.keys = keys
        self.yaw = yaw
        self.pitch = pitch
        self.weapon = weapon

    def pressed(self, mask: int) -> bool:
        return (self.keys & mask) != 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<InputCmd seq={self.seq} dt={self.dt:.4f} keys={self.keys:#x}>"


def _wrap_angle(a: float) -> float:
    """Normalise to (-pi, pi]. Guards against a client sending a drifting yaw."""
    a = math.fmod(a + math.pi, _TWO_PI)
    if a < 0.0:
        a += _TWO_PI
    return a - math.pi


def parse_input(raw: Any) -> Optional[InputCmd]:
    """Validate and coerce one raw ``input`` payload. Returns None if unusable.

    Everything is clamped rather than rejected where a clamp is safe, so a client with a
    hitching frame degrades instead of desyncing. dt is clamped hard: it is the single
    most abusable field, since a large dt buys distance.
    """
    if not isinstance(raw, dict):
        return None
    try:
        seq = int(raw["s"])
        dt_ms = float(raw["dt"])
        keys = int(raw["k"])
        yaw = float(raw["y"])
        pitch = float(raw["p"])
        weapon = int(raw.get("w", 0))
    except (KeyError, TypeError, ValueError):
        return None

    if seq < 0 or seq > 0x7FFFFFFF:
        return None
    if not (math.isfinite(yaw) and math.isfinite(pitch) and math.isfinite(dt_ms)):
        return None

    dt_ms = min(max(dt_ms, MIN_INPUT_DT_MS), MAX_INPUT_DT_MS)
    keys &= K_ALL
    pitch = min(max(pitch, -_HALF_PI + 1e-3), _HALF_PI - 1e-3)
    weapon = weapon if 0 <= weapon <= 3 else 0

    return InputCmd(seq, dt_ms / 1000.0, keys, _wrap_angle(yaw), pitch, weapon)


def parse_input_batch(raw: Any, limit: int) -> List[InputCmd]:
    """Parse an ``input_batch`` payload, keeping at most ``limit`` commands.

    The *newest* commands are kept when a client floods us: dropping old ones costs a
    little prediction accuracy, dropping new ones would make the player feel frozen.
    """
    if isinstance(raw, dict):
        raw = raw.get("c")
    if not isinstance(raw, list):
        return []
    if len(raw) > limit:
        raw = raw[-limit:]
    out: List[InputCmd] = []
    for item in raw:
        cmd = parse_input(item)
        if cmd is not None:
            out.append(cmd)
    return out
