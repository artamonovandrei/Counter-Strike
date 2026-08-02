// path: frontend/src/remote.ts
//
// Remote players: rig, animation, interpolation and nameplates.
//
// Remote entities are drawn ~100 ms in the past, blended between the two snapshots that
// bracket that moment. That delay is what buys smooth motion from a 30 Hz snapshot rate;
// without it, players visibly step 30 times a second. It is also exactly the delay the
// server compensates for when it rewinds the world to resolve your shots.
//
// The rig is hierarchical on purpose. Limbs rotate about their joints, the torso pitches
// with the aim, and the weapon rides in the right hand — so a player's pose actually tells
// you what they are doing (running, aiming, holding a sniper) from across the map.

import * as THREE from 'three';
import {
  F_ADS,
  F_AIRBORNE,
  F_DEAD,
  F_MOVING,
  F_SPRINTING,
  TEAM_COLORS,
  type EntState,
  type Snapshot,
  type Team,
  type WeaponId,
} from '@shared/protocol';
import { buildWeaponModel, foregripOf } from './weapons3d';

const HIP_Y = 0.9;
const TORSO_Y = 0.42; // above the hips
const HEAD_Y = 0.78; // above the hips

interface RemoteModel {
  root: THREE.Group;
  /** Yaws with the view; everything above the legs hangs off this. */
  upper: THREE.Group;
  torso: THREE.Group;
  head: THREE.Group;
  armR: THREE.Group;
  armL: THREE.Group;
  legL: THREE.Group;
  legR: THREE.Group;
  hand: THREE.Group;
  weapon: THREE.Group | null;
  weaponId: WeaponId | null;
  nameplate: THREE.Sprite | null;
  team: Team;
  name: string;
  walkPhase: number;
  deathProgress: number;
  deathTilt: number;
  wasDead: boolean;
  adsBlend: number;
}

interface TeamPalette {
  uniform: THREE.MeshStandardMaterial;
  vest: THREE.MeshStandardMaterial;
  gear: THREE.MeshStandardMaterial;
  skin: THREE.MeshStandardMaterial;
  visor: THREE.MeshStandardMaterial;
}

const palettes = new Map<Team, TeamPalette>();

function teamPalette(team: Team): TeamPalette {
  const hit = palettes.get(team);
  if (hit) return hit;

  const accent = new THREE.Color(TEAM_COLORS[team]);
  // Players are deliberately lighter than the scenery they stand in front of. Realism
  // would put soldiers in dark fatigues; that makes them nearly invisible against a wall
  // in shadow, and "I never saw him" is a worse experience than "that uniform is a bit
  // bright". The team colour also has to survive at 40 m, so it stays saturated.
  const uniform = accent.clone().multiplyScalar(0.85).lerp(new THREE.Color(0x9aa3ae), 0.4);

  const palette: TeamPalette = {
    uniform: new THREE.MeshStandardMaterial({ color: uniform, roughness: 0.85, metalness: 0.04 }),
    vest: new THREE.MeshStandardMaterial({
      color: accent.clone().multiplyScalar(1.0),
      roughness: 0.65,
      metalness: 0.1,
      // A touch of self-illumination keeps the team colour readable even on the shadow
      // side of a player, where there is no direct light at all.
      emissive: accent.clone().multiplyScalar(0.18),
      emissiveIntensity: 1,
    }),
    gear: new THREE.MeshStandardMaterial({ color: 0x4a515b, roughness: 0.8, metalness: 0.15 }),
    skin: new THREE.MeshStandardMaterial({ color: 0xd0997a, roughness: 0.95, metalness: 0.0 }),
    visor: new THREE.MeshStandardMaterial({
      color: 0x1a2028,
      roughness: 0.2,
      metalness: 0.6,
      emissive: accent.clone().multiplyScalar(0.4),
      emissiveIntensity: 1,
    }),
  };
  palettes.set(team, palette);
  return palette;
}

function part(
  parent: THREE.Object3D,
  mat: THREE.Material,
  w: number,
  h: number,
  d: number,
  x: number,
  y: number,
  z: number,
): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  parent.add(mesh);
  return mesh;
}

