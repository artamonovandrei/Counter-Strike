# path: backend/tests/test_room.py
"""Room integration: joining, snapshots, scoring, respawn and the HTTP surface.

These drive the real ``Room`` with a fake emitter, so they cover the seams that unit tests
miss — input queue draining, snapshot shape, and the round controller.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

import pytest

from app.config import Settings
from app.game.room import Room
from app.protocol import F_DEAD, InputCmd, K_FIRE, K_FORWARD, PROTOCOL_VERSION
from conftest import make_world

DT = 1.0 / 60.0


class FakeEmitter:
    """Captures everything the room would have sent."""

    def __init__(self) -> None:
        self.sent: List[Tuple[str, Any, Optional[str], Optional[str]]] = []

    async def __call__(self, event: str, data: Any, to: Optional[str] = None, room: Optional[str] = None) -> None:
        self.sent.append((event, data, to, room))

    def events(self, name: str) -> List[dict]:
        out = []
        for event, data, _to, _room in self.sent:
            if event == "ev":
                out.extend(e for e in data if e.get("e") == name)
        return out

    def snapshots(self, sid: Optional[str] = None) -> List[dict]:
        return [d for e, d, to, _r in self.sent if e == "snap" and (sid is None or to == sid)]


@pytest.fixture
def room(monkeypatch, settings) -> Room:
    # Use the tiny test world rather than the shipped map.
    import app.game.world as world_mod
    import app.game.nav as nav_mod

    monkeypatch.setattr(world_mod, "load_world", lambda name: make_world())
    monkeypatch.setattr("app.game.room.load_world", lambda name: make_world())
    monkeypatch.setattr("app.game.room.load_nav", lambda name: nav_mod.NavGraph([], []))
    s = settings.model_copy(update={"bots_per_team": 0, "bot_fill": False})
    return Room("test", s, emitter=None)


def advance(room: Room, ticks: int) -> None:
    for _ in range(ticks):
        room.step(DT)


def go_live(room: Room, settle_ticks: int = 90) -> None:
    """Start the round and let spawn protection and the weapon draw expire.

    Tests that shoot or take damage need this: a freshly spawned player is invulnerable
    for a second and is still drawing their rifle, both of which are correct behaviour
    and both of which silently swallow the thing under test.
    """
    room.begin_live()
    advance(room, settle_ticks)
    for ent in room.entities.values():
        ent.spawn_protect_until = 0.0


def queue(ent, seq: int, keys: int = 0, yaw: float = 0.0, pitch: float = 0.0, weapon: int = 0) -> None:
    ent.input_queue.append(InputCmd(seq, DT, keys, yaw, pitch, weapon))


# ── joining and teams ─────────────────────────────────────────────────────────

def test_join_assigns_alternating_teams(room):
    a = room.add_player("s1", "Ann")
    b = room.add_player("s2", "Ben")
    assert {a.team, b.team} == {"A", "B"}
    assert room.human_count() == 2


def test_join_honours_a_team_request_when_balanced(room):
    a = room.add_player("s1", "Ann", team="B")
    assert a.team == "B"


def test_team_request_is_overridden_when_it_would_stack(room):
    room.add_player("s1", "Ann", team="B")
    second = room.add_player("s2", "Ben", team="B")
    assert second.team == "A", "must not let one team run two players up"


def test_leaving_removes_the_entity(room):
    room.add_player("s1", "Ann")
    assert room.remove_player("s1") is not None
    assert room.human_count() == 0
    assert room.remove_player("s1") is None


def test_players_spawn_alive_and_inside_the_map(room):
    ent = room.add_player("s1", "Ann")
    assert ent.alive
    b = room.world.bounds
    assert b[0] < ent.pos.x < b[3] and b[2] < ent.pos.z < b[5]


def test_room_reports_full_at_capacity(room):
    for i in range(room.settings.room_max_players):
        room.add_player(f"s{i}", f"P{i}")
    assert room.is_full()


# ── simulation ────────────────────────────────────────────────────────────────

def test_queued_input_moves_the_player(room):
    ent = room.add_player("s1", "Ann")
    start_z = ent.pos.z
    for i in range(1, 61):
        queue(ent, i, K_FORWARD, yaw=0.0)
        room.step(DT)
    assert ent.pos.z < start_z - 1.0
    assert ent.last_input_seq == 60


def test_duplicate_sequence_numbers_are_ignored(room):
    ent = room.add_player("s1", "Ann")
    queue(ent, 5, K_FORWARD)
    room.step(DT)
    moved = ent.pos.z
    queue(ent, 5, K_FORWARD)  # replayed
    queue(ent, 4, K_FORWARD)  # out of order
    room.step(DT)
    assert ent.pos.z == pytest.approx(moved, abs=1e-9)


def test_input_flood_is_capped(room):
    """A client sending 60 commands in one tick must not travel 60 ticks' worth."""
    ent = room.add_player("s1", "Ann")
    advance(room, 30)
    ent.vel.set(0.0, 0.0, 0.0)
    start = ent.pos.copy()

    for i in range(1, 61):
        queue(ent, ent.last_input_seq + i, K_FORWARD)
    room.step(DT)

    assert len(ent.input_queue) == 0, "surplus is discarded, never banked"
    # Movement is bounded by the catch-up budget: a few ticks of acceleration from rest,
    # nowhere near a full second of running.
    assert ent.pos.distance(start) < 0.6


