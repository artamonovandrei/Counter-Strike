# WebStrike

A browser-playable, server-authoritative tactical FPS. Team Deathmatch, hitscan weapons,
server-side bots, client-side prediction with reconciliation — Python backend, Three.js frontend.

Everything here is original. No trademarked names, no third-party game assets. The map is
generated from code, the audio is synthesized at runtime with the WebAudio API, and there is
not a single binary asset in the repo.

```
                       ┌──────────────────────────────────────────────┐
   Browser             │  EC2 / any Linux host                        │
 ┌──────────┐  HTTPS   │  ┌────────┐   :80/:443                       │
 │ Three.js │◄────────►│  │ Caddy  │  static /  + reverse proxy       │
 │  client  │   WSS    │  │        │  /api/*  /socket.io/*            │
 └──────────┘          │  └───┬────┘                                  │
      ▲                │      │ http://backend:8000                   │
      │ inputs 60 Hz   │  ┌───▼──────────────────────────────────┐    │
      │ snapshots 30Hz │  │ uvicorn + FastAPI + python-socketio  │    │
      └────────────────┼─►│  RoomManager                         │    │
                       │  │   └─ Room (60 Hz fixed-step sim)     │    │
                       │  │       ├─ movement / collision        │    │
                       │  │       ├─ hitscan + lag compensation  │    │
                       │  │       ├─ round controller (TDM)      │    │
                       │  │       └─ BotManager (A* waypoints)   │    │
                       │  └──────────────────────────────────────┘    │
                       └──────────────────────────────────────────────┘
```

## Features

- **Server-authoritative** simulation at a fixed 60 Hz internal tick, 30 Hz snapshots.
- **Client prediction + reconciliation**: the client runs the exact same movement integrator
  as the server, replays unacknowledged inputs on every snapshot, and smooths the residual error.
- **Entity interpolation** for remote players with a 100 ms render delay.
- **Lag compensation**: the server keeps a 1 s ring buffer of every entity's transform and
  rewinds the world by `rtt/2 + interp_delay` before resolving a hitscan ray.
- **Six weapons**: rifle, SMG, bolt-action sniper, shotgun, pistol, knife. Recoil patterns,
  movement-dependent spread, distance falloff, headshot multipliers, armour, and
  multi-pellet ballistics for the shotgun.
- **Aim down sights** on right mouse: per-weapon zoom, tighter spread, slower movement,
  and a full scope overlay for the bolt gun. The spread reduction scales with how far the
  sights have actually come up, so quick-scoping has to wait for the sight picture.
- **Procedurally generated graphics**: every texture is drawn into a canvas at load time,
  the map is merged into a handful of draw calls with world-scaled UVs, and lighting is
  shadow-mapped with ACES tone mapping. Still zero binary assets in the repository.
- **Animated player models**: hierarchical rig with a walk cycle, aim pose, sprint carry,
  and a death fall — plus the weapon they're actually holding, in their hands.
- **Bots**: per-team AI with a Patrol / Seek / Engage / Retreat / Regroup state machine,
  FoV + LoS perception, A* over a waypoint graph, burst fire, strafing, aim error that
  converges over time.
- **Team Deathmatch** round controller: warmup → live → intermission, score limit or time limit.
- **Anti-cheat basics**: the client never reports its own position. It reports intent
  (keys + view angles); everything else is derived server-side and rate-limited.

## Repository layout

