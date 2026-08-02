# path: backend/app/sio_server.py
"""Socket.IO surface: the ``/lobby`` and ``/game`` namespaces.

Split by responsibility. ``/lobby`` is cheap and chatty — anyone can connect, list rooms
and request a match. ``/game`` only accepts a connection that presents a valid one-shot
ticket issued by the lobby, so the gameplay namespace never parses an untrusted room id.

Handlers here do as little as possible: validate, translate to a queue push, return. All
state mutation happens inside the room's tick, which keeps the simulation single-threaded
and free of races despite arbitrary socket concurrency.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import socketio

from .config import PRIMARY_WEAPONS, Settings, get_settings
from .game.manager import RoomManager
from .protocol import (
    NS_GAME, NS_LOBBY, PROTOCOL_VERSION, parse_input, parse_input_batch,
    sanitize_chat, sanitize_name,
)

log = logging.getLogger("webstrike.sio")

CHAT_WINDOW = 5.0
CHAT_MAX_IN_WINDOW = 4


def create_sio(settings: Optional[Settings] = None) -> "GameServer":
    return GameServer(settings or get_settings())


class GameServer:
    """Owns the Socket.IO server, the room manager, and the wiring between them."""

    def __init__(self, settings: Settings):
        self.settings = settings

        client_manager = None
        if settings.redis_url:
            # Required as soon as there is more than one backend process: without it,
            # an emit in worker A never reaches a socket held by worker B.
            client_manager = socketio.AsyncRedisManager(settings.redis_url)
            log.info("socket.io using redis manager at %s", settings.redis_url)

        self.sio = socketio.AsyncServer(
            async_mode="asgi",
            cors_allowed_origins=settings.cors_list if settings.cors_list != ["*"] else "*",
            client_manager=client_manager,
            ping_interval=20,
            ping_timeout=25,
            max_http_buffer_size=64 * 1024,
            logger=False,
            engineio_logger=False,
        )
        self.manager = RoomManager(settings, emitter=self._emit)
        self._register()

    # ── emitter used by rooms ─────────────────────────────────────────────────

    async def _emit(self, event: str, data: Any, to: Optional[str] = None, room: Optional[str] = None) -> None:
        try:
            if to is not None:
                await self.sio.emit(event, data, to=to, namespace=NS_GAME)
            else:
                await self.sio.emit(event, data, room=room, namespace=NS_GAME)
        except Exception:  # pragma: no cover - a dead socket must not kill the tick
            log.debug("emit %s failed", event, exc_info=True)

    # ── handlers ──────────────────────────────────────────────────────────────

    def _register(self) -> None:
        sio = self.sio

        # ── lobby ─────────────────────────────────────────────────────────────
        @sio.event(namespace=NS_LOBBY)
        async def connect(sid: str, environ: dict, auth: Optional[dict] = None) -> None:
            await sio.emit(
                "hello",
                {"protocol": PROTOCOL_VERSION, "rooms": self.manager.list_rooms()},
                to=sid,
                namespace=NS_LOBBY,
            )

        @sio.on("rooms", namespace=NS_LOBBY)
        async def on_rooms(sid: str, _data: Any = None) -> None:
            await sio.emit("room_list", self.manager.list_rooms(), to=sid, namespace=NS_LOBBY)

        @sio.on("find_match", namespace=NS_LOBBY)
        async def on_find_match(sid: str, data: Any = None) -> dict:
            data = data if isinstance(data, dict) else {}
            if data.get("protocol") and data["protocol"] != PROTOCOL_VERSION:
                payload = {
                    "ok": False,
                    "error": f"Version mismatch: server {PROTOCOL_VERSION}, client "
                             f"{data['protocol']}. Reload the page.",
                }
                await sio.emit("match_found", payload, to=sid, namespace=NS_LOBBY)
                return payload

            name = sanitize_name(data.get("name"))
            team = data.get("team") if data.get("team") in ("A", "B") else None
            primary = data.get("primary") if data.get("primary") in PRIMARY_WEAPONS else None

            room = self.manager.find_room()
            if room is None:
                payload = {"ok": False, "error": "All servers are full. Try again shortly."}
                await sio.emit("match_found", payload, to=sid, namespace=NS_LOBBY)
                return payload

            ticket = self.manager.issue_ticket(room, name, team, primary)
            payload = {
                "ok": True,
                "ticket": ticket,
                "roomId": room.id,
                "players": room.human_count(),
                "protocol": PROTOCOL_VERSION,
            }
            await sio.emit("match_found", payload, to=sid, namespace=NS_LOBBY)
            return payload

        # ── game ──────────────────────────────────────────────────────────────
        @sio.event(namespace=NS_GAME)
        async def connect(sid: str, environ: dict, auth: Optional[dict] = None) -> bool:  # noqa: F811
            # The connection is accepted but idle until `join` presents a ticket.
            return True

        @sio.on("join", namespace=NS_GAME)
        async def on_join(sid: str, data: Any = None) -> None:
            data = data if isinstance(data, dict) else {}
            if data.get("protocol") != PROTOCOL_VERSION:
                await sio.emit(
                    "join_error",
                    {"error": f"Protocol mismatch (server {PROTOCOL_VERSION}). Reload."},
                    to=sid, namespace=NS_GAME,
                )
                return

            ticket = self.manager.redeem_ticket(str(data.get("ticket", "")))
            if ticket is None:
                await sio.emit(
                    "join_error", {"error": "Invalid or expired ticket. Reload."},
                    to=sid, namespace=NS_GAME,
                )
                return

            room = self.manager.get(ticket.room_id)
            if room is None:
                await sio.emit(
                    "join_error", {"error": "That match has ended. Reload."},
                    to=sid, namespace=NS_GAME,
                )
                return
            if room.is_full():
                await sio.emit(
                    "join_error", {"error": "That match filled up. Try again."},
                    to=sid, namespace=NS_GAME,
                )
                return

            ent = room.add_player(sid, ticket.name, ticket.team, ticket.primary)
            await sio.save_session(sid, {"room": room.id, "eid": ent.eid}, namespace=NS_GAME)
            await sio.enter_room(sid, room.id, namespace=NS_GAME)
            await sio.emit("welcome", room.welcome_payload(ent), to=sid, namespace=NS_GAME)
            room.push_scoreboard()

        @sio.on("input", namespace=NS_GAME)
        async def on_input(sid: str, data: Any = None) -> None:
            ent, room = await self._lookup(sid)
            if ent is None or room is None:
                return
            cmd = parse_input(data)
            if cmd is None:
                return
            if len(ent.input_queue) < 64:
                ent.input_queue.append(cmd)
            ent.last_seen = room.time

        @sio.on("input_batch", namespace=NS_GAME)
        async def on_input_batch(sid: str, data: Any = None) -> None:
            ent, room = await self._lookup(sid)
            if ent is None or room is None:
                return
            for cmd in parse_input_batch(data, limit=self.settings.max_inputs_per_tick * 2):
                if len(ent.input_queue) >= 64:
                    break
                ent.input_queue.append(cmd)
            ent.last_seen = room.time

        @sio.on("ping_ack", namespace=NS_GAME)
        async def on_ping_ack(sid: str, data: Any = None) -> None:
            ent, room = await self._lookup(sid)
            if ent is None or room is None:
                return
            try:
                seq = int(data.get("i")) if isinstance(data, dict) else int(data)
            except (TypeError, ValueError):
                return
            room.on_ping_ack(ent, seq)

        @sio.on("chat", namespace=NS_GAME)
        async def on_chat(sid: str, data: Any = None) -> None:
            ent, room = await self._lookup(sid)
            if ent is None or room is None:
                return
            msg = sanitize_chat(data.get("msg") if isinstance(data, dict) else data)
            if not msg:
                return
            now = time.monotonic()
            recent = [t for t in ent.chat_times if now - t < CHAT_WINDOW]
            if len(recent) >= CHAT_MAX_IN_WINDOW:
                return
            ent.chat_times.append(now)
            room.push_broadcast(
                {"e": "chat", "id": ent.eid, "name": ent.name, "team": ent.team, "msg": msg}
            )

        @sio.on("drop", namespace=NS_GAME)
        async def on_drop(sid: str, _data: Any = None) -> None:
            ent, room = await self._lookup(sid)
            if ent is None or room is None or not ent.alive:
                return
            dropped = ent.arsenal.drop_current(room.time)
            if dropped:
                room.push_broadcast({"e": "switch", "id": ent.eid, "w": ent.arsenal.current})

        @sio.event(namespace=NS_GAME)
        async def disconnect(sid: str, reason: Any = None) -> None:
            session = await self._session(sid)
            if not session:
                return
            room = self.manager.get(session.get("room", ""))
            if room is not None:
                room.remove_player(sid)

    # ── helpers ───────────────────────────────────────────────────────────────

    async def _session(self, sid: str) -> Dict[str, Any]:
        try:
            return await self.sio.get_session(sid, namespace=NS_GAME) or {}
        except KeyError:
            return {}

    async def _lookup(self, sid: str):
        session = await self._session(sid)
        room = self.manager.get(session.get("room", ""))
        if room is None:
            return None, None
        return room.by_sid.get(sid), room
