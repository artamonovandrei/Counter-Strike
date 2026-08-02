# path: backend/app/game/room.py
"""A single match: the fixed-timestep loop, the round controller, and snapshot assembly.

One room owns its entities and runs entirely inside one asyncio task. Nothing outside the
room mutates entity state — sockets push inputs into queues and the loop drains them —
which means there is no locking anywhere and the whole simulation is deterministic given
the same input sequence.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from ..config import (
    BOT_NAMES, BOT_TUNING, DEG, MOVE, SLOT_TO_WEAPON, Settings, WEAPONS, client_config,
    client_weapons,
)
from ..protocol import (
    F_DEAD, InputCmd, K_FIRE, K_RELOAD, PROTOCOL_VERSION, TEAMS, other_team,
)
from .bots import BotBrain, BotManager
from .combat import melee_targets, resolve_shot, rewind_seconds
from .entities import Entity
from .mathx import Vec3, angles_to_dir, clamp
from .movement import fall_damage, step_movement
from .nav import load_nav
from .weapons import damage_at_range
from .world import World, load_world

log = logging.getLogger("webstrike.room")

Emitter = Callable[..., Awaitable[None]]

MAX_QUEUED_INPUTS = 24

# Most simulated time one entity may consume in a single tick, as a multiple of the tick
# length. Above 1.0 so a client recovering from a hitch can catch up; low enough that
# flooding inputs is not a speed hack.
CATCHUP_FACTOR = 3.0


class Room:
    def __init__(
        self,
        room_id: str,
        settings: Settings,
        emitter: Optional[Emitter] = None,
        map_name: Optional[str] = None,
        seed: Optional[int] = None,
    ):
        self.id = room_id
        self.settings = settings
        self.emitter = emitter
        self.map_name = map_name or settings.map_name

        self.world: World = load_world(self.map_name)
        self.nav = load_nav(self.map_name)
        if len(self.nav) == 0:
            log.warning(
                "map '%s' has no nav graph — bots will steer directly and navigate badly. "
                "Run: python -m app.scripts.gen_nav %s",
                self.map_name, self.map_name,
            )

        self.rng = random.Random(seed if seed is not None else int(time.time() * 1000) & 0xFFFFFFFF)
        self.bots = BotManager(
            self.world, self.nav, BOT_TUNING[settings.bot_difficulty],
            settings.bot_logic_hz, seed=self.rng.randrange(1 << 30),
        )

        self.entities: Dict[int, Entity] = {}
        self.by_sid: Dict[str, Entity] = {}
        self._next_eid = 1
        self._used_bot_names: set = set()

        self.tick = 0
        self.time = 0.0  # seconds since room start
        self.started_at = time.monotonic()
        self.running = False
        self._task: Optional[asyncio.Task] = None

        self.phase = "warmup"
        self.phase_ends_at = float(settings.warmup_seconds)
        self.scores: Dict[str, int] = {"A": 0, "B": 0}
        self.winner: Optional[str] = None

        self._broadcast: List[dict] = []
        self._private: Dict[str, List[dict]] = {}
        self._snapshot_counter = 0
        self._ping_seq = 0
        self._next_ping_at = 1.0
        self._pending_pings: Dict[int, Tuple[int, float]] = {}  # seq -> (eid, sent_at)

        # Rolling tick-cost samples for /metrics.
        self.tick_times: List[float] = []
        self.last_activity = time.monotonic()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run(), name=f"room-{self.id}")

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Fixed-timestep loop with accumulator.

        Sleeping until the next deadline rather than for a fixed duration keeps the tick
        rate honest under load: if a tick overruns, the next one starts immediately and
        the room catches up instead of drifting slower and slower.
        """
        dt = self.settings.tick_dt
        next_time = time.monotonic()
        max_catchup = 5
        try:
            while self.running:
                now = time.monotonic()
                behind = 0
                while now >= next_time and behind < max_catchup:
                    t0 = time.perf_counter()
                    self.step(dt)
                    self.tick_times.append(time.perf_counter() - t0)
                    if len(self.tick_times) > 600:
                        del self.tick_times[:300]
                    await self.flush()
                    next_time += dt
                    behind += 1
                if behind >= max_catchup:
                    # Too far behind to catch up; drop the backlog rather than spiral.
                    log.warning("room %s fell behind, dropping %d ticks", self.id, behind)
                    next_time = time.monotonic() + dt
                sleep_for = next_time - time.monotonic()
                await asyncio.sleep(sleep_for if sleep_for > 0.0 else 0.0)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("room %s loop crashed", self.id)
            self.running = False

    # ── population ────────────────────────────────────────────────────────────

    @property
    def humans(self) -> List[Entity]:
        return [e for e in self.entities.values() if not e.is_bot]

    @property
    def bot_list(self) -> List[Entity]:
        return [e for e in self.entities.values() if e.is_bot]

    def human_count(self, team: Optional[str] = None) -> int:
        return sum(1 for e in self.entities.values() if not e.is_bot and (team is None or e.team == team))

    def bot_count(self, team: Optional[str] = None) -> int:
        return sum(1 for e in self.entities.values() if e.is_bot and (team is None or e.team == team))

    def is_full(self) -> bool:
        return self.human_count() >= self.settings.room_max_players

    def pick_team(self, preferred: Optional[str] = None) -> str:
        if preferred in TEAMS:
            a, b = self.human_count("A"), self.human_count("B")
            # Honour the request unless it would make the teams lopsided.
            if preferred == "A" and a - b < 1:
                return "A"
            if preferred == "B" and b - a < 1:
                return "B"
        a, b = self.human_count("A"), self.human_count("B")
        if a != b:
            return "A" if a < b else "B"
        ta, tb = len(self.team_members("A")), len(self.team_members("B"))
        return "A" if ta <= tb else "B"

    def team_members(self, team: str) -> List[Entity]:
        return [e for e in self.entities.values() if e.team == team]

    def _alloc_eid(self) -> int:
        eid = self._next_eid
        self._next_eid += 1
        return eid

    def add_player(self, sid: str, name: str, team: Optional[str] = None) -> Entity:
        chosen = self.pick_team(team)
        ent = Entity(self._alloc_eid(), name, chosen, is_bot=False, sid=sid)
        ent.last_seen = self.time
        self.entities[ent.eid] = ent
        self.by_sid[sid] = ent
        self.respawn(ent, immediate=True)
        self.last_activity = time.monotonic()
        self.push_broadcast(
            {"e": "join", "id": ent.eid, "name": ent.name, "team": ent.team, "bot": False}
        )
        self.sync_bots()
        self.push_scoreboard()
        log.info("room %s: %s joined team %s (%d humans)", self.id, name, chosen, self.human_count())
        return ent

    def remove_player(self, sid: str) -> Optional[Entity]:
        ent = self.by_sid.pop(sid, None)
        if ent is None:
            return None
        self.entities.pop(ent.eid, None)
        self.push_broadcast({"e": "leave", "id": ent.eid, "name": ent.name})
        self.sync_bots()
        self.push_scoreboard()
        log.info("room %s: %s left (%d humans)", self.id, ent.name, self.human_count())
        return ent

    def add_bot(self, team: str) -> Entity:
        name = self._bot_name()
        ent = Entity(self._alloc_eid(), name, team, is_bot=True)
        self.entities[ent.eid] = ent
        self.bots.attach(ent)
        self.respawn(ent, immediate=True)
        self.push_broadcast({"e": "join", "id": ent.eid, "name": name, "team": team, "bot": True})
        return ent

    def _bot_name(self) -> str:
        available = [n for n in BOT_NAMES if n not in self._used_bot_names]
        if not available:
            self._used_bot_names.clear()
            available = list(BOT_NAMES)
        name = self.rng.choice(available)
        self._used_bot_names.add(name)
        return name

    def remove_bot(self, team: str) -> None:
        candidates = [e for e in self.entities.values() if e.is_bot and e.team == team]
        if not candidates:
            return
        # Prefer removing a dead bot so nobody sees a body vanish mid-fight.
        candidates.sort(key=lambda e: (e.alive, e.kills))
        victim = candidates[0]
        self.entities.pop(victim.eid, None)
        self._used_bot_names.discard(victim.name)
        self.push_broadcast({"e": "leave", "id": victim.eid, "name": victim.name})

    def sync_bots(self) -> None:
        """Keep each team at the configured size by adding/removing bots."""
        if not self.settings.bot_fill:
            return
        target = self.settings.bots_per_team
        for team in TEAMS:
            desired_bots = max(0, target - self.human_count(team))
            have = self.bot_count(team)
            for _ in range(desired_bots - have):
                self.add_bot(team)
            for _ in range(have - desired_bots):
                self.remove_bot(team)

    # ── spawning ──────────────────────────────────────────────────────────────

    def choose_spawn(self, ent: Entity) -> Tuple[Vec3, float]:
        points = self.world.spawn_points(ent.team)
        if not points:
            return Vec3(0.0, 1.0, 0.0), 0.0

        enemies = [e for e in self.entities.values() if e.team != ent.team and e.alive]
        allies = [e for e in self.entities.values() if e is not ent and e.alive]

        best: Optional[Tuple[Vec3, float]] = None
        best_score = -1e9
        for pos, yaw in points:
            # Far from enemies, not on top of a teammate, small jitter to break ties.
            score = min((pos.distance(e.pos) for e in enemies), default=100.0)
            crowding = min((pos.distance(a.pos) for a in allies), default=100.0)
            if crowding < 1.5:
                score -= 50.0
            score += self.rng.random() * 4.0
            if score > best_score:
                best_score = score
                best = (pos, yaw)
        assert best is not None
        return Vec3(best[0].x, best[0].y, best[0].z), best[1]

    def respawn(self, ent: Entity, immediate: bool = False) -> None:
        pos, yaw = self.choose_spawn(ent)
        ent.spawn(pos, yaw, self.time)
        if ent.is_bot and isinstance(ent.brain, BotBrain):
            ent.brain.reset_on_spawn(self.time)
        self.push_broadcast({"e": "spawn", "id": ent.eid, "p": ent.pos.rounded()})

    # ── the tick ──────────────────────────────────────────────────────────────

    def step(self, dt: float) -> None:
        self.time += dt
        self.tick += 1

        self.update_round(dt)

        living = list(self.entities.values())
        self.bots.update(self.time, dt, [e for e in living if e.is_bot], living)

        for ent in living:
            self.consume_inputs(ent, dt)

        for ent in living:
            ent.record_history(self.time)

        self.handle_respawns()
        self.schedule_pings()

        self._snapshot_counter += 1
        if self._snapshot_counter >= self.settings.snapshot_every:
            self._snapshot_counter = 0
            self.build_snapshots()

    def consume_inputs(self, ent: Entity, tick_dt: float) -> None:
        """Drain queued commands for one entity.

        A client that hitched legitimately needs to catch up, so several commands may be
        consumed in one tick. Two separate ceilings bound the abuse:

        * a **count** ceiling (``max_inputs_per_tick``), and
        * a **time** ceiling — the total simulated ``dt`` consumed in one tick may not
          exceed ``CATCHUP_FACTOR`` times the tick length.

        The time ceiling is the one that matters. Without it a client could send eight
        commands per tick forever and move eight times as fast, because the count limit
        says nothing about how much simulated time each command carries. Surplus is
        discarded rather than deferred: deferring would let a cheater bank movement and
        release it in a burst.
        """
        queue = ent.input_queue
        if not queue:
            # No input this tick: keep simulating so gravity/momentum still apply.
            if ent.alive:
                self.simulate_entity(ent, ent.prev_keys & ~(K_FIRE | K_RELOAD), tick_dt, None)
            return

        budget = self.settings.max_inputs_per_tick
        if len(queue) > budget:
            for _ in range(len(queue) - budget):
                queue.popleft()

        dt_budget = tick_dt * CATCHUP_FACTOR
        spent = 0.0

        while queue:
            if spent >= dt_budget:
                queue.clear()
                break
            cmd: InputCmd = queue.popleft()
            spent += cmd.dt
            if cmd.seq <= ent.last_input_seq and not ent.is_bot:
                continue  # duplicate or out-of-order, already applied
            ent.last_input_seq = cmd.seq
            ent.yaw = cmd.yaw
            ent.pitch = cmd.pitch
            if ent.alive:
                self.simulate_entity(ent, cmd.keys, cmd.dt, cmd)
            ent.prev_keys = cmd.keys

    def simulate_entity(self, ent: Entity, keys: int, dt: float, cmd: Optional[InputCmd]) -> None:
        ars = ent.arsenal
        now = self.time

        if cmd is not None and cmd.weapon:
            if ars.select_slot(cmd.weapon, now):
                self.push_broadcast({"e": "switch", "id": ent.eid, "w": ars.current})

        done = ars.update(now, dt)
        if done == "reload_done" and not ent.is_bot:
            pass  # ammo counts ride along in the snapshot; no separate event needed

        result = step_movement(self.world, ent.pos, ent.vel, ent.yaw, keys, dt, ent.grounded)
        ent.grounded = result.grounded
        if result.landed:
            dmg = fall_damage(result.land_speed)
            if dmg > 0:
                self.damage_entity(ent, None, dmg, 1.0, "fall", False)

        if self.phase == "intermission":
            return

        # Reload on the rising edge only, so holding R doesn't restart the reload forever.
        if keys & K_RELOAD and not (ent.prev_keys & K_RELOAD):
            ars.begin_reload(now)
        elif ars.auto_reload_needed() and not ars.is_reloading(now):
            ars.begin_reload(now)

        trigger = bool(keys & K_FIRE)
        if ars.can_fire(now, trigger):
            self.fire(ent, now)
        ars.trigger_held = trigger

    # ── shooting ──────────────────────────────────────────────────────────────

    def fire(self, ent: Entity, now: float) -> None:
        ars = ent.arsenal
        weapon = ars.definition

        if weapon.melee:
            ars.consume_shot(now)
            self.push_broadcast(
                {
                    "e": "shot", "id": ent.eid, "w": weapon.id,
                    "o": ent.eye_pos().rounded(), "d": ent.look_dir().rounded(),
                }
            )
            victim = melee_targets(ent, self.entities.values(), weapon, self.world)
            if victim is not None:
                # Backstabs hit harder; the multiplier stands in for the head/back split.
                to = (victim.pos - ent.pos).normalized()
                facing = angles_to_dir(victim.yaw, 0.0)
                back = to.dot(facing) > 0.5
                dmg = weapon.damage * (weapon.headshot_mult if back else 1.0)
                self.damage_entity(victim, ent, int(dmg), weapon.armor_pen, weapon.id, back)
            if ent.is_bot and isinstance(ent.brain, BotBrain):
                ent.brain.note_shot_fired(now)
            return

        spread = ars.current_spread_deg(ent.horizontal_speed(), ent.grounded, MOVE.sprint_speed)
        yaw, pitch = ars.apply_spread(ent.yaw, ent.pitch, spread, self.rng)
        direction = angles_to_dir(yaw, pitch)
        origin = ent.eye_pos()

        rewind = 0.0
        if not ent.is_bot:
            rewind = rewind_seconds(
                ent.ping_ms, self.settings.interp_delay_ms, self.settings.lagcomp_max_ms
            )

        result = resolve_shot(
            self.world, ent, list(self.entities.values()), weapon, origin, direction,
            now, rewind=rewind,
        )

        ars.consume_shot(now)
        if ent.is_bot and isinstance(ent.brain, BotBrain):
            ent.brain.note_shot_fired(now)

        self.push_broadcast(
            {
                "e": "shot", "id": ent.eid, "w": weapon.id,
                "o": origin.rounded(), "d": direction.rounded(),
            }
        )

        # Recoil is a view kick the client applies to its own camera; the server sends it
        # rather than modifying the shot, so what the player aims at is what gets traced.
        if ent.sid and (weapon.recoil_pitch or weapon.recoil_yaw):
            kick_yaw, kick_pitch = ars.recoil_kick()
            self.push_private(ent.sid, {"e": "kick", "y": round(kick_yaw, 5), "p": round(kick_pitch, 5)})

        if result.victim is not None:
            self.damage_entity(
                result.victim, ent, result.damage, weapon.armor_pen, weapon.id, result.headshot
            )
        elif result.impact_normal is not None:
            self.push_broadcast(
                {
                    "e": "impact", "p": result.end.rounded(),
                    "n": result.impact_normal.rounded(), "m": result.impact_material,
                }
            )

    def damage_entity(
        self, victim: Entity, attacker: Optional[Entity], amount: int,
        armor_pen: float, weapon_id: str, headshot: bool,
    ) -> None:
        if not victim.alive or self.phase == "intermission":
            return
        attacker_id = attacker.eid if attacker else None
        dealt = victim.apply_damage(amount, armor_pen, attacker_id, self.time)
        if dealt <= 0:
            return

        killed = not victim.alive
        if attacker is not None and attacker is not victim:
            attacker.damage_dealt += dealt
            if attacker.sid:
                self.push_private(
                    attacker.sid,
                    {"e": "hit", "dmg": dealt, "hs": headshot, "kill": killed},
                )
        if victim.sid:
            src = attacker.pos.rounded() if attacker else victim.pos.rounded()
            self.push_private(
                victim.sid, {"e": "hurt", "amt": dealt, "hp": victim.health, "from": src}
            )

        if killed:
            self.on_kill(victim, attacker, weapon_id, headshot)

    def on_kill(
        self, victim: Entity, attacker: Optional[Entity], weapon_id: str, headshot: bool
    ) -> None:
        victim.deaths += 1
        victim.respawn_at = self.time + self.settings.respawn_seconds
        victim.vel.set(0.0, 0.0, 0.0)

        killer_name = "world"
        killer_id = 0
        team = victim.team
        if attacker is not None and attacker is not victim:
            attacker.kills += 1
            attacker.score += 1
            killer_name = attacker.name
            killer_id = attacker.eid
            team = attacker.team
            if self.phase == "live":
                self.scores[attacker.team] += 1
        elif attacker is victim:
            victim.kills -= 1  # suicide costs you
            if self.phase == "live":
                self.scores[other_team(victim.team)] += 0

        self.push_broadcast(
            {
                "e": "kill", "kid": killer_id, "vid": victim.eid,
                "k": killer_name, "v": victim.name, "w": weapon_id,
                "hs": headshot, "team": team,
            }
        )
        self.push_scoreboard()

        if self.phase == "live" and self.scores[team] >= self.settings.score_limit:
            self.end_round(winner=team)

    def handle_respawns(self) -> None:
        if self.phase == "intermission":
            return
        for ent in self.entities.values():
            if not ent.alive and ent.respawn_at and self.time >= ent.respawn_at:
                self.respawn(ent)

    # ── round controller ──────────────────────────────────────────────────────

    def update_round(self, dt: float) -> None:
        if self.time < self.phase_ends_at:
            return
        if self.phase == "warmup":
            self.begin_live()
        elif self.phase == "live":
            a, b = self.scores["A"], self.scores["B"]
            winner = "A" if a > b else ("B" if b > a else "draw")
            self.end_round(winner=winner)
        elif self.phase == "intermission":
            self.begin_live()

    def begin_live(self) -> None:
        self.phase = "live"
        self.winner = None
        self.scores = {"A": 0, "B": 0}
        self.phase_ends_at = self.time + self.settings.round_seconds
        for ent in self.entities.values():
            ent.kills = 0
            ent.deaths = 0
            ent.damage_dealt = 0.0
            self.respawn(ent)
        self.push_round_event()
        log.info("room %s: round started", self.id)

    def end_round(self, winner: str) -> None:
        self.phase = "intermission"
        self.winner = winner
        self.phase_ends_at = self.time + self.settings.intermission_seconds
        self.push_round_event()
        self.push_scoreboard()
        log.info("room %s: round over, winner=%s %s", self.id, winner, self.scores)

    def push_round_event(self) -> None:
        ev = {
            "e": "round",
            "ph": self.phase,
            "pt": round(max(0.0, self.phase_ends_at - self.time), 2),
            "sc": dict(self.scores),
        }
        if self.winner:
            ev["winner"] = self.winner
        self.push_broadcast(ev)

    # ── networking ────────────────────────────────────────────────────────────

    def push_broadcast(self, event: dict) -> None:
        self._broadcast.append(event)

    def push_private(self, sid: str, event: dict) -> None:
        self._private.setdefault(sid, []).append(event)

    def push_scoreboard(self) -> None:
        rows = [e.to_score_row() for e in self.entities.values()]
        rows.sort(key=lambda r: (-r["kills"], r["deaths"], r["name"]))
        self.push_broadcast({"e": "scoreboard", "rows": rows})

    def schedule_pings(self) -> None:
        """Server-driven RTT measurement.

        The server times the round trip itself instead of trusting a client-reported
        number, because that number feeds the lag-compensation rewind.
        """
        if self.time < self._next_ping_at:
            return
        self._next_ping_at = self.time + 1.0
        for ent in self.humans:
            if not ent.sid:
                continue
            self._ping_seq += 1
            self._pending_pings[self._ping_seq] = (ent.eid, self.time)
            self.push_private(ent.sid, {"e": "ping_req", "i": self._ping_seq})
        # Forget unanswered probes so the dict can't grow without bound.
        if len(self._pending_pings) > 256:
            cutoff = self.time - 10.0
            for k in [k for k, v in self._pending_pings.items() if v[1] < cutoff]:
                self._pending_pings.pop(k, None)

    def on_ping_ack(self, ent: Entity, seq: int) -> None:
        pending = self._pending_pings.pop(seq, None)
        if pending is None or pending[0] != ent.eid:
            return
        rtt_ms = max(0.0, (self.time - pending[1]) * 1000.0)
        # Exponential smoothing: a single spike shouldn't move the rewind window much.
        ent.ping_ms = rtt_ms if ent.ping_ms <= 0.0 else ent.ping_ms * 0.7 + rtt_ms * 0.3
        ent.last_seen = self.time

    def build_snapshots(self) -> None:
        st = round(self.time * 1000.0, 1)
        ents_all = list(self.entities.values())
        # One shared list; per-player differences are only in `self` and `ack`.
        ent_states = [e.to_ent_state() for e in ents_all]
        scores = dict(self.scores)
        phase_left = round(max(0.0, self.phase_ends_at - self.time), 2)

        for ent in ents_all:
            if ent.is_bot or not ent.sid:
                continue
            payload = {
                "t": self.tick,
                "st": st,
                "ack": ent.last_input_seq,
                "self": ent.to_self_state(self.time),
                "ents": [e for e in ent_states if e["id"] != ent.eid],
                "sc": scores,
                "ph": self.phase,
                "pt": phase_left,
            }
            self.push_private(ent.sid, {"__snap": payload})

    async def flush(self) -> None:
        """Send everything accumulated this tick. One emit per socket where possible."""
        if self.emitter is None:
            self._broadcast.clear()
            self._private.clear()
            return

        broadcast = self._broadcast
        private = self._private
        self._broadcast = []
        self._private = {}

        if broadcast:
            await self.emitter("ev", broadcast, room=self.id)

        for sid, events in private.items():
            snaps = [e["__snap"] for e in events if "__snap" in e]
            rest = [e for e in events if "__snap" not in e]
            if rest:
                await self.emitter("ev", rest, to=sid)
            for snap in snaps:
                await self.emitter("snap", snap, to=sid)

    # ── introspection ─────────────────────────────────────────────────────────

    def welcome_payload(self, ent: Entity) -> dict:
        return {
            "protocol": PROTOCOL_VERSION,
            "playerId": ent.eid,
            "roomId": self.id,
            "team": ent.team,
            "name": ent.name,
            "config": client_config(self.settings),
            "weapons": client_weapons(),
            "map": self.world.data,
            "serverTime": round(self.time * 1000.0, 1),
        }

    def info(self) -> dict:
        return {
            "roomId": self.id,
            "players": self.human_count(),
            "bots": self.bot_count(),
            "capacity": self.settings.room_max_players,
            "map": self.map_name,
            "phase": self.phase,
            "scoreA": self.scores["A"],
            "scoreB": self.scores["B"],
        }

    def metrics(self) -> dict:
        times = self.tick_times[-300:]
        avg = sum(times) / len(times) if times else 0.0
        return {
            "roomId": self.id,
            "tick": self.tick,
            "uptime": round(self.time, 1),
            "entities": len(self.entities),
            "humans": self.human_count(),
            "bots": self.bot_count(),
            "tickAvgMs": round(avg * 1000.0, 3),
            "tickMaxMs": round(max(times) * 1000.0, 3) if times else 0.0,
        }
