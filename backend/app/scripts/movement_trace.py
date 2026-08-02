# path: backend/app/scripts/movement_trace.py
"""Run a fixed set of movement scenarios and print the results as JSON.

Paired with ``frontend/tools/movement-trace.ts``, which runs the identical scenarios
through the TypeScript port. ``scripts/check-parity.py`` diffs the two outputs.

This is the guard against the single most annoying class of bug in this codebase: the
client and server movement integrators drifting apart, which players experience as
constant rubber-banding and which no unit test on either side alone can catch.

Run: ``python -m app.scripts.movement_trace``
"""

from __future__ import annotations

import argparse
import json
import math
from typing import List

from ..config import get_settings
from ..game.mathx import Vec3
from ..game.movement import step_movement
from ..game.world import load_world
from ..protocol import K_BACK, K_FORWARD, K_JUMP, K_LEFT, K_RIGHT, K_SPRINT

DT = 1.0 / 60.0

# (name, start, yaw, keys-per-tick callable descriptor, ticks)
SCENARIOS = [
    {"name": "walk_forward", "start": [0.0, 0.5, -20.0], "yaw": 0.0, "keys": K_FORWARD, "ticks": 120},
    {"name": "sprint_diagonal", "start": [0.0, 0.5, -20.0], "yaw": 0.7, "keys": K_FORWARD | K_RIGHT | K_SPRINT, "ticks": 150},
    {"name": "strafe_left", "start": [-12.0, 0.5, 0.0], "yaw": 1.9, "keys": K_LEFT, "ticks": 90},
    {"name": "backpedal", "start": [6.0, 0.5, 14.0], "yaw": 2.4, "keys": K_BACK, "ticks": 90},
    {"name": "bunny_hop", "start": [0.0, 0.5, -18.0], "yaw": 0.0, "keys": K_FORWARD | K_JUMP, "ticks": 180},
    {"name": "into_building_wall", "start": [4.0, 0.5, 12.0], "yaw": 0.0, "keys": K_FORWARD | K_SPRINT, "ticks": 200},
    {"name": "climb_platform_stairs", "start": [19.0, 0.5, 9.0], "yaw": 0.0, "keys": K_FORWARD, "ticks": 90},
    {"name": "fall_from_height", "start": [0.0, 8.0, 0.0], "yaw": 0.0, "keys": 0, "ticks": 120},
    {"name": "air_control", "start": [-19.0, 6.0, 0.0], "yaw": 1.2, "keys": K_FORWARD | K_RIGHT, "ticks": 120},
    {"name": "corner_slide", "start": [8.6, 0.5, -8.0], "yaw": 0.6, "keys": K_FORWARD, "ticks": 160},
]


def run(map_name: str) -> List[dict]:
    world = load_world(map_name)
    out = []
    for sc in SCENARIOS:
        pos = Vec3.from_seq(sc["start"])
        vel = Vec3()
        grounded = False
        for _ in range(int(sc["ticks"])):
            result = step_movement(
                world, pos, vel, float(sc["yaw"]), int(sc["keys"]), DT, grounded
            )
            grounded = result.grounded
        out.append(
            {
                "name": sc["name"],
                "pos": [round(pos.x, 5), round(pos.y, 5), round(pos.z, 5)],
                "vel": [round(vel.x, 5), round(vel.y, 5), round(vel.z, 5)],
                "grounded": grounded,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Movement trace for parity checking.")
    parser.add_argument("--map", default=None)
    args = parser.parse_args()
    map_name = args.map or get_settings().map_name
    print(json.dumps({"map": map_name, "dt": DT, "runs": run(map_name)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
