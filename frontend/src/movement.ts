// path: frontend/src/movement.ts
//
// ⚠ This is a line-for-line port of backend/app/game/movement.py. It exists so the client
// can predict its own motion and arrive at the same answer the server will. Any
// divergence — a different constant, a different order of operations, a different
// epsilon — shows up as rubber-banding, because the server's correction will disagree
// with what the client already drew.
//
// If you change one file, change the other in the same commit.

import type { GameConfig } from '@shared/protocol';
import {
  K_ADS,
  K_BACK,
  K_CROUCH,
  K_FORWARD,
  K_JUMP,
  K_LEFT,
  K_RIGHT,
  K_SPRINT,
} from '@shared/protocol';
import type { AABB, CollisionWorld } from './world';

const EPS = 1e-6;

// Overlaps shallower than this are treated as contact rather than penetration. 0.1 mm is
// far below anything a player can perceive and far above floating-point noise.
const PENETRATION_EPS = 1e-4;

export interface Vec3Like {
  x: number;
  y: number;
  z: number;
}

export interface MoveResult {
  grounded: boolean;
  landed: boolean;
  landSpeed: number;
  blocked: boolean;
}

function playerBox(pos: Vec3Like, radius: number, height: number): AABB {
  return {
    minX: pos.x - radius,
    minY: pos.y,
    minZ: pos.z - radius,
    maxX: pos.x + radius,
    maxY: pos.y + height,
    maxZ: pos.z + radius,
  };
}

/**
 * Push `pos` out of any overlapping brush, moving only along `axis` (0=x, 1=y, 2=z).
 *
 * Three rules here exist because of a bug that dropped players through the world:
 *
 * - **A graze is not a collision.** Brushes overlapping by less than `PENETRATION_EPS` are
 *   ignored. This is the one that actually mattered: a player standing flush against a
 *   container overlapped it by 1e-10 on Z purely from floating point, which was enough for
 *   the Y resolver to fire and eject them 1.8 m downwards.
 * - **Vertical pushes prefer "up".** A player is only pushed *down* out of a brush if their
 *   feet started below it (they jumped into a ceiling). Otherwise they are standing on or
 *   in something and belong on top of it.
 * - **The second pass may not reverse the first**, or the two passes can shove a player
 *   back and forth between adjacent brushes.
 */
function resolveAxis(
  world: CollisionWorld,
  pos: Vec3Like,
  radius: number,
  height: number,
  axis: 0 | 1 | 2,
): boolean {
  let corrected = false;
  let pushSign = 0;
  for (let pass = 0; pass < 2; pass++) {
    const box = playerBox(pos, radius, height);
    const boxLo = axis === 0 ? box.minX : axis === 1 ? box.minY : box.minZ;
    const boxHi = axis === 0 ? box.maxX : axis === 1 ? box.maxY : box.maxZ;

    let bestPush = 0;
    for (const i of world.overlapping(box)) {
      const b = world.boxes[i];

      // Penetration depth on every axis. If the shallowest is negligible the boxes are
      // touching, not intersecting, and resolving would be a violent no-op.
      const penX = Math.min(b.maxX - box.minX, box.maxX - b.minX);
      const penY = Math.min(b.maxY - box.minY, box.maxY - b.minY);
      const penZ = Math.min(b.maxZ - box.minZ, box.maxZ - b.minZ);
      if (penX <= PENETRATION_EPS || penY <= PENETRATION_EPS || penZ <= PENETRATION_EPS) {
        continue;
      }

      const bLo = axis === 0 ? b.minX : axis === 1 ? b.minY : b.minZ;
      const bHi = axis === 0 ? b.maxX : axis === 1 ? b.maxY : b.maxZ;
      const exitPos = bHi - boxLo;
      const exitNeg = boxHi - bLo;
      let push: number;
      if (axis === 1) {
        const cameFromBelow = box.minY < b.minY - PENETRATION_EPS;
        push = cameFromBelow ? -exitNeg : exitPos;
      } else {
        push = exitPos < exitNeg ? exitPos : -exitNeg;
      }
      if (pushSign !== 0 && push * pushSign < 0) continue;
      if (Math.abs(push) > Math.abs(bestPush)) bestPush = push;
    }
    if (Math.abs(bestPush) < EPS) break;
    if (axis === 0) pos.x += bestPush;
    else if (axis === 1) pos.y += bestPush;
    else pos.z += bestPush;
    pushSign = bestPush > 0 ? 1 : -1;
    corrected = true;
  }
  return corrected;
}

