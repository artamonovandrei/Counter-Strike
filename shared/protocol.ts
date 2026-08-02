// path: shared/protocol.ts
//
// Wire protocol shared by the browser client and the Python server.
// This file is the TypeScript mirror of backend/app/protocol.py.
// If you change a field name here, change it there too — `make check-parity`
// diffs the two and fails the build when they drift.

export const PROTOCOL_VERSION = '1.1.0';

/** Socket.IO namespaces. */
export const NS_LOBBY = '/lobby';
export const NS_GAME = '/game';

// ─── Key bitmask (client → server) ────────────────────────────────────────────
export const K_FORWARD = 1 << 0;
export const K_BACK = 1 << 1;
export const K_LEFT = 1 << 2;
export const K_RIGHT = 1 << 3;
export const K_JUMP = 1 << 4;
export const K_SPRINT = 1 << 5;
export const K_FIRE = 1 << 6;
export const K_RELOAD = 1 << 7;
export const K_CROUCH = 1 << 8;
export const K_ADS = 1 << 9;

// ─── Entity state flags (server → client) ─────────────────────────────────────
export const F_DEAD = 1 << 0;
export const F_GROUNDED = 1 << 1;
export const F_RELOADING = 1 << 2;
export const F_SPRINTING = 1 << 3;
export const F_MOVING = 1 << 4;
export const F_BOT = 1 << 5;
export const F_ADS = 1 << 6;
export const F_AIRBORNE = 1 << 7;

export type Team = 'A' | 'B';
export type Phase = 'warmup' | 'live' | 'intermission';

export type PrimaryWeaponId = 'rifle' | 'smg' | 'sniper' | 'shotgun';
export type WeaponId = PrimaryWeaponId | 'pistol' | 'knife';
export type WeaponCategory = 'primary' | 'secondary' | 'melee';

export const PRIMARY_WEAPONS: PrimaryWeaponId[] = ['rifle', 'smg', 'sniper', 'shotgun'];

export type Vec3 = [number, number, number];

// ─── Lobby namespace ──────────────────────────────────────────────────────────

/** client → server, event `find_match` */
export interface FindMatchRequest {
  protocol: string;
  name: string;
  /** Preferred team, or null to be auto-balanced. */
  team: Team | null;
  /** Chosen primary weapon; the server falls back to the rifle if it doesn't like it. */
  primary: PrimaryWeaponId | null;
}

/** server → client, event `match_found` */
export interface MatchFoundResponse {
  ok: boolean;
  error?: string;
  ticket?: string;
  roomId?: string;
  players?: number;
  protocol?: string;
}

/** server → client, event `room_list` */
export interface RoomListEntry {
  roomId: string;
  players: number;
  bots: number;
  capacity: number;
  map: string;
  phase: Phase;
  scoreA: number;
  scoreB: number;
}

// ─── Game namespace: handshake ────────────────────────────────────────────────

/** client → server, event `join` */
export interface JoinRequest {
  protocol: string;
  ticket: string;
}

export interface WeaponConfig {
  id: WeaponId;
  /** 1 = primary, 2 = secondary, 3 = melee. Stable across loadouts. */
  slot: number;
  category: WeaponCategory;
  name: string;
  magSize: number;
  reserveMax: number;
  rpm: number;
  auto: boolean;
  /** >1 for shotguns. */
  pellets: number;
  damage: number;
  headshotMult: number;
  range: number;
  reloadTime: number;
  spreadBase: number;
  spreadMove: number;
  spreadAir: number;
  spreadPerShot: number;
  spreadMax: number;
  spreadDecay: number;
  recoilPitch: number;
  recoilYaw: number;
  switchTime: number;
  /** Vertical FOV while sighted. 0 means the weapon cannot be aimed down sights. */
  adsFov: number;
  adsSpreadMult: number;
  adsTime: number;
  /** true = full scope overlay (sniper), false = simple zoom. */
  scope: boolean;
  melee: boolean;
}

/** Movement/tuning constants. Sent by the server so both sides agree at runtime. */
export interface GameConfig {
  tickHz: number;
  snapshotHz: number;
  interpDelayMs: number;
  playerRadius: number;
  playerHeight: number;
  eyeHeight: number;
  gravity: number;
  jumpSpeed: number;
  walkSpeed: number;
  sprintSpeed: number;
  crouchSpeed: number;
  adsSpeed: number;
  groundAccel: number;
  airAccel: number;
  airCap: number;
  friction: number;
  stopSpeed: number;
  stepHeight: number;
  maxFallSpeed: number;
  maxHealth: number;
  respawnSeconds: number;
  scoreLimit: number;
  roundSeconds: number;
}

/** server → client, event `welcome` */
export interface Welcome {
  protocol: string;
  playerId: number;
  roomId: string;
  team: Team;
  name: string;
  /** The primary the server actually gave you — not necessarily the one you asked for. */
  primary: PrimaryWeaponId;
  config: GameConfig;
  /** Every weapon in the game, so any player's model can be rendered. */
  weapons: WeaponConfig[];
  map: MapData;
  serverTime: number;
}

// ─── Map data ─────────────────────────────────────────────────────────────────

export interface MapBox {
  /** centre */
  p: Vec3;
  /** full extents */
  s: Vec3;
  /** material key, used for colour + impact sound */
  m: string;
}

export interface MapSpawn {
  p: Vec3;
  yaw: number;
}

