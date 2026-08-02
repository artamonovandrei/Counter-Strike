# path: backend/app/main.py
"""ASGI entrypoint.

``app`` is a Socket.IO ASGI wrapper around the FastAPI application, so one uvicorn process
serves both the REST endpoints and the WebSocket transport on the same port — which is
what lets the reverse proxy config stay a single upstream.

Run: ``uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload``
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import VERSION, get_settings
from .protocol import PROTOCOL_VERSION
from .sio_server import GameServer, create_sio

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("webstrike")

game: GameServer = create_sio(settings)
START_TIME = time.time()

REAP_INTERVAL = 60.0


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Warm one room at boot so the first player doesn't pay for map + nav load.
    game.manager.create_room()
    reaper = asyncio.create_task(_reap_loop(), name="room-reaper")
    log.info(
        "WebStrike %s ready — protocol %s, map '%s', %d Hz tick / %d Hz snapshots",
        VERSION, PROTOCOL_VERSION, settings.map_name, settings.tick_hz, settings.snapshot_hz,
    )
    try:
        yield
    finally:
        reaper.cancel()
        try:
            await reaper
        except asyncio.CancelledError:
            pass
        await game.manager.shutdown()
        log.info("WebStrike shut down cleanly")


async def _reap_loop() -> None:
    while True:
        try:
            await asyncio.sleep(REAP_INTERVAL)
            await game.manager.reap_idle()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive
            log.exception("room reaper failed")


api = FastAPI(
    title="WebStrike",
    version=VERSION,
    description="Server-authoritative browser FPS backend.",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

api.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@api.get("/api/health")
async def health() -> dict:
    """Liveness + a little readiness. Used by the deploy script and by compose."""
    rooms = game.manager.rooms
    stalled = [r.id for r in rooms.values() if r.running is False]
    return {
        "status": "degraded" if stalled else "ok",
        "uptime": round(time.time() - START_TIME, 1),
        "rooms": len(rooms),
        "players": sum(r.human_count() for r in rooms.values()),
        "stalledRooms": stalled,
    }


@api.get("/api/version")
async def version() -> dict:
    return {
        "version": VERSION,
        "protocol": PROTOCOL_VERSION,
        "map": settings.map_name,
        "tickHz": settings.tick_hz,
        "snapshotHz": settings.snapshot_hz,
    }


@api.get("/api/metrics")
async def metrics() -> dict:
    """Plain JSON rather than Prometheus text — small enough to read by eye, and easy to
    scrape with a one-line exporter if you later want it in Prometheus."""
    m = game.manager.metrics()
    m["uptime"] = round(time.time() - START_TIME, 1)
    m["version"] = VERSION
    return m


@api.get("/api/rooms")
async def rooms() -> list:
    return game.manager.list_rooms()


@api.get("/api/config")
async def config() -> dict:
    """Public tuning values. Handy for debugging a client that won't connect."""
    from .config import client_config, client_weapons

    return {
        "protocol": PROTOCOL_VERSION,
        "config": client_config(settings),
        "weapons": client_weapons(),
    }


@api.exception_handler(404)
async def not_found(_request, _exc) -> JSONResponse:
    return JSONResponse({"error": "not found"}, status_code=404)


# Socket.IO owns /socket.io/*; everything else falls through to FastAPI.
app = socketio.ASGIApp(game.sio, other_asgi_app=api, socketio_path="socket.io")