function isFree(world: CollisionWorld, pos: Vec3Like, radius: number, height: number): boolean {
  const box = playerBox(pos, radius, height);
  box.minY += 0.02;
  return world.isFree(box);
}

export function collideAndSlide(
  world: CollisionWorld,
  pos: Vec3Like,
  vel: Vec3Like,
  dt: number,
  wasGrounded: boolean,
  cfg: GameConfig,
): MoveResult {
  const radius = cfg.playerRadius;
  const height = cfg.playerHeight;
  const startX = pos.x;
  const startY = pos.y;
  const startZ = pos.z;

  // ── horizontal ──────────────────────────────────────────────────────────────
  pos.x += vel.x * dt;
  const blockedX = resolveAxis(world, pos, radius, height, 0);
  pos.z += vel.z * dt;
  const blockedZ = resolveAxis(world, pos, radius, height, 2);
  let blocked = blockedX || blockedZ;

  if (blocked && wasGrounded && cfg.stepHeight > 0) {
    const stepped = { x: startX, y: startY + cfg.stepHeight, z: startZ };
    if (isFree(world, stepped, radius, height)) {
      stepped.x += vel.x * dt;
      resolveAxis(world, stepped, radius, height, 0);
      stepped.z += vel.z * dt;
      resolveAxis(world, stepped, radius, height, 2);
      stepped.y -= cfg.stepHeight;
      resolveAxis(world, stepped, radius, height, 1);

      const gained = (stepped.x - startX) ** 2 + (stepped.z - startZ) ** 2;
      const current = (pos.x - startX) ** 2 + (pos.z - startZ) ** 2;
      // Only take the stepped-up result if it is genuinely better *and* leaves the player
      // somewhere legal — skipping the free check is how a player ends up embedded.
      if (
        gained > current + 1e-4 &&
        stepped.y >= startY - EPS &&
        isFree(world, stepped, radius, height)
      ) {
        pos.x = stepped.x;
        pos.y = stepped.y;
        pos.z = stepped.z;
        blocked = false;
      }
    }
  }

  if (blockedX && Math.abs(pos.x - startX) < Math.abs(vel.x * dt) * 0.5) vel.x = 0;
  if (blockedZ && Math.abs(pos.z - startZ) < Math.abs(vel.z * dt) * 0.5) vel.z = 0;

  // ── vertical ────────────────────────────────────────────────────────────────
  const fallingSpeed = vel.y;
  pos.y += vel.y * dt;
  const hitVertical = resolveAxis(world, pos, radius, height, 1);

  let grounded = false;
  let landed = false;
  let landSpeed = 0;
  if (hitVertical) {
    if (vel.y <= 0) {
      grounded = true;
      if (!wasGrounded) {
        landed = true;
        landSpeed = -fallingSpeed;
      }
    }
    vel.y = 0;
  } else {
    const probe = { x: pos.x, y: pos.y - 0.06, z: pos.z };
    if (vel.y <= 0 && !isFree(world, probe, radius, height)) grounded = true;
  }

  return { grounded, landed, landSpeed, blocked };
}

function accelerate(
  vel: Vec3Like,
  wishX: number,
  wishZ: number,
  wishSpeed: number,
  accel: number,
  dt: number,
): void {
  const current = vel.x * wishX + vel.z * wishZ;
  const addSpeed = wishSpeed - current;
  if (addSpeed <= 0) return;
  let accelSpeed = accel * dt * wishSpeed;
  if (accelSpeed > addSpeed) accelSpeed = addSpeed;
  vel.x += wishX * accelSpeed;
  vel.z += wishZ * accelSpeed;
}