```
.
├── assets/
│   └── maps/
│       ├── alley.json            # level geometry (AABB brushes), spawns, lights
│       └── alley.nav.json        # waypoint graph used by bot pathfinding
├── backend/
│   ├── app/
│   │   ├── main.py               # FastAPI app: /health /version /metrics /api/*
│   │   ├── sio_server.py         # Socket.IO namespaces: /lobby and /game
│   │   ├── config.py             # pydantic-settings + game tuning tables
│   │   ├── protocol.py           # wire schemas + key bitmask + version constant
│   │   ├── game/
│   │   │   ├── mathx.py          # tiny vector / ray helpers
│   │   │   ├── world.py          # map loading, AABB collision, raycasting
│   │   │   ├── nav.py            # waypoint graph + A*
│   │   │   ├── movement.py       # the shared movement integrator (mirrored in TS)
│   │   │   ├── weapons.py        # weapon definitions and per-shot state
│   │   │   ├── entities.py       # Player/Bot entity + transform history buffer
│   │   │   ├── combat.py         # hitscan, lag comp rewind, damage model
│   │   │   ├── bots.py           # bot AI state machine
│   │   │   ├── room.py           # the tick loop, round controller, snapshots
│   │   │   └── manager.py        # room registry / matchmaking
│   │   └── scripts/
│   │       ├── gen_map.py        # regenerates assets/maps/alley.json
│   │       ├── gen_nav.py        # regenerates assets/maps/alley.nav.json
│   │       ├── movement_trace.py # reference output for the parity check
│   │       └── run_headless_match.py
│   ├── tests/                    # movement, combat, bots, protocol, room
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.ts               # bootstrap + menu wiring
│   │   ├── game.ts               # render loop, orchestration
│   │   ├── net.ts                # Socket.IO client, snapshot buffer, RTT
│   │   ├── input.ts              # pointer lock, keyboard, input sampling at 60 Hz
│   │   ├── movement.ts           # 1:1 port of backend/app/game/movement.py
│   │   ├── predict.ts            # prediction + reconciliation
│   │   ├── world.ts              # builds the Three.js scene from the map JSON
│   │   ├── remote.ts             # remote player models + interpolation
│   │   ├── effects.ts            # view model + tracers, flashes, sparks, bullet holes
│   │   ├── weapons3d.ts          # weapon geometry, shared by view model and players
│   │   ├── textures.ts           # canvas-generated textures (no image files)
│   │   ├── audio.ts              # procedural WebAudio SFX (no sample files)
│   │   ├── hud.ts                # health/ammo/timer/score/crosshair/killfeed
│   │   └── menu.ts               # name entry, team select, settings, scoreboard
│   ├── tools/
│   │   ├── movement-trace.ts     # client half of the parity check
│   │   └── texture-luminance.ts  # guards against textures drifting dark
│   ├── index.html
│   ├── vite.config.ts
│   └── Dockerfile                # node build → caddy static + reverse proxy
├── shared/
│   └── protocol.ts               # TS mirror of backend/app/protocol.py
├── infra/
│   ├── Caddyfile                 # static + reverse proxy, auto-HTTPS
│   ├── Caddyfile.dev
│   └── systemd/                  # non-Docker deployment
├── scripts/
│   ├── check-parity.py           # client vs server: constants + movement
│   ├── deploy.sh                 # rebuild + health check + rollback
│   └── dev.sh
├── .github/workflows/ci.yml
├── docker-compose.yml
├── Makefile
└── .env.example
```

## Quick start (local, no Docker)

Two terminals.

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend**

```bash
cd frontend
pnpm install          # or: npm install / yarn
pnpm dev              # http://localhost:5173
```

Vite proxies `/api` and `/socket.io` to `http://localhost:8000`, so there are no CORS
problems in dev and the WebSocket upgrade works out of the box.