export interface MapData {
  name: string;
  version: number;
  /** [minX, minY, minZ, maxX, maxY, maxZ] */
  bounds: [number, number, number, number, number, number];
  ambient: string;
  sky: [string, string];
  fog: { color: string; near: number; far: number };
  materials: Record<string, { color: string; roughness: number; metalness: number }>;
  boxes: MapBox[];
  spawns: { A: MapSpawn[]; B: MapSpawn[] };
  lights: { p: Vec3; color: string; intensity: number; distance: number }[];
}

export interface NavNode {
  id: number;
  p: Vec3;
  /** cover score 0..1, used by bots when retreating */
  cover: number;
}

export interface NavData {
  map: string;
  nodes: NavNode[];
  /** adjacency list, links[i] = neighbour ids of node i */
  links: number[][];
}

// ─── Gameplay: client → server ────────────────────────────────────────────────

/** event `input` — sent at TICK_HZ. Field names are terse on purpose. */
export interface InputCmd {
  /** sequence number, strictly increasing */
  s: number;
  /** delta time in milliseconds for this command (clamped server-side) */
  dt: number;
  /** key bitmask */
  k: number;
  /** yaw in radians */
  y: number;
  /** pitch in radians, clamped to ±PI/2 */
  p: number;
  /** requested weapon slot (1..3), 0 = no change */
  w: number;
}

/** event `input_batch` — several InputCmd in one frame to survive packet loss. */
export interface InputBatch {
  c: InputCmd[];
}

/** event `chat` */
export interface ChatRequest {
  msg: string;
}

// ─── Gameplay: server → client ────────────────────────────────────────────────

/** Your own authoritative state. */
export interface SelfState {
  x: number;
  y: number;
  z: number;
  vx: number;
  vy: number;
  vz: number;
  hp: number;
  ar: number;
  /** current weapon id */
  w: WeaponId;
  /** rounds in magazine */
  am: number;
  /** reserve rounds */
  rs: number;
  /** state flags */
  f: number;
  /** seconds until respawn, 0 when alive */
  rt: number;
  /** round-trip time in ms, measured by the server */
  pg: number;
  /** how far the sights are raised, 0..1 */
  ap: number;
  /** ammo in every carried weapon, for the loadout strip in the HUD */
  sl: { id: WeaponId; ammo: number; reserve: number }[];
}

/** A remote entity as seen in a snapshot. */
export interface EntState {
  id: number;
  /** team */
  t: Team;
  x: number;
  y: number;
  z: number;
  /** yaw */
  a: number;
  /** pitch */
  p: number;
  hp: number;
  /** state flags */
  f: number;
  /** weapon id */
  w: WeaponId;
}

/** event `snapshot` */
export interface Snapshot {
  /** server tick number */
  t: number;
  /** server time, milliseconds since server start */
  st: number;
  /** last input sequence consumed for you */
  ack: number;
  self: SelfState;
  ents: EntState[];
  /** scores */
  sc: { A: number; B: number };
  ph: Phase;
  /** seconds left in the current phase */
  pt: number;
}

/** event `pong` */
export interface PongMsg {
  /** echo of the client timestamp */
  c: number;
  /** server time */
  st: number;
}

// ─── Game events (server → client, event `ev`) ────────────────────────────────

export type GameEvent =
  /** One per trigger pull: drives the muzzle flash and the report. */
  | { e: 'shot'; id: number; w: WeaponId; o: Vec3; d: Vec3 }
  /** One per projectile — nine of these for a shotgun blast, one for a rifle round. */
  | { e: 'tracer'; id: number; o: Vec3; p: Vec3 }
  | { e: 'impact'; p: Vec3; n: Vec3; m: string }
  | { e: 'hit'; dmg: number; hs: boolean; kill: boolean; vid: number }
  | { e: 'hurt'; amt: number; hp: number; from: Vec3 }
  | { e: 'kick'; y: number; p: number }
  | { e: 'kill'; kid: number; vid: number; k: string; v: string; w: WeaponId; hs: boolean; team: Team }
  | { e: 'spawn'; id: number; p: Vec3 }
  | { e: 'round'; ph: Phase; pt: number; sc: { A: number; B: number }; winner?: Team | 'draw' }
  | { e: 'join'; id: number; name: string; team: Team; bot: boolean }
  | { e: 'leave'; id: number; name: string }
  | { e: 'chat'; id: number; name: string; team: Team; msg: string }
  | { e: 'scoreboard'; rows: ScoreRow[] }
  | { e: 'reload'; id: number; w: WeaponId }
  | { e: 'switch'; id: number; w: WeaponId }
  | { e: 'ping_req'; i: number };

export interface ScoreRow {
  id: number;
  name: string;
  team: Team;
  kills: number;
  deaths: number;
  bot: boolean;
  ping: number;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

export function hasFlag(flags: number, flag: number): boolean {
  return (flags & flag) !== 0;
}

export function otherTeam(t: Team): Team {
  return t === 'A' ? 'B' : 'A';
}

/** Display metadata for the loadout picker. Purely cosmetic; the server owns the stats. */
export const WEAPON_BLURBS: Record<PrimaryWeaponId, string> = {
  rifle: 'All-rounder. Controllable spray, hits hard at any sane range.',
  smg: 'Fast and mobile. Stays accurate while moving, useless past mid.',
  sniper: 'One shot, one kill. Hopeless unless you are scoped and still.',
  shotgun: 'Nine pellets. Devastating in a doorway, a joke across the map.',
};

/** Team colours, used consistently by HUD, killfeed and player models. */
export const TEAM_COLORS: Record<Team, string> = {
  A: '#4ea8ff',
  B: '#ff9d4e',
};

export const TEAM_NAMES: Record<Team, string> = {
  A: 'Vanguard',
  B: 'Insurgents',
};
