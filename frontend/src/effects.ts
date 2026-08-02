// path: frontend/src/effects.ts
//
// Everything that flashes, sparks or swings: the first-person weapon, muzzle flashes,
// tracers, impacts, bullet holes and blood.
//
// All of it is pooled. Allocating geometry per bullet would hand the garbage collector a
// steady stream of work during a firefight, which is exactly when a frame hitch is least
// forgivable.

import * as THREE from 'three';
import type { GameConfig, WeaponConfig, WeaponId } from '@shared/protocol';
import { bulletHoleTexture, flashTexture, glowTexture } from './textures';
import { buildWeaponModel, muzzleOf } from './weapons3d';

const TRACER_POOL = 64;
const IMPACT_POOL = 40;
const HOLE_POOL = 48;
const TRACER_LIFE = 0.075;
const IMPACT_LIFE = 0.5;
const HOLE_LIFE = 14;

interface Tracer {
  mesh: THREE.Mesh;
  life: number;
}

interface Impact {
  points: THREE.Points;
  velocities: Float32Array;
  life: number;
}

interface Hole {
  mesh: THREE.Mesh;
  life: number;
}

/** Where the view model sits at the hip and when sighted, per weapon. */
interface Pose {
  hip: THREE.Vector3;
  ads: THREE.Vector3;
  hipRot: THREE.Euler;
}

const POSES: Record<WeaponId, Pose> = {
  rifle: {
    hip: new THREE.Vector3(0.16, -0.17, -0.3),
    ads: new THREE.Vector3(0, -0.098, -0.22),
    hipRot: new THREE.Euler(0.02, 0.05, 0.03),
  },
  smg: {
    hip: new THREE.Vector3(0.15, -0.16, -0.26),
    ads: new THREE.Vector3(0, -0.075, -0.2),
    hipRot: new THREE.Euler(0.02, 0.06, 0.04),
  },
  sniper: {
    hip: new THREE.Vector3(0.17, -0.18, -0.32),
    ads: new THREE.Vector3(0, -0.11, -0.1),
    hipRot: new THREE.Euler(0.01, 0.04, 0.02),
  },
  shotgun: {
    hip: new THREE.Vector3(0.16, -0.17, -0.3),
    ads: new THREE.Vector3(0, -0.08, -0.2),
    hipRot: new THREE.Euler(0.02, 0.05, 0.03),
  },
  pistol: {
    hip: new THREE.Vector3(0.13, -0.16, -0.24),
    ads: new THREE.Vector3(0, -0.065, -0.16),
    hipRot: new THREE.Euler(0.02, 0.06, 0.04),
  },
  knife: {
    hip: new THREE.Vector3(0.18, -0.2, -0.26),
    ads: new THREE.Vector3(0.18, -0.2, -0.26),
    hipRot: new THREE.Euler(0.1, 0.3, -0.2),
  },
};

export interface ViewModelState {
  speed: number;
  grounded: boolean;
  adsProgress: number;
  reloading: boolean;
  dead: boolean;
}

export class Effects {
  private tracers: Tracer[] = [];
  private tracerIndex = 0;
  private impacts: Impact[] = [];
  private impactIndex = 0;
  private holes: Hole[] = [];
  private holeIndex = 0;

  private muzzleLight: THREE.PointLight;
  private muzzleSprite: THREE.Sprite;
  private muzzleLife = 0;

  /** Container the view model hangs off, so the weapon can be swapped wholesale. */
  private viewRoot = new THREE.Group();
  private viewModel: THREE.Group | null = null;
  private viewWeapon: WeaponId = 'rifle';
  private viewConfig: WeaponConfig | null = null;
  private viewMuzzle = new THREE.Object3D();

  private recoil = 0;
  private recoilRot = 0;
  private sway = new THREE.Vector2();
  private bobPhase = 0;
  private drawTimer = 0;
  private drawTime = 0.5;
  private cycle = 0; // 0..1 animation of slide / pump / bolt
  private slide: THREE.Object3D | null = null;
  private pump: THREE.Object3D | null = null;

