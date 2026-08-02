// path: frontend/src/predict.ts
//
// Client-side prediction and reconciliation.
//
// The loop: apply an input locally the instant it's sampled, keep it in a pending list,
// and when a snapshot arrives (which tells us the last input the server consumed) discard
// the acknowledged ones and replay the rest on top of the server's authoritative state.
//
// If the replayed result matches what we already drew, nothing visible happens — the
// common case. When it doesn't, the error is corrected smoothly rather than by snapping,
// unless it's large enough that smoothing would look like the player is skating.

import type { GameConfig, InputCmd, SelfState } from '@shared/protocol';
import { stepMovement, type Vec3Like } from './movement';
import type { CollisionWorld } from './world';

/** Above this, a correction is applied instantly — smoothing it would look worse. */
const SNAP_DISTANCE = 2.0;
/** Below this, the correction is ignored entirely; it's float noise, not a disagreement. */
const IGNORE_DISTANCE = 0.002;
/** Fraction of the remaining error removed per second while smoothing. */
const SMOOTH_RATE = 18;

export interface PendingInput {
  cmd: InputCmd;
  keys: number;
}

export class Predictor {
  /** Predicted state, what the camera follows. */
  pos: Vec3Like = { x: 0, y: 0, z: 0 };
  vel: Vec3Like = { x: 0, y: 0, z: 0 };
  grounded = false;

  /** Visual offset used to bleed off a correction over a few frames. */
  private errorX = 0;
  private errorY = 0;
  private errorZ = 0;

  private pending: PendingInput[] = [];

  /** Diagnostics for the netgraph. */
  lastCorrection = 0;
  corrections = 0;
  hardSnaps = 0;

  constructor(
    private world: CollisionWorld,
    private cfg: GameConfig,
  ) {}

  reset(state: SelfState): void {
    this.pos = { x: state.x, y: state.y, z: state.z };
    this.vel = { x: state.vx, y: state.vy, z: state.vz };
    this.pending.length = 0;
    this.errorX = this.errorY = this.errorZ = 0;
  }

  /** Apply an input immediately and remember it until the server acknowledges it. */
  apply(cmd: InputCmd): void {
    const result = stepMovement(
      this.world,
      this.pos,
      this.vel,
      cmd.y,
      cmd.k,
      cmd.dt / 1000,
      this.grounded,
      this.cfg,
    );
    this.grounded = result.grounded;
    this.pending.push({ cmd, keys: cmd.k });
    // A pathological backlog means the server stopped acknowledging; replaying thousands
    // of inputs would freeze the tab, so cap it.
    if (this.pending.length > 256) this.pending.shift();
  }

  /**
   * Rewind to the server's state, drop acknowledged inputs, replay the rest.
   *
   * Called once per snapshot. `dead` skips the replay: a dead player isn't simulated
   * server-side, so replaying inputs would drift the camera away from the body.
   */
  reconcile(state: SelfState, ack: number, dead: boolean): void {
    const predictedX = this.pos.x;
    const predictedY = this.pos.y;
    const predictedZ = this.pos.z;

    this.pending = this.pending.filter((p) => p.cmd.s > ack);

    this.pos.x = state.x;
    this.pos.y = state.y;
    this.pos.z = state.z;
    this.vel.x = state.vx;
    this.vel.y = state.vy;
    this.vel.z = state.vz;

    if (!dead) {
      for (const p of this.pending) {
        const result = stepMovement(
          this.world,
          this.pos,
          this.vel,
          p.cmd.y,
          p.keys,
          p.cmd.dt / 1000,
          this.grounded,
          this.cfg,
        );
        this.grounded = result.grounded;
      }
    }

    const dx = predictedX - this.pos.x;
    const dy = predictedY - this.pos.y;
    const dz = predictedZ - this.pos.z;
    const dist = Math.hypot(dx, dy, dz);
    this.lastCorrection = dist;

    if (dist < IGNORE_DISTANCE) {
      this.errorX = this.errorY = this.errorZ = 0;
      return;
    }
    if (dist > SNAP_DISTANCE) {
      // Teleport, respawn, or a genuine desync. Show the truth immediately.
      this.errorX = this.errorY = this.errorZ = 0;
      this.hardSnaps++;
      return;
    }
    // Carry the visual error forward and decay it, so the camera glides to the
    // authoritative position instead of jumping to it.
    this.errorX = dx;
    this.errorY = dy;
    this.errorZ = dz;
    this.corrections++;
  }

  /** Decay the smoothing offset. Call once per rendered frame. */
  update(dt: number): void {
    const decay = Math.exp(-SMOOTH_RATE * dt);
    this.errorX *= decay;
    this.errorY *= decay;
    this.errorZ *= decay;
    if (Math.abs(this.errorX) < 1e-4) this.errorX = 0;
    if (Math.abs(this.errorY) < 1e-4) this.errorY = 0;
    if (Math.abs(this.errorZ) < 1e-4) this.errorZ = 0;
  }

  /** Position to render at: authoritative prediction plus the decaying error. */
  renderX(): number {
    return this.pos.x + this.errorX;
  }

  renderY(): number {
    return this.pos.y + this.errorY;
  }

  renderZ(): number {
    return this.pos.z + this.errorZ;
  }

  pendingCount(): number {
    return this.pending.length;
  }

  speed(): number {
    return Math.hypot(this.vel.x, this.vel.z);
  }
}