function friction(vel: Vec3Like, dt: number, cfg: GameConfig): void {
  const speed = Math.sqrt(vel.x * vel.x + vel.z * vel.z);
  if (speed < EPS) {
    vel.x = 0;
    vel.z = 0;
    return;
  }
  const control = speed > cfg.stopSpeed ? speed : cfg.stopSpeed;
  const drop = control * cfg.friction * dt;
  let newSpeed = speed - drop;
  if (newSpeed < 0) newSpeed = 0;
  const scale = newSpeed / speed;
  vel.x *= scale;
  vel.z *= scale;
}

/** One movement step. Mutates `pos` and `vel`. */
export function stepMovement(
  world: CollisionWorld,
  pos: Vec3Like,
  vel: Vec3Like,
  yaw: number,
  keys: number,
  dt: number,
  wasGrounded: boolean,
  cfg: GameConfig,
): MoveResult {
  const moveF = (keys & K_FORWARD ? 1 : 0) - (keys & K_BACK ? 1 : 0);
  const moveR = (keys & K_RIGHT ? 1 : 0) - (keys & K_LEFT ? 1 : 0);

  let wishX = 0;
  let wishZ = 0;
  if (moveF || moveR) {
    // yaw 0 looks down -Z; right is +X. Must match mathx.forward_xz / right_xz.
    const fx = -Math.sin(yaw);
    const fz = -Math.cos(yaw);
    const rx = Math.cos(yaw);
    const rz = -Math.sin(yaw);
    wishX = fx * moveF + rx * moveR;
    wishZ = fz * moveF + rz * moveR;
    const norm = Math.sqrt(wishX * wishX + wishZ * wishZ);
    if (norm > EPS) {
      wishX /= norm;
      wishZ /= norm;
    }
  }

  // Priority matters: aiming down sights overrides sprint, so holding shift while scoped
  // does not quietly give you rifle mobility with sniper accuracy.
  const ads = (keys & K_ADS) !== 0;
  const sprinting = (keys & K_SPRINT) !== 0 && moveF > 0 && wasGrounded && !ads;
  const crouching = (keys & K_CROUCH) !== 0;
  let wishSpeed = ads
    ? cfg.adsSpeed
    : crouching
      ? cfg.crouchSpeed
      : sprinting
        ? cfg.sprintSpeed
        : cfg.walkSpeed;
  if (moveF === 0 && moveR === 0) wishSpeed = 0;

  let grounded = wasGrounded;
  if (grounded) {
    friction(vel, dt, cfg);
    accelerate(vel, wishX, wishZ, wishSpeed, cfg.groundAccel, dt);
    if (keys & K_JUMP) {
      vel.y = cfg.jumpSpeed;
      grounded = false;
    }
  } else {
    const airWish = wishSpeed < cfg.airCap ? wishSpeed : cfg.airCap;
    accelerate(vel, wishX, wishZ, airWish, cfg.airAccel, dt);
  }

  if (!grounded) {
    vel.y -= cfg.gravity * dt;
    if (vel.y < -cfg.maxFallSpeed) vel.y = -cfg.maxFallSpeed;
  }

  const result = collideAndSlide(world, pos, vel, dt, grounded, cfg);

  const b = world.bounds;
  const margin = cfg.playerRadius + 0.1;
  if (pos.x < b.minX + margin) {
    pos.x = b.minX + margin;
    vel.x = Math.max(0, vel.x);
  } else if (pos.x > b.maxX - margin) {
    pos.x = b.maxX - margin;
    vel.x = Math.min(0, vel.x);
  }
  if (pos.z < b.minZ + margin) {
    pos.z = b.minZ + margin;
    vel.z = Math.max(0, vel.z);
  } else if (pos.z > b.maxZ - margin) {
    pos.z = b.maxZ - margin;
    vel.z = Math.min(0, vel.z);
  }

  return result;
}
