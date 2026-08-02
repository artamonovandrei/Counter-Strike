# path: backend/app/scripts/run_headless_match.py
"""Run a full match with no clients attached.

This is the smoke test that matters: it drives the real room, the real physics and the
real AI, so anything structurally broken (nav graph missing, raycaster returning nothing,
bots stuck on spawn) shows up as zero kills rather than as a subtle bug someone notices
in production.

It exits non-zero when the match looks degenerate, so it works as a CI gate.

Run: ``python -m app.scripts.run_headless_match --bots 8 --ticks 5000``
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from typing import List

from ..config import Settings, get_settings
from ..game.bots import BotBrain, BotState
from ..game.room import Room


def build_settings(args: argparse.Namespace) -> Settings:
    base = get_settings()
    data = base.model_dump()
    data.update(
        {
            "bots_per_team": max(1, args.bots // 2),
            "bot_difficulty": args.difficulty,
            "bot_fill": True,
            "map_name": args.map,
            "round_seconds": args.round_seconds,
            "score_limit": args.score_limit,
            "warmup_seconds": 0,
        }
    )
    return Settings(**data)


async def run(args: argparse.Namespace) -> int:
    settings = build_settings(args)
    room = Room("headless", settings, emitter=None, seed=args.seed)
    room.sync_bots()

    total_bots = room.bot_count()
    print(
        f"map={room.map_name} nav_nodes={len(room.nav)} bots={total_bots} "
        f"tick={settings.tick_hz}Hz ticks={args.ticks} "
        f"({args.ticks / settings.tick_hz:.0f}s of game time)"
    )
    if len(room.nav) == 0:
        print("ERROR: no nav graph. Run: python -m app.scripts.gen_nav", room.map_name)
        return 2
    if total_bots == 0:
        print("ERROR: no bots spawned")
        return 2

    dt = settings.tick_dt
    tick_ms: List[float] = []
    shots_before = 0

    wall_start = time.perf_counter()
    for i in range(args.ticks):
        t0 = time.perf_counter()
        room.step(dt)
        tick_ms.append((time.perf_counter() - t0) * 1000.0)
        await room.flush()  # emitter is None: just drains the outbox
        if args.progress and (i + 1) % args.progress == 0:
            print(
                f"  tick {i + 1:6d}  t={room.time:6.1f}s  "
                f"A={room.scores['A']:3d} B={room.scores['B']:3d}  phase={room.phase}"
            )
    wall = time.perf_counter() - wall_start

    # ── report ────────────────────────────────────────────────────────────────
    tick_ms.sort()
    def pct(p: float) -> float:
        return tick_ms[min(len(tick_ms) - 1, int(len(tick_ms) * p))]

    sim_seconds = args.ticks * dt
    print("\n── timing ──────────────────────────────────────────────")
    print(f"  wall clock      {wall:.2f}s for {sim_seconds:.1f}s simulated "
          f"({sim_seconds / wall:.1f}x realtime)")
    print(f"  tick mean       {statistics.fmean(tick_ms):.3f} ms")
    print(f"  tick p50/p95/p99 {pct(0.5):.3f} / {pct(0.95):.3f} / {pct(0.99):.3f} ms")
    print(f"  tick max        {tick_ms[-1]:.3f} ms  (budget {dt * 1000.0:.1f} ms)")

    entities = sorted(room.entities.values(), key=lambda e: (-e.kills, e.deaths))
    total_kills = sum(e.kills for e in entities)
    total_deaths = sum(e.deaths for e in entities)

    print("\n── scoreboard ──────────────────────────────────────────")
    print(f"  team A {room.scores['A']}   team B {room.scores['B']}   phase={room.phase}")
    print(f"  {'name':<10} {'team':<5} {'K':>4} {'D':>4} {'dmg':>7}  state")
    for e in entities:
        state = ""
        if isinstance(e.brain, BotBrain):
            state = e.brain.state.value
        print(f"  {e.name:<10} {e.team:<5} {e.kills:>4} {e.deaths:>4} {e.damage_dealt:>7.0f}  {state}")

    states: dict = {}
    for e in room.entities.values():
        if isinstance(e.brain, BotBrain):
            states[e.brain.state.value] = states.get(e.brain.state.value, 0) + 1
    print(f"\n  final AI states: {states}")

    # ── sanity gates ──────────────────────────────────────────────────────────
    problems = []
    if total_kills == 0:
        problems.append("no kills — check the raycaster, bot perception, or spawns")
    if total_deaths == 0:
        problems.append("no deaths recorded")
    idle_bots = sum(
        1 for e in room.entities.values()
        if isinstance(e.brain, BotBrain) and e.brain.state is BotState.IDLE
    )
    if idle_bots:
        problems.append(f"{idle_bots} bots never left IDLE — nav graph probably unreachable")
    if tick_ms[-1] > dt * 1000.0 * 4:
        problems.append(f"worst tick {tick_ms[-1]:.1f} ms far exceeds the {dt * 1000.0:.1f} ms budget")

    if problems:
        print("\n── PROBLEMS ────────────────────────────────────────────")
        for p in problems:
            print(f"  ! {p}")
        return 1

    print(f"\nOK — {total_kills} kills over {sim_seconds:.0f}s of play")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless WebStrike match.")
    parser.add_argument("--bots", type=int, default=8, help="total bots (split across teams)")
    parser.add_argument("--ticks", type=int, default=5000)
    parser.add_argument("--map", default=None)
    parser.add_argument("--difficulty", default="normal", choices=["easy", "normal", "hard", "expert"])
    parser.add_argument("--round-seconds", type=int, default=360)
    parser.add_argument("--score-limit", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--progress", type=int, default=0, help="print every N ticks (0 = off)")
    args = parser.parse_args()
    if args.map is None:
        args.map = get_settings().map_name
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
