// path: frontend/tools/movement-trace.ts
//
// TypeScript half of the movement parity check. Runs the same scenarios as
// backend/app/scripts/movement_trace.py and prints the same JSON shape;
// scripts/check-parity.py diffs them.
//
// Bundled and executed with esbuild + node (see the `parity` target in the Makefile),
// so it needs no test runner and no browser.

import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  K_ADS,
  K_BACK,
  K_FORWARD,
  K_JUMP,
  K_LEFT,
  K_RIGHT,
  K_SPRINT,
  type GameConfig,
  type MapData,
} from '@shared/protocol';
import { stepMovement } from '../src/movement';
import { CollisionWorld } from '../src/world';

const DT = 1 / 60;

// Must match backend/app/config.py: MoveConfig. The running client gets these from the
// server's `welcome`; here they are spelled out so a divergence in the *values* is caught
// too, not just a divergence in the algorithm.
const CONFIG: GameConfig = {
  tickHz: 60,
  snapshotHz: 30,
  interpDelayMs: 100,
  playerRadius: 0.4,
  playerHeight: 1.8,
  eyeHeight: 1.62,
  gravity: 22.0,
  jumpSpeed: 7.0,
  walkSpeed: 5.2,
  sprintSpeed: 7.2,
  crouchSpeed: 2.6,
  adsSpeed: 2.9,
  groundAccel: 70.0,
  airAccel: 14.0,
  airCap: 1.2,
  friction: 9.0,
  stopSpeed: 1.5,
  stepHeight: 0.35,
  maxFallSpeed: 60.0,
  maxHealth: 100,
  respawnSeconds: 3,
  scoreLimit: 50,
  roundSeconds: 360,
};

interface Scenario {
  name: string;
  start: [number, number, number];
  yaw: number;
  keys: number;
  ticks: number;
}

const SCENARIOS: Scenario[] = [
  { name: 'walk_forward', start: [0, 0.5, -20], yaw: 0, keys: K_FORWARD, ticks: 120 },
  {
    name: 'sprint_diagonal',
    start: [0, 0.5, -20],
    yaw: 0.7,
    keys: K_FORWARD | K_RIGHT | K_SPRINT,
    ticks: 150,
  },
  { name: 'strafe_left', start: [-12, 0.5, 0], yaw: 1.9, keys: K_LEFT, ticks: 90 },
  { name: 'backpedal', start: [6, 0.5, 14], yaw: 2.4, keys: K_BACK, ticks: 90 },
  { name: 'bunny_hop', start: [0, 0.5, -18], yaw: 0, keys: K_FORWARD | K_JUMP, ticks: 180 },
  {
    name: 'into_building_wall',
    start: [4, 0.5, 12],
    yaw: 0,
    keys: K_FORWARD | K_SPRINT,
    ticks: 200,
  },
  { name: 'climb_platform_stairs', start: [19, 0.5, 9], yaw: 0, keys: K_FORWARD, ticks: 90 },
  { name: 'fall_from_height', start: [0, 8, 0], yaw: 0, keys: 0, ticks: 120 },
  { name: 'air_control', start: [-19, 6, 0], yaw: 1.2, keys: K_FORWARD | K_RIGHT, ticks: 120 },
  { name: 'corner_slide', start: [8.6, 0.5, -8], yaw: 0.6, keys: K_FORWARD, ticks: 160 },
  // ADS changes the movement speed, so it has to be covered here or a divergence in the
  // slow-walk would only show up as rubber-banding while scoped.
  { name: 'ads_walk', start: [0, 0.5, -20], yaw: 0, keys: K_FORWARD | K_ADS, ticks: 120 },
  {
    name: 'ads_beats_sprint',
    start: [0, 0.5, -20],
    yaw: 0.3,
    keys: K_FORWARD | K_SPRINT | K_ADS,
    ticks: 120,
  },
];

function round5(v: number): number {
  return Math.round(v * 1e5) / 1e5;
}

function main(): void {
  // The map path may be passed explicitly, because this file gets bundled to a temporary
  // location where import.meta.url no longer points anywhere near the repo.
  const explicit = process.argv[2];
  const here = dirname(fileURLToPath(import.meta.url));
  const mapPath = explicit ?? resolve(here, '../../assets/maps/alley.json');
  const mapName = mapPath.replace(/^.*[\\/]/, '').replace(/\.json$/, '');
  const map = JSON.parse(readFileSync(mapPath, 'utf-8')) as MapData;
  const world = new CollisionWorld(map);

  const runs = SCENARIOS.map((sc) => {
    const pos = { x: sc.start[0], y: sc.start[1], z: sc.start[2] };
    const vel = { x: 0, y: 0, z: 0 };
    let grounded = false;
    for (let i = 0; i < sc.ticks; i++) {
      const result = stepMovement(world, pos, vel, sc.yaw, sc.keys, DT, grounded, CONFIG);
      grounded = result.grounded;
    }
    return {
      name: sc.name,
      pos: [round5(pos.x), round5(pos.y), round5(pos.z)],
      vel: [round5(vel.x), round5(vel.y), round5(vel.z)],
      grounded,
    };
  });

  process.stdout.write(JSON.stringify({ map: mapName, dt: DT, runs }, null, 1) + '\n');
}

main();