export class RemotePlayers {
  private models = new Map<number, RemoteModel>();
  private names = new Map<number, { name: string; team: Team }>();

  constructor(
    private scene: THREE.Scene,
    private localTeam: Team,
  ) {}

  setNameFor(id: number, name: string, team: Team): void {
    this.names.set(id, { name, team });
    const model = this.models.get(id);
    if (model && model.name !== name) {
      // Rebuild so the nameplate picks up the new name.
      this.scene.remove(model.root);
      disposeTree(model.root);
      this.models.delete(id);
    }
  }

  /**
   * Interpolate and animate every entity between two snapshots.
   *
   * Entities missing from the newer snapshot are removed rather than left frozen: a
   * player who disconnects should vanish, not stand there as a target.
   */
  update(from: Snapshot, to: Snapshot, alpha: number, dt: number): void {
    const prevById = new Map<number, EntState>();
    for (const e of from.ents) prevById.set(e.id, e);

    const seen = new Set<number>();
    for (const target of to.ents) {
      seen.add(target.id);
      const prev = prevById.get(target.id) ?? target;
      const model = this.ensure(target.id, target.t);
      this.animate(model, prev, target, alpha, dt);
    }

    for (const [id, model] of this.models) {
      if (!seen.has(id)) {
        this.scene.remove(model.root);
        disposeTree(model.root);
        this.models.delete(id);
      }
    }
  }

  private animate(
    model: RemoteModel,
    prev: EntState,
    target: EntState,
    alpha: number,
    dt: number,
  ): void {
    const dead = (target.f & F_DEAD) !== 0;

    model.root.position.set(
      lerp(prev.x, target.x, alpha),
      lerp(prev.y, target.y, alpha),
      lerp(prev.z, target.z, alpha),
    );

    if (dead) {
      if (!model.wasDead) {
        model.wasDead = true;
        model.deathProgress = 0;
        // Fall to one side or the other, chosen once so the corpse doesn't twitch.
        model.deathTilt = Math.random() > 0.5 ? 1 : -1;
      }
      model.deathProgress = Math.min(1, model.deathProgress + dt * 2.4);
      const t = easeOutCubic(model.deathProgress);
      model.root.rotation.z = model.deathTilt * t * Math.PI * 0.5;
      model.root.position.y += t * 0.32; // the body pivots at the feet, so lift as it turns
      model.upper.rotation.x = t * 0.25;
      model.legL.rotation.x = -t * 0.4;
      model.legR.rotation.x = t * 0.3;
      if (model.nameplate) model.nameplate.visible = false;
      return;
    }

    if (model.wasDead) {
      // Respawned: reset the ragdoll pose immediately.
      model.wasDead = false;
      model.deathProgress = 0;
      model.root.rotation.z = 0;
      model.upper.rotation.x = 0;
      if (model.nameplate) model.nameplate.visible = true;
    }

    // Legs follow the direction of travel, the upper body follows the aim. Approximating
    // both with a single yaw is what makes cheap player models look like mannequins.
    const yaw = lerpAngle(prev.a, target.a, alpha);
    model.root.rotation.y = yaw;

    const pitch = lerpAngle(prev.p, target.p, alpha);
    model.upper.rotation.x = clamp(-pitch * 0.55, -0.5, 0.5);
    model.head.rotation.x = clamp(-pitch * 0.45, -0.6, 0.6);

    const ads = (target.f & F_ADS) !== 0 ? 1 : 0;
    model.adsBlend += (ads - model.adsBlend) * Math.min(1, dt * 9);

    const moving = (target.f & F_MOVING) !== 0;
    const sprinting = (target.f & F_SPRINTING) !== 0;
    const airborne = (target.f & F_AIRBORNE) !== 0;

    if (airborne) {
      // Tuck in the air: legs forward, no walk cycle.
      model.legL.rotation.x = THREE.MathUtils.lerp(model.legL.rotation.x, -0.5, dt * 8);
      model.legR.rotation.x = THREE.MathUtils.lerp(model.legR.rotation.x, -0.25, dt * 8);
    } else if (moving) {
      model.walkPhase += dt * (sprinting ? 13 : 8.5);
      const amp = sprinting ? 0.62 : 0.42;
      const swing = Math.sin(model.walkPhase) * amp;
      model.legL.rotation.x = swing;
      model.legR.rotation.x = -swing;
      // Counter-swing the free arm, but only when not aiming — aiming locks both hands
      // to the weapon.
      model.armL.rotation.x = -swing * 0.25 * (1 - model.adsBlend);
      // A little vertical bounce sells the stride more than bigger leg angles do.
      model.upper.position.y = HIP_Y + Math.abs(Math.sin(model.walkPhase)) * 0.035;
    } else {
      model.walkPhase = 0;
      model.legL.rotation.x *= 1 - Math.min(1, dt * 10);
      model.legR.rotation.x *= 1 - Math.min(1, dt * 10);
      model.armL.rotation.x *= 1 - Math.min(1, dt * 10);
      model.upper.position.y = HIP_Y;
    }

    // Weapon pose: from a relaxed low-ready to shouldered.
    const b = model.adsBlend;
    model.armR.rotation.x = THREE.MathUtils.lerp(-0.15, -0.05, b);
    model.armR.rotation.z = THREE.MathUtils.lerp(0.12, 0.02, b);
    model.hand.position.set(
      THREE.MathUtils.lerp(0.02, -0.09, b),
      THREE.MathUtils.lerp(-0.16, -0.06, b),
      THREE.MathUtils.lerp(-0.12, -0.2, b),
    );
    if (sprinting && b < 0.1) {
      // Weapon swings down while sprinting, matching what the first-person view does.
      model.hand.rotation.x = THREE.MathUtils.lerp(model.hand.rotation.x, 0.55, dt * 8);
      model.hand.rotation.y = THREE.MathUtils.lerp(model.hand.rotation.y, 0.4, dt * 8);
    } else {
      model.hand.rotation.x = THREE.MathUtils.lerp(model.hand.rotation.x, 0, dt * 10);
      model.hand.rotation.y = THREE.MathUtils.lerp(model.hand.rotation.y, 0, dt * 10);
    }

    if (target.w !== model.weaponId) this.setWeapon(model, target.w);

    // Off hand reaches for the weapon's foregrip.
    if (model.weaponId) {
      const grip = foregripOf(model.weaponId);
      model.armL.position.set(-0.22, 0.28, 0);
      model.armL.rotation.z = -0.5 - grip.z * 0.4;
    }
  }

