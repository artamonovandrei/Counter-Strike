// path: frontend/src/effects.ts
//
// Tracers, impact sparks, muzzle flash and the first-person weapon.
//
// Everything here is pooled. Allocating a geometry per bullet would hand the garbage
// collector a steady stream of work during a firefight, which is exactly when a frame
// hitch is least forgivable.

import * as THREE from 'three';
import type { GameConfig, WeaponId } from '@shared/protocol';

const TRACER_POOL = 48;
const IMPACT_POOL = 32;
const TRACER_LIFE = 0.09;
const IMPACT_LIFE = 0.45;

interface Tracer {
  mesh: THREE.Mesh;
  life: number;
}

interface Impact {
  points: THREE.Points;
  velocities: Float32Array;
  life: number;
}

export class Effects {
  private tracers: Tracer[] = [];
  private tracerIndex = 0;
  private impacts: Impact[] = [];
  private impactIndex = 0;

  private muzzleLight: THREE.PointLight;
  private muzzleLife = 0;

  private viewModel: THREE.Group;
  private viewRecoil = 0;
  private viewSway = new THREE.Vector2();
  private bobPhase = 0;

  constructor(
    private scene: THREE.Scene,
    private camera: THREE.PerspectiveCamera,
  ) {
    const tracerGeo = new THREE.CylinderGeometry(0.012, 0.012, 1, 5, 1, true);
    // Point the cylinder down -Z so it can be aimed with lookAt.
    tracerGeo.rotateX(Math.PI / 2);
    const tracerMat = new THREE.MeshBasicMaterial({
      color: 0xffe9a8,
      transparent: true,
      opacity: 0.9,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    for (let i = 0; i < TRACER_POOL; i++) {
      const mesh = new THREE.Mesh(tracerGeo, tracerMat.clone());
      mesh.visible = false;
      mesh.frustumCulled = false;
      scene.add(mesh);
      this.tracers.push({ mesh, life: 0 });
    }

    for (let i = 0; i < IMPACT_POOL; i++) {
      const count = 10;
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
      const mat = new THREE.PointsMaterial({
        color: 0xffcf8a,
        size: 0.06,
        transparent: true,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      });
      const points = new THREE.Points(geo, mat);
      points.visible = false;
      points.frustumCulled = false;
      scene.add(points);
      this.impacts.push({ points, velocities: new Float32Array(count * 3), life: 0 });
    }

    this.muzzleLight = new THREE.PointLight(0xffcc77, 0, 12, 2);
    scene.add(this.muzzleLight);

    this.viewModel = this.buildViewModel();
    camera.add(this.viewModel);
    scene.add(camera); // the camera must be in the graph for its children to render
  }

  // ── first-person weapon ─────────────────────────────────────────────────────

  private buildViewModel(): THREE.Group {
    const group = new THREE.Group();
    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0x2f343c,
      roughness: 0.45,
      metalness: 0.7,
    });
    const gripMat = new THREE.MeshStandardMaterial({
      color: 0x1d2026,
      roughness: 0.8,
      metalness: 0.15,
    });

    const receiver = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.1, 0.42), bodyMat);
    receiver.position.set(0, 0, -0.1);
    group.add(receiver);

    const barrel = new THREE.Mesh(new THREE.BoxGeometry(0.035, 0.035, 0.34), bodyMat);
    barrel.position.set(0, 0.012, -0.44);
    group.add(barrel);

    const mag = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.14, 0.08), gripMat);
    mag.position.set(0, -0.1, -0.14);
    group.add(mag);

    const grip = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.13, 0.07), gripMat);
    grip.position.set(0, -0.09, 0.03);
    grip.rotation.x = -0.25;
    group.add(grip);

    // Sits low-right of centre so it frames the crosshair without covering it.
    group.position.set(0.17, -0.16, -0.28);
    group.rotation.y = 0.04;
    return group;
  }

  setWeaponVisual(weapon: WeaponId): void {
    // Scale rather than swap models: three distinct silhouettes from one mesh, and no
    // per-weapon assets to keep in sync.
    switch (weapon) {
      case 'rifle':
        this.viewModel.scale.set(1, 1, 1);
        this.viewModel.position.set(0.17, -0.16, -0.28);
        break;
      case 'pistol':
        this.viewModel.scale.set(0.8, 0.8, 0.55);
        this.viewModel.position.set(0.14, -0.15, -0.22);
        break;
      case 'knife':
        this.viewModel.scale.set(0.4, 0.5, 0.35);
        this.viewModel.position.set(0.15, -0.14, -0.2);
        break;
    }
  }

  /** Called every frame: weapon bob, sway and recoil recovery. */
  updateViewModel(dt: number, speed: number, cfg: GameConfig, grounded: boolean): void {
    this.bobPhase += dt * Math.min(speed, cfg.sprintSpeed) * 1.9;
    const bobAmount = grounded ? Math.min(speed / cfg.sprintSpeed, 1) * 0.012 : 0.004;
    const bobX = Math.cos(this.bobPhase) * bobAmount;
    const bobY = Math.abs(Math.sin(this.bobPhase)) * bobAmount;

    this.viewRecoil *= Math.exp(-14 * dt);
    this.viewSway.multiplyScalar(Math.exp(-10 * dt));

    const base = this.viewModel.position;
    base.x = 0.17 * this.viewModel.scale.x + bobX + this.viewSway.x;
    base.y = -0.16 + bobY + this.viewSway.y;
    base.z = -0.28 + this.viewRecoil * 0.5;
    this.viewModel.rotation.x = this.viewRecoil * 2.2;
  }

  /** Nudge the view model when the camera turns, so the gun lags behind slightly. */
  addSway(dYaw: number, dPitch: number): void {
    this.viewSway.x += dYaw * 0.35;
    this.viewSway.y += dPitch * 0.35;
    this.viewSway.x = THREE.MathUtils.clamp(this.viewSway.x, -0.05, 0.05);
    this.viewSway.y = THREE.MathUtils.clamp(this.viewSway.y, -0.05, 0.05);
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
    this.muzzleLight.intensity = 22 * strength;
    this.muzzleLife = 0.05;
    this.viewRecoil += 0.035 * strength;
  }

  impact(at: THREE.Vector3, normal: THREE.Vector3, material: string): void {
    const slot = this.impacts[this.impactIndex];
    this.impactIndex = (this.impactIndex + 1) % this.impacts.length;

    const positions = slot.points.geometry.getAttribute('position') as THREE.BufferAttribute;
    const count = positions.count;
    for (let i = 0; i < count; i++) {
      positions.setXYZ(i, at.x, at.y, at.z);
      // Spray roughly along the surface normal with plenty of scatter.
      slot.velocities[i * 3] = normal.x * 2 + (Math.random() - 0.5) * 3;
      slot.velocities[i * 3 + 1] = normal.y * 2 + Math.random() * 2.5;
      slot.velocities[i * 3 + 2] = normal.z * 2 + (Math.random() - 0.5) * 3;
    }
    positions.needsUpdate = true;

    const mat = slot.points.material as THREE.PointsMaterial;
    mat.color.set(material === 'metal' ? 0xbfe4ff : material === 'crate' ? 0xd9a86a : 0xffcf8a);
    mat.opacity = 1;
    slot.points.visible = true;
    slot.life = IMPACT_LIFE;
  }

  bloodPuff(at: THREE.Vector3): void {
    this.impact(at, new THREE.Vector3(0, 1, 0), 'blood');
    const slot = this.impacts[(this.impactIndex - 1 + this.impacts.length) % this.impacts.length];
    (slot.points.material as THREE.PointsMaterial).color.set(0xc8203a);
  }

  // ── per-frame ───────────────────────────────────────────────────────────────

  update(dt: number): void {
    for (const t of this.tracers) {
      if (t.life <= 0) continue;
      t.life -= dt;
      const mat = t.mesh.material as THREE.MeshBasicMaterial;
      mat.opacity = Math.max(0, (t.life / TRACER_LIFE) * 0.9);
      if (t.life <= 0) t.mesh.visible = false;
    }

    for (const imp of this.impacts) {
      if (imp.life <= 0) continue;
      imp.life -= dt;
      const positions = imp.points.geometry.getAttribute('position') as THREE.BufferAttribute;
      const arr = positions.array as Float32Array;
      for (let i = 0; i < positions.count; i++) {
        const o = i * 3;
        imp.velocities[o + 1] -= 9.8 * dt;
        arr[o] += imp.velocities[o] * dt;
        arr[o + 1] += imp.velocities[o + 1] * dt;
        arr[o + 2] += imp.velocities[o + 2] * dt;
      }
      positions.needsUpdate = true;
      (imp.points.material as THREE.PointsMaterial).opacity = Math.max(0, imp.life / IMPACT_LIFE);
      if (imp.life <= 0) imp.points.visible = false;
    }

    if (this.muzzleLife > 0) {
      this.muzzleLife -= dt;
      this.muzzleLight.intensity *= Math.exp(-30 * dt);
      if (this.muzzleLife <= 0) this.muzzleLight.intensity = 0;
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
    this.camera.remove(this.viewModel);
    this.scene.remove(this.muzzleLight);
  }
}
