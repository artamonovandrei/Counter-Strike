# path: backend/app/game/manager.py
"""Room registry and matchmaking.

Deliberately simple: find the fullest room that still has space (so players cluster into
one good match instead of spreading thinly across empty ones), else open a new one.

A room lives in the memory of exactly one process. Running more than one worker therefore
requires Redis for the Socket.IO fan-out *and* a shared registry — see the scaling notes
in the README.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..config import Settings, get_settings
from .room import Emitter, Room

log = logging.getLogger("webstrike.manager")

TICKET_TTL = 60.0  # seconds a lobby ticket stays valid
IDLE_ROOM_TTL = 300.0  # empty rooms are torn down after this long


@dataclass
class Ticket:
    room_id: str
    name: str
    team: Optional[str]
    issued_at: float


class RoomManager:
    def __init__(self, settings: Optional[Settings] = None, emitter: Optional[Emitter] = None):
        self.settings = settings or get_settings()
        self.emitter = emitter
        self.rooms: Dict[str, Room] = {}
        self.tickets: Dict[str, Ticket] = {}
        self._counter = 0

    # ── rooms ─────────────────────────────────────────────────────────────────

    def _new_room_id(self) -> str:
        self._counter += 1
        return f"r{self._counter:03d}"

    def create_room(self) -> Room:
        room = Room(self._new_room_id(), self.settings, emitter=self.emitter)
        self.rooms[room.id] = room
        room.start()
        room.sync_bots()
        log.info("created room %s on map %s", room.id, room.map_name)
        return room

    def find_room(self) -> Optional[Room]:
        """Fullest room with a free slot, or a new one if allowed."""
        open_rooms = [r for r in self.rooms.values() if not r.is_full()]
        if open_rooms:
            open_rooms.sort(key=lambda r: -r.human_count())
            return open_rooms[0]
        if len(self.rooms) >= self.settings.room_max_rooms:
            return None
        return self.create_room()

    def get(self, room_id: str) -> Optional[Room]:
        return self.rooms.get(room_id)

    def list_rooms(self) -> List[dict]:
        return [r.info() for r in self.rooms.values()]

    # ── tickets ───────────────────────────────────────────────────────────────

    def issue_ticket(self, room: Room, name: str, team: Optional[str]) -> str:
        """One-shot credential handing a lobby client over to the game namespace.

        It exists so the /game namespace never has to trust a client-supplied room id or
        name, and so a stale reconnect can't silently rejoin a room it was kicked from.
        """
        self._expire_tickets()
        token = secrets.token_urlsafe(16)
        self.tickets[token] = Ticket(room.id, name, team, time.monotonic())
        return token

    def redeem_ticket(self, token: str) -> Optional[Ticket]:
        self._expire_tickets()
        return self.tickets.pop(token, None)

    def _expire_tickets(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self.tickets.items() if now - v.issued_at > TICKET_TTL]
        for k in stale:
            self.tickets.pop(k, None)

    # ── housekeeping ──────────────────────────────────────────────────────────

    async def reap_idle(self) -> int:
        """Close rooms that have had no humans for a while. Returns how many closed.

        Always keeps one room warm so the first player of the day doesn't wait for a cold
        start (map load + nav parse + bot spawn).
        """
        now = time.monotonic()
        closed = 0
        for room_id, room in list(self.rooms.items()):
            if room.human_count() > 0:
                room.last_activity = now
                continue
            if len(self.rooms) <= 1:
                continue
            if now - room.last_activity > IDLE_ROOM_TTL:
                await room.stop()
                self.rooms.pop(room_id, None)
                closed += 1
                log.info("reaped idle room %s", room_id)
        return closed

    async def shutdown(self) -> None:
        for room in list(self.rooms.values()):
            await room.stop()
        self.rooms.clear()
        self.tickets.clear()

    def metrics(self) -> dict:
        return {
            "rooms": len(self.rooms),
            "players": sum(r.human_count() for r in self.rooms.values()),
            "bots": sum(r.bot_count() for r in self.rooms.values()),
            "capacity": self.settings.room_max_rooms * self.settings.room_max_players,
            "detail": [r.metrics() for r in self.rooms.values()],
        }