Or just `make dev` from the repo root, which runs both.

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
# http://localhost
```

With `DOMAIN` unset, Caddy serves plain HTTP on port 80. Set `DOMAIN` and `ACME_EMAIL`
in `.env` and Caddy will provision a real certificate automatically.

## Controls

| Key | Action |
| --- | --- |
| `W A S D` | Move |
| `Space` | Jump |
| `Shift` | Sprint |
| Mouse | Look (click the canvas to lock the pointer) |
| Left click | Fire |
| Right click | Aim down sights |
| `R` | Reload |
| `1` / `2` / `3` | Primary / Pistol / Knife |
| Mouse wheel | Cycle weapon |
| `G` | Drop current weapon |
| `Tab` (hold) | Scoreboard |
| `Esc` | Release pointer / menu |
| `Y` | Chat |

## Weapons

You pick a primary in the menu; the pistol and knife come as standard. The number keys
always mean the same thing — 1 primary, 2 pistol, 3 knife — regardless of what you chose.

| Weapon | Role | Damage | Rate | Mag | Notes |
| --- | --- | --- | --- | --- | --- |
| MR-9 Rifle | All-rounder | 33 | 600 rpm | 30 | Learnable recoil pattern, good to ~90 m |
| VP-7 SMG | Mobile | 24 | 850 rpm | 30 | Stays accurate while moving, falls off hard past 40 m |
| LR-40 Bolt | Long range | 118 | 41 rpm | 5 | One-shot body kill; unusable unless scoped and still |
| TS-12 Shotgun | Close quarters | 23 × 9 | 85 rpm | 8 | Lethal in a doorway, harmless across the map |
| SD-11 Pistol | Sidearm | 26 | 400 rpm | 12 | Semi-auto, always carried |
| Field Knife | Melee | 55 | — | — | Backstabs hit for 1.6× |

The balance is expressed as *relationships* rather than magic numbers, and the test suite
asserts those relationships (`backend/tests/test_weapons.py`): the SMG must be more
accurate than the rifle while moving, the shotgun must kill at point blank and not at
range, the sniper must be hopeless from the hip. Tune the numbers freely — the tests fail
only if a weapon stops doing its job.

Bots pick weapons by weighted roll (`BOT_PRIMARY_WEIGHTS`) and adjust their preferred
engagement distance to match (`BOT_RANGE_BY_WEAPON`), so a shotgun bot pushes and a sniper
bot holds. Snipers are deliberately rare: four bots holding the same angle with a one-shot
weapon is miserable to play against.

## How the netcode fits together

**Input path.** `input.ts` samples the keyboard into a bitmask at a fixed 60 Hz, stamps it
with a monotonically increasing `seq`, applies it immediately to the local player through
`movement.ts`, pushes it onto a pending queue, and sends it to the server.

**Snapshot path.** Every other tick the server serialises the world. The snapshot carries
`ack` — the last input sequence number it consumed for you. The client drops everything
`<= ack` from the pending queue, snaps the local player to the authoritative state, and
replays the remaining pending inputs. If the resulting position differs from what was
predicted by less than `RECONCILE_SNAP_DIST` the difference is smoothed out over a few
frames instead of teleporting.

**Because both sides must agree**, `frontend/src/movement.ts` is a line-for-line port of
`backend/app/game/movement.py`. If you change one, change the other.

`make check-parity` enforces this. It compares the 19 protocol constants in both
languages, then runs twelve movement scenarios — walking, sprinting, strafing, a jump arc,
running into a wall, climbing the platform stairs, falling, air control, and both
aim-down-sights cases — through *both* integrators against the real map, and diffs the
resulting transforms. Current state: all twelve agree to the last decimal place printed
(delta 0.000000). If that ever stops being true, players will rubber-band, and CI will say
so before they do.

This is not theoretical: adding ADS movement broke it immediately, because
`frontend/tools/` was outside the `tsconfig` include list and a missing config field
silently became `NaN`. The parity check caught it; nothing else would have.

**Remote entities** are rendered 100 ms in the past and interpolated between the two
snapshots that bracket the render time, which is why other players look smooth at 30 Hz.

**Lag compensation.** Each entity keeps 1 s of history. When you fire, the server rewinds
every other entity to `now - (rtt/2 + interp_delay)` — the world as it appeared on your
screen — resolves the ray, and then restores the present. This is capped at
`LAGCOMP_MAX_MS` (250 ms) so a high-ping player cannot shoot into the distant past.

## Lighting and visibility

Surface brightness is the product of two things: the colour in `assets/maps/alley.json`
and the generated detail texture that multiplies it. **The textures are near-white on
purpose** — they add grain, mortar lines and plank shadows, not colour.

Getting that wrong is not hypothetical. The first version painted the material colour into
the canvas *as well* as leaving it on `material.color`, so every surface was its own colour
squared: the floor's 0.24 albedo rendered at about 0.06 and the map was close to unplayable
in the shaded areas. There is a tool to keep that from coming back:

```bash
cd frontend
npm i -D @napi-rs/canvas
./node_modules/.bin/esbuild tools/texture-luminance.ts --bundle --platform=node \
    --format=cjs --external:@napi-rs/canvas --outfile=lum.cjs
