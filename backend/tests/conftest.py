# path: backend/tests/conftest.py
"""Shared fixtures.

Tests build their own tiny worlds rather than loading the shipped map: a test that fails
should point at the physics, not at whatever someone changed in gen_map.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `import app...` when pytest is run from the backend/ directory or the repo root.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings  # noqa: E402
from app.game.entities import Entity  # noqa: E402
from app.game.mathx import Vec3  # noqa: E402
from app.game.world import World  # noqa: E402


def make_world(extra_boxes=None) -> World:
    """A 40x40 room: floor, four walls, plus whatever the test adds."""
    boxes = [
        {"p": [0, -0.5, 0], "s": [40, 1, 40], "m": "floor"},
        {"p": [0, 3, -20.5], "s": [41, 6, 1], "m": "wall"},
        {"p": [0, 3, 20.5], "s": [41, 6, 1], "m": "wall"},
        {"p": [-20.5, 3, 0], "s": [1, 6, 40], "m": "wall"},
        {"p": [20.5, 3, 0], "s": [1, 6, 40], "m": "wall"},
    ]
    if extra_boxes:
        boxes.extend(extra_boxes)
    return World(
        {
            "name": "test",
            "bounds": [-20, -1, -20, 20, 9, 20],
            "boxes": boxes,
            "spawns": {
                "A": [{"p": [0, 0.05, -15], "yaw": 3.14159}],
                "B": [{"p": [0, 0.05, 15], "yaw": 0.0}],
            },
        }
    )


@pytest.fixture
def world() -> World:
    return make_world()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        tick_hz=60,
        snapshot_hz=30,
        bots_per_team=2,
        warmup_seconds=0,
        respawn_seconds=1.0,
        round_seconds=60,
        score_limit=5,
    )


def make_entity(eid: int = 1, team: str = "A", pos: Vec3 = None, is_bot: bool = False) -> Entity:
    ent = Entity(eid, f"E{eid}", team, is_bot=is_bot, sid=f"sid{eid}")
    ent.spawn(pos or Vec3(0.0, 0.0, 0.0), 0.0, 0.0, protect=0.0)
    return ent