def test_honest_client_is_not_penalised(room):
    """One command per tick must be consumed in full, every tick."""
    ent = room.add_player("s1", "Ann")
    advance(room, 30)
    for i in range(60):
        queue(ent, ent.last_input_seq + 1, K_FORWARD)
        room.step(DT)
        assert not ent.input_queue, "a well-behaved client's input must never back up"


def test_gravity_applies_without_any_input(room):
    ent = room.add_player("s1", "Ann")
    ent.pos.y = 5.0
    ent.grounded = False
    advance(room, 120)
    assert ent.pos.y == pytest.approx(0.0, abs=0.05)


# ── snapshots ─────────────────────────────────────────────────────────────────

def test_snapshot_shape_and_cadence(settings, monkeypatch):
    import app.game.nav as nav_mod

    monkeypatch.setattr("app.game.room.load_world", lambda name: make_world())
    monkeypatch.setattr("app.game.room.load_nav", lambda name: nav_mod.NavGraph([], []))
    emitter = FakeEmitter()
    s = settings.model_copy(update={"bots_per_team": 0, "bot_fill": False})
    r = Room("test", s, emitter=emitter)
    ent = r.add_player("s1", "Ann")
    r.add_player("s2", "Ben")

    async def drive():
        for _ in range(60):
            r.step(DT)
            await r.flush()

    asyncio.run(drive())

    snaps = emitter.snapshots("s1")
    # 60 ticks at 60 Hz with a 30 Hz snapshot rate -> 30 snapshots.
    assert len(snaps) == 30

    snap = snaps[-1]
    assert set(snap) == {"t", "st", "ack", "self", "ents", "sc", "ph", "pt"}
    assert snap["self"]["hp"] == 100
    assert all(e["id"] != ent.eid for e in snap["ents"]), "you are never in your own ents list"
    assert snap["ents"][0]["t"] in ("A", "B")
    assert snap["sc"] == {"A": 0, "B": 0}


def test_snapshot_ack_reflects_the_last_consumed_input(settings, monkeypatch):
    import app.game.nav as nav_mod

    monkeypatch.setattr("app.game.room.load_world", lambda name: make_world())
    monkeypatch.setattr("app.game.room.load_nav", lambda name: nav_mod.NavGraph([], []))
    emitter = FakeEmitter()
    s = settings.model_copy(update={"bots_per_team": 0, "bot_fill": False})
    r = Room("test", s, emitter=emitter)
    ent = r.add_player("s1", "Ann")

    async def drive():
        for i in range(1, 11):
            queue(ent, i * 3, K_FORWARD)
            r.step(DT)
            await r.flush()

    asyncio.run(drive())
    assert emitter.snapshots("s1")[-1]["ack"] == 30


# ── combat and scoring ────────────────────────────────────────────────────────

def test_kill_updates_scores_and_schedules_respawn(room):
    killer = room.add_player("s1", "Ann")
    victim = room.add_player("s2", "Ben")
    go_live(room)

    room.damage_entity(victim, killer, 500, 1.0, "rifle", True)

    assert not victim.alive
    assert victim.deaths == 1
    assert killer.kills == 1
    assert room.scores[killer.team] == 1
    assert victim.respawn_at > room.time


def test_dead_player_respawns_after_the_delay(room):
    killer = room.add_player("s1", "Ann")
    victim = room.add_player("s2", "Ben")
    go_live(room)
    room.damage_entity(victim, killer, 500, 1.0, "rifle", False)

    advance(room, int(room.settings.respawn_seconds * 60) + 5)
    assert victim.alive
    assert victim.health == 100


def test_dead_players_are_flagged_in_snapshots(room):
    a = room.add_player("s1", "Ann")
    b = room.add_player("s2", "Ben")
    go_live(room)
    room.damage_entity(b, a, 500, 1.0, "rifle", False)
    assert b.to_ent_state()["f"] & F_DEAD


def test_reaching_the_score_limit_ends_the_round(room):
    killer = room.add_player("s1", "Ann")
    victim = room.add_player("s2", "Ben")
    go_live(room)
    for _ in range(room.settings.score_limit):
        victim.alive = True
        victim.health = 100
        victim.spawn_protect_until = 0.0
        room.damage_entity(victim, killer, 500, 1.0, "rifle", False)
    assert room.phase == "intermission"
    assert room.winner == killer.team


def test_round_timer_ends_the_round(room):
    room.add_player("s1", "Ann")
    room.begin_live()
    room.phase_ends_at = room.time + 0.05
    advance(room, 10)
    assert room.phase == "intermission"


