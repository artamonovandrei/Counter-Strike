// path: frontend/src/remote.ts
//
// Remote players: models, interpolation and nameplates.
//
// Remote entities are drawn ~100 ms in the past, blended between the two snapshots that
// bracket that moment. That delay is what buys smooth motion from a 30 Hz snapshot rate;
// without it, players visibly step 30 times a second. It's also exactly the delay the
// server compensates for when it rewinds the world to resolve your shots.

import * as THREE from 'three';
import {
  F_DEAD,
  F_MOVING,
  TEAM_COLORS,
  type EntState,
  type Snapshot,
  type Team,
} from '@shared/protocol';

interface RemoteModel {
  group: THREE.Group;
  body: THREE.Mesh;
  head: THREE.Mesh;
  gun: THREE.Mesh;
  legL: THREE.Mesh;
  legR: THREE.Mesh;
  nameplate: THREE.Sprite | null;
  team: Team;
  name: string;
  walkPhase: number;
  lastSeen: number;
}

const BODY_HEIGHT = 1.45;
const HEAD_SIZE = 0.34;

export class RemotePlayers {
  private models = new Map<number, RemoteModel>();
  private names = new Map<number, { name: string; team: Team }>();
  private geometries: THREE.BufferGeometry[] = [];
  private materials: THREE.Material[] = [];

  constructor(
    private scene: THREE.Scene,
    private localTeam: Team,
  ) {}

  setNameFor(id: number, name: string, team: Team): void {
    this.names.set(id, { name, team });
    const model = this.models.get(id);
    if (model && model.name !== name) {
      model.name = name;
      this.scene.remove(model.group);
      this.models.delete(id);
    }
  }

  /**
   * Interpolate every entity between two snapshots.
   *
   * Entities missing from the newer snapshot are removed rather than left frozen: a
   * player who disconnects should vanish, not stand there as a target.
   */
  update(from: Snapshot, to: Snapshot, alpha: number, dt: number, now: number): void {
    const byId = new Map<number, EntState>();
    for (const e of to.ents) byId.set(e.id, e);
    const prevById = new Map<number, EntState>();
    for (const e of from.ents) prevById.set(e.id, e);

    for (const [id, target] of byId) {
      const prev = prevById.get(id) ?? target;
      const model = this.ensure(id, target.t);

      model.group.position.set(
        lerp(prev.x, target.x, alpha),
        lerp(prev.y, target.y, alpha),
        lerp(prev.z, target.z, alpha),
      );
      const yaw = lerpAngle(prev.a, target.a, alpha);
      model.group.rotation.y = yaw;

      const dead = (target.f & F_DEAD) !== 0;
      model.group.visible = !dead;
      model.lastSeen = now;

      // Pitch only the head and gun; rotating the whole body looks like a broken puppet.
      const pitch = lerpAngle(prev.p, target.p, alpha);
      model.head.rotation.x = clamp(pitch, -0.9, 0.9);
      model.gun.rotation.x = clamp(pitch, -1.2, 1.2);

      // Cheap walk cycle driven by the MOVING flag — enough to read as movement at range.
      if ((target.f & F_MOVING) !== 0 && !dead) {
        model.walkPhase += dt * 9;
        const swing = Math.sin(model.walkPhase) * 0.35;
        model.legL.rotation.x = swing;
        model.legR.rotation.x = -swing;
      } else {
        model.legL.rotation.x *= 0.8;
        model.legR.rotation.x *= 0.8;
      }

      if (model.nameplate) model.nameplate.visible = !dead;
    }

    for (const [id, model] of this.models) {
      if (!byId.has(id)) {
        this.scene.remove(model.group);
        this.models.delete(id);
      }
    }
  }

  remove(id: number): void {
    const model = this.models.get(id);
    if (model) {
      this.scene.remove(model.group);
      this.models.delete(id);
    }
    this.names.delete(id);
  }