  private ensure(id: number, team: Team): RemoteModel {
    const existing = this.models.get(id);
    if (existing) return existing;
    const info = this.names.get(id);
    const model = this.build(team, info?.name ?? '');
    this.models.set(id, model);
    this.scene.add(model.root);
    return model;
  }

  private setWeapon(model: RemoteModel, id: WeaponId): void {
    if (model.weapon) {
      model.hand.remove(model.weapon);
      disposeTree(model.weapon);
    }
    const weapon = buildWeaponModel(id);
    weapon.scale.setScalar(1.0);
    weapon.traverse((o) => {
      o.castShadow = true;
    });
    model.hand.add(weapon);
    model.weapon = weapon;
    model.weaponId = id;
  }

  private build(team: Team, name: string): RemoteModel {
    const p = teamPalette(team);
    const root = new THREE.Group();

    // ── legs (pivot at the hip so rotation swings the whole leg) ──────────────
    const makeLeg = (x: number): THREE.Group => {
      const g = new THREE.Group();
      g.position.set(x, HIP_Y, 0);
      part(g, p.uniform, 0.19, 0.5, 0.21, 0, -0.25, 0);
      part(g, p.gear, 0.2, 0.16, 0.24, 0, -0.5, 0.01); // boot
      part(g, p.gear, 0.205, 0.07, 0.22, 0, -0.16, 0); // knee pad
      root.add(g);
      return g;
    };
    const legL = makeLeg(-0.13);
    const legR = makeLeg(0.13);

    // ── upper body ────────────────────────────────────────────────────────────
    const upper = new THREE.Group();
    upper.position.y = HIP_Y;
    root.add(upper);

    const torso = new THREE.Group();
    upper.add(torso);
    part(torso, p.uniform, 0.44, 0.5, 0.26, 0, TORSO_Y - 0.05, 0); // chest
    part(torso, p.vest, 0.46, 0.34, 0.3, 0, TORSO_Y, 0.005); // plate carrier
    part(torso, p.gear, 0.2, 0.12, 0.32, 0, TORSO_Y + 0.16, 0); // collar
    part(torso, p.gear, 0.12, 0.1, 0.1, -0.14, TORSO_Y - 0.12, 0.16); // pouch
    part(torso, p.gear, 0.12, 0.1, 0.1, 0.02, TORSO_Y - 0.12, 0.16);
    part(torso, p.gear, 0.16, 0.2, 0.12, 0, TORSO_Y - 0.02, -0.2); // backpack

    const head = new THREE.Group();
    head.position.y = HEAD_Y;
    upper.add(head);
    part(head, p.skin, 0.22, 0.24, 0.22, 0, 0, 0);
    part(head, p.gear, 0.27, 0.16, 0.27, 0, 0.09, 0); // helmet shell
    part(head, p.gear, 0.28, 0.06, 0.1, 0, 0.05, -0.11); // brim
    part(head, p.vest, 0.28, 0.035, 0.28, 0, 0.02, 0); // team band
    part(head, p.visor, 0.2, 0.08, 0.03, 0, 0.005, -0.115); // goggles

    // ── arms ──────────────────────────────────────────────────────────────────
    const armR = new THREE.Group();
    armR.position.set(0.26, TORSO_Y + 0.14, 0);
    upper.add(armR);
    part(armR, p.uniform, 0.15, 0.34, 0.16, 0, -0.16, 0);
    part(armR, p.gear, 0.16, 0.1, 0.17, 0, -0.32, -0.02); // glove

    const armL = new THREE.Group();
    armL.position.set(-0.22, TORSO_Y + 0.14, 0);
    upper.add(armL);
    part(armL, p.uniform, 0.15, 0.32, 0.16, 0, -0.15, 0);
    part(armL, p.gear, 0.16, 0.1, 0.17, 0, -0.3, -0.02);

    // The weapon hangs off a hand node rather than the arm, so aiming can move the gun
    // without dislocating the shoulder.
    const hand = new THREE.Group();
    hand.position.set(0.02, -0.16, -0.12);
    armR.add(hand);

    let nameplate: THREE.Sprite | null = null;
    if (team === this.localTeam && name) {
      nameplate = makeNameplate(name, TEAM_COLORS[team]);
      nameplate.position.y = HIP_Y + HEAD_Y + 0.42;
      root.add(nameplate);
    }

    const model: RemoteModel = {
      root,
      upper,
      torso,
      head,
      armR,
      armL,
      legL,
      legR,
      hand,
      weapon: null,
      weaponId: null,
      nameplate,
      team,
      name,
      walkPhase: Math.random() * Math.PI * 2,
      deathProgress: 0,
      deathTilt: 1,
      wasDead: false,
      adsBlend: 0,
    };
    this.setWeapon(model, 'rifle');
    return model;
  }

  remove(id: number): void {
    const model = this.models.get(id);
    if (model) {
      this.scene.remove(model.root);
      disposeTree(model.root);
      this.models.delete(id);
    }
    this.names.delete(id);
  }

  positionOf(id: number): THREE.Vector3 | null {
    return this.models.get(id)?.root.position ?? null;
  }

  clear(): void {
    for (const model of this.models.values()) {
      this.scene.remove(model.root);
      disposeTree(model.root);
    }
    this.models.clear();
  }

  dispose(): void {
    this.clear();
    for (const p of palettes.values()) {
      for (const mat of Object.values(p)) mat.dispose();
    }
    palettes.clear();
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
    new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true, fog: false }),
  );
  sprite.scale.set(1.5, 0.38, 1);
  sprite.renderOrder = 10;
  return sprite;
}

function disposeTree(root: THREE.Object3D): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    const sprite = o as THREE.Sprite;
    if (sprite.isSprite) {
      const mat = sprite.material as THREE.SpriteMaterial;
      mat.map?.dispose();
      mat.dispose();
    }
  });
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

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}