  constructor(
    private scene: THREE.Scene,
    private camera: THREE.PerspectiveCamera,
  ) {
    const tracerGeo = new THREE.CylinderGeometry(0.011, 0.011, 1, 5, 1, true);
    tracerGeo.rotateX(Math.PI / 2); // point down -Z so lookAt aims it
    for (let i = 0; i < TRACER_POOL; i++) {
      const mat = new THREE.MeshBasicMaterial({
        color: 0xffe6a0,
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        fog: false,
      });
      const mesh = new THREE.Mesh(tracerGeo, mat);
      mesh.visible = false;
      mesh.frustumCulled = false;
      scene.add(mesh);
      this.tracers.push({ mesh, life: 0 });
    }

    const sparkTex = glowTexture();
    for (let i = 0; i < IMPACT_POOL; i++) {
      const count = 12;
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
      const mat = new THREE.PointsMaterial({
        color: 0xffcf8a,
        size: 0.075,
        map: sparkTex,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
        sizeAttenuation: true,
      });
      const points = new THREE.Points(geo, mat);
      points.visible = false;
      points.frustumCulled = false;
      scene.add(points);
      this.impacts.push({ points, velocities: new Float32Array(count * 3), life: 0 });
    }

    const holeGeo = new THREE.PlaneGeometry(0.16, 0.16);
    const holeTex = bulletHoleTexture();
    for (let i = 0; i < HOLE_POOL; i++) {
      const mat = new THREE.MeshBasicMaterial({
        map: holeTex,
        transparent: true,
        depthWrite: false,
        // Pull the decal toward the camera in depth so it never z-fights the wall.
        polygonOffset: true,
        polygonOffsetFactor: -4,
        polygonOffsetUnits: -4,
      });
      const mesh = new THREE.Mesh(holeGeo, mat);
      mesh.visible = false;
      scene.add(mesh);
      this.holes.push({ mesh, life: 0 });
    }

    this.muzzleLight = new THREE.PointLight(0xffbb66, 0, 14, 2);
    scene.add(this.muzzleLight);

    this.muzzleSprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: flashTexture(),
        transparent: true,
        depthWrite: false,
        depthTest: false,
        blending: THREE.AdditiveBlending,
        opacity: 0,
        rotation: 0,
      }),
    );
    this.muzzleSprite.scale.setScalar(0.55);
    this.muzzleSprite.renderOrder = 20;
    scene.add(this.muzzleSprite);

    camera.add(this.viewRoot);
    scene.add(camera); // the camera must be in the graph for its children to render
    this.viewRoot.add(this.viewMuzzle);
  }

  // ── first-person weapon ─────────────────────────────────────────────────────

  /** Swap the held weapon and play the draw animation. */
  setWeapon(id: WeaponId, config: WeaponConfig | null): void {
    if (this.viewModel && this.viewWeapon === id) return;
    if (this.viewModel) {
      this.viewRoot.remove(this.viewModel);
      disposeObject(this.viewModel);
    }

    this.viewWeapon = id;
    this.viewConfig = config;
    this.viewModel = buildWeaponModel(id);
    // View models must not cast shadows: the weapon is inches from the camera, so its
    // shadow would smear across the entire screen.
    this.viewModel.traverse((o) => {
      o.castShadow = false;
      o.receiveShadow = false;
      // Render after the world with a cleared depth range would be ideal; short of that,
      // a small scale keeps it from poking through nearby walls.
    });
    this.viewModel.scale.setScalar(0.92);
    this.viewRoot.add(this.viewModel);

    this.slide = this.viewModel.getObjectByName('slide') ?? null;
    this.pump = this.viewModel.getObjectByName('pump') ?? null;

    this.viewMuzzle.position.copy(muzzleOf(id)).multiplyScalar(0.92);
    this.drawTime = Math.max(0.15, config?.switchTime ?? 0.5);
    this.drawTimer = this.drawTime;
    this.cycle = 0;
  }

  /** Per-frame view model motion: draw, bob, sway, ADS, recoil recovery, action cycle. */
  updateViewModel(dt: number, state: ViewModelState, cfg: GameConfig): void {
    const model = this.viewRoot;
    const pose = POSES[this.viewWeapon] ?? POSES.rifle;

    this.recoil *= Math.exp(-13 * dt);
    this.recoilRot *= Math.exp(-11 * dt);
    this.sway.multiplyScalar(Math.exp(-9 * dt));
    if (this.cycle > 0) this.cycle = Math.max(0, this.cycle - dt * 6);
    if (this.drawTimer > 0) this.drawTimer = Math.max(0, this.drawTimer - dt);

    const ads = state.adsProgress;

    // Bob scales with speed and is almost entirely suppressed while sighted — a swaying
    // scope is the fastest way to make aiming feel broken.
    this.bobPhase += dt * Math.min(state.speed, cfg.sprintSpeed) * 2.1;
    const bobAmount =
      (state.grounded ? Math.min(state.speed / cfg.sprintSpeed, 1) * 0.016 : 0.005) * (1 - ads * 0.9);
    const bobX = Math.cos(this.bobPhase) * bobAmount;
    const bobY = Math.abs(Math.sin(this.bobPhase)) * bobAmount;

    // Position: blend hip → ADS, then add bob, sway and recoil.
    const target = pose.hip.clone().lerp(pose.ads, ads);
    target.x += (bobX + this.sway.x) * (1 - ads * 0.8);
    target.y += (bobY + this.sway.y) * (1 - ads * 0.8);
    target.z += this.recoil * 0.55;

    // Sprinting lowers and angles the weapon; reloading dips it. Both read instantly as
    // "you cannot shoot right now", which is more useful than any HUD text.
    const sprinting = state.grounded && state.speed > cfg.walkSpeed * 1.05 && ads < 0.1;
    if (sprinting) {
      target.y -= 0.06;
      target.z += 0.04;
    }
    if (state.reloading) {
      target.y -= 0.07;
      target.x += 0.02;
    }
    if (this.drawTimer > 0) {
      const t = this.drawTimer / this.drawTime; // 1 → 0
      target.y -= 0.28 * t * t;
      target.z += 0.06 * t;
    }

    model.position.lerp(target, Math.min(1, dt * 22));

    // Rotation.
    const rx =
      pose.hipRot.x * (1 - ads) +
      this.recoilRot * 2.4 +
      (sprinting ? 0.25 : 0) +
      (state.reloading ? 0.35 : 0) +
      (this.drawTimer > 0 ? 0.7 * (this.drawTimer / this.drawTime) : 0);
    const ry = pose.hipRot.y * (1 - ads) + (sprinting ? 0.5 : 0) - this.sway.x * 1.2;
    const rz = pose.hipRot.z * (1 - ads) + (sprinting ? -0.2 : 0) + this.sway.y * 0.8;
    model.rotation.set(
      THREE.MathUtils.lerp(model.rotation.x, rx, Math.min(1, dt * 18)),
      THREE.MathUtils.lerp(model.rotation.y, ry, Math.min(1, dt * 18)),
      THREE.MathUtils.lerp(model.rotation.z, rz, Math.min(1, dt * 18)),
    );

    // Moving parts.
    if (this.slide) this.slide.position.z = 0.09 * this.cycle;
    if (this.pump) this.pump.position.z = 0.1 * this.cycle;

    // A scoped weapon hides its model at full zoom: you are looking *through* the optic,
    // not at it, and the overlay takes over.
    const scoped = this.viewConfig?.scope === true;
    if (this.viewModel) this.viewModel.visible = !(scoped && ads > 0.82) && !state.dead;
  }

  /** Nudge the view model when the camera turns, so the gun lags behind slightly. */
  addSway(dYaw: number, dPitch: number): void {
    this.sway.x = THREE.MathUtils.clamp(this.sway.x + dYaw * 0.4, -0.06, 0.06);
    this.sway.y = THREE.MathUtils.clamp(this.sway.y + dPitch * 0.4, -0.06, 0.06);
  }

  /** Called when the local player fires: kicks the model and cycles the action. */
  fired(strength = 1): void {
    this.recoil += 0.045 * strength;
    this.recoilRot += 0.05 * strength;
    this.cycle = 1;
    this.flashAtViewMuzzle(strength);
  }

  private flashAtViewMuzzle(strength: number): void {
    const world = new THREE.Vector3();
    this.viewMuzzle.getWorldPosition(world);
    this.muzzleFlash(world, strength);
  }

  /** World position of the held weapon's muzzle — where local tracers should start. */
  viewMuzzleWorld(out = new THREE.Vector3()): THREE.Vector3 {
    return this.viewMuzzle.getWorldPosition(out);
  }

  // ── shots ───────────────────────────────────────────────────────────────────

  tracer(from: THREE.Vector3, to: THREE.Vector3): void {
    const slot = this.tracers[this.tracerIndex];
    this.tracerIndex = (this.tracerIndex + 1) % this.tracers.length;

    const length = from.distanceTo(to);
    if (length < 0.05) return;
    slot.mesh.position.copy(from).lerp(to, 0.5);
    slot.mesh.lookAt(to);
    slot.mesh.scale.set(1, 1, length);
    slot.mesh.visible = true;
    (slot.mesh.material as THREE.MeshBasicMaterial).opacity = 0.9;
    slot.life = TRACER_LIFE;
  }

  muzzleFlash(at: THREE.Vector3, strength = 1): void {
    this.muzzleLight.position.copy(at);
    this.muzzleLight.intensity = 26 * strength;
    this.muzzleSprite.position.copy(at);
    this.muzzleSprite.scale.setScalar(0.28 + 0.34 * strength);
    // Random roll each shot so a long spray doesn't look like a looping animation.
    (this.muzzleSprite.material as THREE.SpriteMaterial).rotation = Math.random() * Math.PI * 2;
    (this.muzzleSprite.material as THREE.SpriteMaterial).opacity = 1;
    this.muzzleLife = 0.055;
  }

  impact(at: THREE.Vector3, normal: THREE.Vector3, material: string): void {
    this.sparks(at, normal, material);
    this.bulletHole(at, normal);
  }

  private sparks(at: THREE.Vector3, normal: THREE.Vector3, material: string): void {
    const slot = this.impacts[this.impactIndex];
    this.impactIndex = (this.impactIndex + 1) % this.impacts.length;

    const positions = slot.points.geometry.getAttribute('position') as THREE.BufferAttribute;
    for (let i = 0; i < positions.count; i++) {
      positions.setXYZ(i, at.x, at.y, at.z);
      slot.velocities[i * 3] = normal.x * 2.2 + (Math.random() - 0.5) * 3.2;
      slot.velocities[i * 3 + 1] = normal.y * 2.2 + Math.random() * 2.8;
      slot.velocities[i * 3 + 2] = normal.z * 2.2 + (Math.random() - 0.5) * 3.2;
    }
    positions.needsUpdate = true;

    const mat = slot.points.material as THREE.PointsMaterial;
    mat.color.set(
      material === 'metal'
        ? 0xd8ecff
        : material === 'crate'
          ? 0xd9a86a
          : material === 'blood'
            ? 0xc8203a
            : 0xffcf8a,
    );
    mat.opacity = 1;
    slot.points.visible = true;
    slot.life = IMPACT_LIFE;
  }

  private bulletHole(at: THREE.Vector3, normal: THREE.Vector3): void {
    const slot = this.holes[this.holeIndex];
    this.holeIndex = (this.holeIndex + 1) % this.holes.length;

    slot.mesh.position.copy(at).addScaledVector(normal, 0.006);
    slot.mesh.lookAt(slot.mesh.position.clone().add(normal));
    slot.mesh.rotateZ(Math.random() * Math.PI * 2);
    slot.mesh.scale.setScalar(0.7 + Math.random() * 0.5);
    (slot.mesh.material as THREE.MeshBasicMaterial).opacity = 1;
    slot.mesh.visible = true;
    slot.life = HOLE_LIFE;
  }

  bloodPuff(at: THREE.Vector3, towards: THREE.Vector3): void {
    this.sparks(at, towards, 'blood');
  }

  // ── per-frame ───────────────────────────────────────────────────────────────

  update(dt: number): void {
    for (const t of this.tracers) {
      if (t.life <= 0) continue;
      t.life -= dt;
      (t.mesh.material as THREE.MeshBasicMaterial).opacity = Math.max(
        0,
        (t.life / TRACER_LIFE) * 0.9,
      );
      if (t.life <= 0) t.mesh.visible = false;
    }

    for (const imp of this.impacts) {
      if (imp.life <= 0) continue;
      imp.life -= dt;
      const positions = imp.points.geometry.getAttribute('position') as THREE.BufferAttribute;
      const arr = positions.array as Float32Array;
      for (let i = 0; i < positions.count; i++) {
        const o = i * 3;
        imp.velocities[o + 1] -= 11 * dt;
        arr[o] += imp.velocities[o] * dt;
        arr[o + 1] += imp.velocities[o + 1] * dt;
        arr[o + 2] += imp.velocities[o + 2] * dt;
      }
      positions.needsUpdate = true;
      (imp.points.material as THREE.PointsMaterial).opacity = Math.max(0, imp.life / IMPACT_LIFE);
      if (imp.life <= 0) imp.points.visible = false;
    }

    for (const h of this.holes) {
      if (h.life <= 0) continue;
      h.life -= dt;
      // Hold full opacity, then fade over the last two seconds.
      if (h.life < 2) (h.mesh.material as THREE.MeshBasicMaterial).opacity = h.life / 2;
      if (h.life <= 0) h.mesh.visible = false;
    }

    if (this.muzzleLife > 0) {
      this.muzzleLife -= dt;
      this.muzzleLight.intensity *= Math.exp(-32 * dt);
      const mat = this.muzzleSprite.material as THREE.SpriteMaterial;
      mat.opacity = Math.max(0, mat.opacity - dt * 22);
      if (this.muzzleLife <= 0) {
        this.muzzleLight.intensity = 0;
        mat.opacity = 0;
      }
    }
  }

  dispose(): void {
    for (const t of this.tracers) {
      this.scene.remove(t.mesh);
      (t.mesh.material as THREE.Material).dispose();
    }
    for (const i of this.impacts) {
      this.scene.remove(i.points);
      i.points.geometry.dispose();
      (i.points.material as THREE.Material).dispose();
    }
    for (const h of this.holes) {
      this.scene.remove(h.mesh);
      (h.mesh.material as THREE.Material).dispose();
    }
    if (this.viewModel) disposeObject(this.viewModel);
    this.camera.remove(this.viewRoot);
    this.scene.remove(this.muzzleLight);
    this.scene.remove(this.muzzleSprite);
    (this.muzzleSprite.material as THREE.Material).dispose();
  }
}

function disposeObject(root: THREE.Object3D): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
  });
}