  positionOf(id: number): THREE.Vector3 | null {
    return this.models.get(id)?.group.position ?? null;
  }

  clear(): void {
    for (const model of this.models.values()) this.scene.remove(model.group);
    this.models.clear();
  }

  dispose(): void {
    this.clear();
    for (const g of this.geometries) g.dispose();
    for (const m of this.materials) m.dispose();
  }

  private ensure(id: number, team: Team): RemoteModel {
    const existing = this.models.get(id);
    if (existing) return existing;

    const info = this.names.get(id);
    const model = this.build(team, info?.name ?? '');
    this.models.set(id, model);
    this.scene.add(model.group);
    return model;
  }

  private track<T extends THREE.BufferGeometry>(g: T): T {
    this.geometries.push(g);
    return g;
  }

  private trackMat<T extends THREE.Material>(m: T): T {
    this.materials.push(m);
    return m;
  }

  private build(team: Team, name: string): RemoteModel {
    const group = new THREE.Group();
    const color = new THREE.Color(TEAM_COLORS[team]);
    const dark = color.clone().multiplyScalar(0.55);

    const bodyMat = this.trackMat(
      new THREE.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0.05 }),
    );
    const legMat = this.trackMat(
      new THREE.MeshStandardMaterial({ color: dark, roughness: 0.8, metalness: 0.05 }),
    );
    const gunMat = this.trackMat(
      new THREE.MeshStandardMaterial({ color: 0x2b2f36, roughness: 0.4, metalness: 0.7 }),
    );

    // Torso sits on top of the legs; the whole rig is anchored at the feet so it lines up
    // with the server's position, which is also at the feet.
    const body = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.55, 0.75, 0.34)), bodyMat);
    body.position.y = BODY_HEIGHT - 0.38;
    group.add(body);

    const head = new THREE.Mesh(
      this.track(new THREE.BoxGeometry(HEAD_SIZE, HEAD_SIZE, HEAD_SIZE)),
      bodyMat,
    );
    head.position.y = BODY_HEIGHT + 0.17;
    group.add(head);

    const legGeo = this.track(new THREE.BoxGeometry(0.2, 0.72, 0.24));
    const legL = new THREE.Mesh(legGeo, legMat);
    legL.position.set(-0.15, 0.72, 0);
    legL.geometry.translate(0, -0.36, 0); // pivot at the hip so rotation swings the leg
    group.add(legL);

    const legR = new THREE.Mesh(legGeo.clone(), legMat);
    legR.position.set(0.15, 0.72, 0);
    group.add(legR);

    const gun = new THREE.Mesh(this.track(new THREE.BoxGeometry(0.09, 0.13, 0.68)), gunMat);
    gun.position.set(0.22, BODY_HEIGHT - 0.3, -0.35);
    group.add(gun);

    // Nameplates only for teammates: seeing an enemy's name floating over cover would be
    // a wallhack with extra steps.
    let nameplate: THREE.Sprite | null = null;
    if (team === this.localTeam && name) {
      nameplate = makeNameplate(name, TEAM_COLORS[team]);
      nameplate.position.y = BODY_HEIGHT + 0.62;
      group.add(nameplate);
    }

    return {
      group,
      body,
      head,
      gun,
      legL,
      legR,
      nameplate,
      team,
      name,
      walkPhase: Math.random() * Math.PI * 2,
      lastSeen: 0,
    };
  }
}

function makeNameplate(text: string, color: string): THREE.Sprite {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  ctx.font = 'bold 34px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.lineWidth = 6;
  ctx.strokeStyle = 'rgba(0,0,0,0.85)';
  ctx.strokeText(text, 128, 32);
  ctx.fillStyle = color;
  ctx.fillText(text, 128, 32);

  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true }),
  );
  sprite.scale.set(1.6, 0.4, 1);
  sprite.renderOrder = 10;
  return sprite;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function lerpAngle(a: number, b: number, t: number): number {
  let d = b - a;
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;
  return a + d * t;
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}