node lum.cjs ../assets/maps/alley.json
```

```
material     texture   colour   surface
floor        0.876     0.577    0.506
wall         0.869     0.662    0.575
concrete     0.880     0.709    0.624
crate        0.853     0.572    0.488
metal        0.876     0.645    0.566
```

It exits non-zero if a texture mean drops below 0.8 (it has stopped being a detail map) or
a final albedo drops below 0.3 (players will not be readable against it).

Other levers, in the order worth reaching for them:

- **Brightness slider** in the menu — tone-mapping exposure, applied live, persisted.
  Different monitors need genuinely different values; there is no correct default.
- **`ambient`, `sky`, `lights`** in `gen_map.py`. There is no global illumination here, so
  the ambient and hemisphere terms are the *only* light reaching the inside of the building
  and the shadow side of every crate. Under-setting them makes parts of the map into places
  players simply cannot see into — a gameplay bug wearing an art-direction costume.
- **Tone mapping** is `NeutralToneMapping`, not ACES. ACES has a filmic toe that crushes
  the low end, which looks great in a render and hides enemies in a shooter.
- **Player materials** in `remote.ts` are deliberately lighter than the scenery. Realistic
  dark fatigues make players nearly invisible against a wall in shadow, and "I never saw
  him" is a worse experience than "that uniform is a bit bright".

## Tuning bots

All bot behaviour lives in `BOT_TUNING` in `backend/app/config.py` and can be overridden
per-difficulty via env:

```bash
BOT_DIFFICULTY=easy|normal|hard|expert
BOTS_PER_TEAM=5
BOT_LOGIC_HZ=10          # AI think rate; physics always runs at the room tick
```

| Field | Meaning |
| --- | --- |
| `aim_error_deg` | Cone the bot's aim starts in when it acquires a target |
| `aim_converge` | How fast (per second) that cone shrinks while tracking |
| `reaction_time` | Delay between seeing an enemy and being allowed to shoot |
| `fov_deg` | Perception cone half-angle × 2 |
| `sight_range` | Metres |
| `burst_min/max` | Rounds per burst before a pause |
| `retreat_health` | Below this, the bot backs off toward friendly territory |
| `strafe_period` | Seconds between strafe direction flips while engaging |

The AI think rate is decoupled from physics: bots move every tick using the same
integrator as humans, but only re-evaluate targets/paths at `BOT_LOGIC_HZ`, staggered
across bots so the cost is spread over the frame budget.

## Map and navmesh workflow

The level is a list of axis-aligned boxes. That is deliberate: the same JSON drives the
server's collision/raycast world and the client's Three.js scene, so what you see is
exactly what the server shoots at.

```bash
cd backend
python -m app.scripts.gen_map            # writes assets/maps/alley.json
python -m app.scripts.gen_nav alley      # writes assets/maps/alley.nav.json
```

`gen_map.py` is plain Python — edit the `build()` function to change the layout. `gen_nav.py`
samples a grid over the walkable area, drops points that are inside geometry or unsupported,
then links nodes that have clearance and line of sight. Both scripts print a summary and are
safe to re-run.

To use a different map, set `MAP_NAME=yourmap` and drop `yourmap.json` + `yourmap.nav.json`
into `assets/maps/`.

## Headless match (smoke test)

Runs a full match with no clients attached — useful for profiling the sim and for CI.

```bash
cd backend
python -m app.scripts.run_headless_match --bots 8 --ticks 5000
```

It prints tick timing percentiles, the final scoreboard, kill counts per bot, and exits
non-zero if the sim produced no kills (which almost always means the nav graph or the
raycaster is broken) or if any bot never left the `IDLE` state (unreachable nav graph).

Typical output on a modest machine — 10 bots, 100 s of simulated play:

```
tick mean        0.311 ms
tick p50/p95/p99 0.266 / 0.598 / 0.914 ms
tick max         4.141 ms  (budget 16.7 ms)
OK — 66 kills over 100s of play
```

That's ~2% of the 60 Hz frame budget for a full room, which is the headroom the scaling
notes below assume.

## Tests

```bash
make check          # everything below, in the order CI runs it
```

or individually:

```bash
cd backend && pytest -q          # 165 tests
make smoke                       # headless bot match
make check-parity                # client/server agreement
cd frontend && npm run typecheck && npm run lint && npm run build
```

The Python suite covers the movement integrator (gravity, jump apex measured against the
analytic `v²/2g`, wall collision, step-up, tunnelling at 3× sprint speed, friction,
diagonal normalisation), raycasting and hit classification (headshot vs body, damage
falloff, wall occlusion, teammates never blocking a bullet), lag-compensated hit
resolution, the weapon state machine (fire rate, semi-auto trigger discipline, reload,
spread growth, recoil pattern), the arsenal (loadouts, slot stability across loadouts,
shotgun pellet spread, ADS raise/lower timing and its effect on spread and recoil,
per-weapon balance relationships), bot pathfinding (A* optimality on a lattice, unreachable
goals, path smoothing, cover scoring), the room (joining, team balance, snapshot shape and
cadence, scoring, respawn, round transitions, input-flood capping) and protocol parsing
against hostile input.

## Deploying to AWS EC2

1. **Launch** a `t3.small` (or larger) with Ubuntu 22.04 LTS. A single room of 10 players
   + 10 bots fits comfortably in `t3.small`; scale up before you scale out.

2. **Security group**: inbound `22` (your IP only), `80`, `443`. WebSockets ride on 443, so
   no extra ports are needed.

3. **DNS**: create an `A` record for `webstrike.example.com` pointing at the instance's
   Elastic IP. Do this before starting Caddy, otherwise the ACME challenge fails.

4. **Install Docker**:

   ```bash
   sudo apt-get update
   sudo apt-get install -y docker.io docker-compose-plugin git
   sudo usermod -aG docker $USER && newgrp docker
   ```

5. **Deploy**:

   ```bash
   git clone <your-repo-url> webstrike && cd webstrike
   cp .env.example .env
   # edit .env: DOMAIN=webstrike.example.com, ACME_EMAIL=you@example.com,
   #            CORS_ORIGINS=https://webstrike.example.com
   docker compose up -d --build
   docker compose logs -f caddy   # watch the certificate get issued
   ```

6. **Verify**:

   ```bash
   curl -s https://webstrike.example.com/api/health   # {"status":"ok",...}
   curl -s https://webstrike.example.com/api/version
   ```

   Then open the site, join, and check the netgraph in the top-right (toggle with `F3`).
   Ping should be stable and `loss` should be 0.

`scripts/deploy.sh` wraps steps 5–6 for redeploys (`git pull`, rebuild, health check,
rollback if the health check fails).

### Without Docker

`infra/systemd/` contains `webstrike-backend.service` plus install notes. Build the
frontend with `pnpm build`, copy `frontend/dist` to `/srv/webstrike`, and point the system
Caddy at `infra/Caddyfile`.

### Scaling

- One uvicorn worker owns its rooms in-process. **Do not** run multiple workers without
  Redis — a room lives in one process's memory.
- For multiple processes, set `REDIS_URL`; the Socket.IO manager switches to
  `AsyncRedisManager` so emits fan out correctly, and the room registry becomes shared.
  Route by room with sticky sessions (`ip_hash`) if you put a load balancer in front.
- Rough budget: the sim costs ~0.35 ms/tick per 20 entities on a `t3.small` vCPU, so a
  single core saturates around 8–10 concurrent full rooms. Raise `ROOM_MAX_PLAYERS` before
  adding rooms; the per-room overhead dominates.
- Drop `SNAPSHOT_HZ` from 30 to 20 to cut bandwidth ~33% at a modest smoothness cost.

## Security notes

- The client sends **intent only**: a key bitmask and view angles. Position, velocity,
  health, ammo and hits are all derived server-side.
- Fire rate, reload state, magazine contents and weapon switch timing are validated against
  the server clock; a client that spams `FIRE` gets its extra requests dropped silently.
- Inputs are clamped: `dt` per input is bounded, and a client that floods more than
  `MAX_INPUTS_PER_TICK` has the surplus discarded rather than buffered.
- Names are sanitised to a printable subset and truncated; chat is rate-limited.
- **Known MVP gap**: recoil is applied as a view kick that the client is trusted to
  incorporate. A modified client could ignore it. Mitigating this properly means
  server-owned view angles, which is out of scope for the MVP — spread growth during a
  spray limits the payoff in the meantime.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Blank page, console shows `WebSocket closed before connection established` | Reverse proxy isn't upgrading. Check the `/socket.io/*` handler in `infra/Caddyfile`. |
| `400 Bad Request` from Socket.IO immediately on connect | Client and server `PROTOCOL_VERSION` differ. Rebuild the frontend. |
| You rubber-band constantly | Prediction desync — the movement constants on both sides drifted. Run `make check-parity`. |
| Players teleport/stutter but you feel fine | Snapshot rate too low or `INTERP_DELAY_MS` shorter than the snapshot interval. It must be ≥ `1000/SNAPSHOT_HZ`. |
| Bots stand still at spawn | Nav graph didn't load. `python -m app.scripts.gen_nav alley` and check the node/link counts in the log. |
| Bots walk into walls | Nav links were generated with too little clearance; raise `CLEARANCE` in `gen_nav.py`. |
| Shots visibly hit but no damage | Lag comp rewind is exceeding `LAGCOMP_MAX_MS`, or the client's clock offset is wrong. Check `F3` netgraph RTT. |
| Caddy loops on `ACME challenge failed` | DNS `A` record isn't pointing at the box yet, or port 80 is blocked in the security group. |
| High CPU with many bots | Lower `BOT_LOGIC_HZ` to 8, or `BOTS_PER_TEAM`. Physics cost is linear in entities, AI cost is linear in entities². |
| Low frame rate on a laptop | Turn off "High quality shadows" in the menu — it drops the shadow map to 1024 and switches to cheaper filtering. Lowering FOV also helps, since less of the map is drawn. |
| Too dark / too bright | Use the **Brightness** slider in the menu (tone-mapping exposure, 0.7–2.2, applies live). If *everything* is dark rather than just your monitor, run the texture luminance check below — a detail texture that has drifted dark dims the whole level. |
| Scoping feels sluggish | That's `adsTime` doing its job; the sniper deliberately takes 0.35 s. Quick-scoping is meant to be a trade, not free. |
| Shotgun feels like it does nothing | Check the range. Damage is floored at 15% past 22 m by design — it is a doorway weapon. |
| Pointer lock won't engage | Browsers require a user gesture and a secure context. Use `https://` or `localhost`. |

## Post-deploy checklist

- [ ] `GET /api/health` returns `200` over HTTPS
- [ ] `GET /api/version` matches the commit you deployed
- [ ] The WebSocket connects (Network tab: `101 Switching Protocols`, not polling fallback)
- [ ] Two browsers in different networks see each other move smoothly
- [ ] `docker compose logs backend | grep -i error` is empty after 5 minutes of play
- [ ] `docker stats` shows the backend under ~60% of one core with a full room
- [ ] Certificate expiry is > 60 days and Caddy's renewal timer is active
- [ ] `.env` is not committed and is `chmod 600`

## Licence and asset provenance

MIT — see [LICENSE](LICENSE).

Everything in this repository is original work created for this project:

- **Map geometry** — generated procedurally by `backend/app/scripts/gen_map.py`. No imported
  meshes, no third-party level data.
- **Audio** — synthesised at runtime in `frontend/src/audio.ts` using WebAudio oscillators
  and noise buffers. There are no sample files, CC0 or otherwise, so there is nothing to
  attribute.
- **Textures** — generated at runtime in `frontend/src/textures.ts` by drawing into a
  canvas: concrete grain, breeze-block mortar, plank grain, brushed metal, bullet holes,
  muzzle flashes. Seeded, so a surface looks identical on every machine and every reload.
- **Models** — weapons and players are assembled from primitives in
  `frontend/src/weapons3d.ts` and `frontend/src/remote.ts`. No imported meshes.
- **Names** — "WebStrike" and all weapon/map names are original. No trademarked terms from
  any existing game appear in the code, assets or documentation.

Third-party *software* dependencies (Three.js, FastAPI, python-socketio, Vite, Caddy) are
used under their own permissive licences and are pinned in `requirements.txt` /
`package.json`.