def test_intermission_returns_to_live(room):
    room.add_player("s1", "Ann")
    room.begin_live()
    room.end_round("A")
    room.phase_ends_at = room.time + 0.05
    advance(room, 10)
    assert room.phase == "live"
    assert room.scores == {"A": 0, "B": 0}


def test_no_damage_during_intermission(room):
    a = room.add_player("s1", "Ann")
    b = room.add_player("s2", "Ben")
    room.phase = "intermission"
    room.damage_entity(b, a, 500, 1.0, "rifle", False)
    assert b.alive and b.health == 100


def test_firing_consumes_ammo_and_emits_a_shot(room):
    ent = room.add_player("s1", "Ann")
    go_live(room)
    before = ent.arsenal.ammo()

    queue(ent, ent.last_input_seq + 1, K_FIRE)
    room.step(DT)

    assert ent.arsenal.ammo() == before - 1
    assert any(e["e"] == "shot" for e in room._broadcast)


def test_cannot_fire_while_still_drawing_a_swapped_weapon(room):
    """Swapping starts a draw animation; the trigger must do nothing until it finishes.

    Spawning, by contrast, hands you a ready weapon — you should never lose a spawn duel
    to an animation you didn't ask for.
    """
    ent = room.add_player("s1", "Ann")
    go_live(room)

    queue(ent, ent.last_input_seq + 1, K_FIRE, weapon=2)  # swap to pistol and hold fire
    room.step(DT)
    assert ent.arsenal.current == "pistol"
    assert ent.arsenal.ammo() == 12, "still drawing, so nothing fired"

    # Let the draw finish, then release and re-press (the pistol is semi-auto).
    for _ in range(40):
        queue(ent, ent.last_input_seq + 1, 0)
        room.step(DT)
    queue(ent, ent.last_input_seq + 1, K_FIRE)
    room.step(DT)
    assert ent.arsenal.ammo() == 11


def test_recoil_kick_is_sent_only_to_the_shooter(room):
    ent = room.add_player("s1", "Ann")
    room.add_player("s2", "Ben")
    go_live(room)
    room._private.clear()
    queue(ent, ent.last_input_seq + 1, K_FIRE)
    room.step(DT)

    kicks = [e for e in room._private.get("s1", []) if e.get("e") == "kick"]
    assert len(kicks) == 1
    assert "s2" not in room._private or not [
        e for e in room._private["s2"] if e.get("e") == "kick"
    ]


# ── bots ──────────────────────────────────────────────────────────────────────

def test_bot_fill_balances_teams(settings, monkeypatch):
    import app.game.nav as nav_mod

    monkeypatch.setattr("app.game.room.load_world", lambda name: make_world())
    monkeypatch.setattr("app.game.room.load_nav", lambda name: nav_mod.NavGraph([], []))
    s = settings.model_copy(update={"bots_per_team": 3, "bot_fill": True})
    r = Room("test", s, emitter=None)
    r.sync_bots()
    assert r.bot_count("A") == 3 and r.bot_count("B") == 3

    r.add_player("s1", "Ann", team="A")
    assert r.bot_count("A") == 2, "a human takes a bot's slot"
    assert r.bot_count("B") == 3

    r.remove_player("s1")
    assert r.bot_count("A") == 3


def test_bots_never_receive_snapshots(settings, monkeypatch):
    import app.game.nav as nav_mod

    monkeypatch.setattr("app.game.room.load_world", lambda name: make_world())
    monkeypatch.setattr("app.game.room.load_nav", lambda name: nav_mod.NavGraph([], []))
    emitter = FakeEmitter()
    s = settings.model_copy(update={"bots_per_team": 2, "bot_fill": True})
    r = Room("test", s, emitter=emitter)
    r.sync_bots()

    async def drive():
        for _ in range(4):
            r.step(DT)
            await r.flush()

    asyncio.run(drive())
    assert emitter.snapshots() == []


# ── welcome payload ───────────────────────────────────────────────────────────

def test_welcome_carries_everything_the_client_needs(room):
    ent = room.add_player("s1", "Ann")
    w = room.welcome_payload(ent)
    assert w["protocol"] == PROTOCOL_VERSION
    assert w["playerId"] == ent.eid
    assert w["team"] in ("A", "B")
    assert w["config"]["tickHz"] == room.settings.tick_hz
    # The full table, not just this player's loadout: the client has to render everyone.
    assert {x["id"] for x in w["weapons"]} == {
        "rifle", "smg", "sniper", "shotgun", "pistol", "knife",
    }
    assert w["primary"] in ("rifle", "smg", "sniper", "shotgun")
    assert w["map"]["boxes"], "the client builds its scene from this"


def test_metrics_and_info_are_serialisable(room):
    room.add_player("s1", "Ann")
    advance(room, 10)
    import json

    json.dumps(room.info())
    json.dumps(room.metrics())
