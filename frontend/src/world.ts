// path: frontend/src/world.ts
//
// Two things are built from the same map JSON the server collides against:
//   1. a CollisionWorld used by client-side prediction, and
//   2. the Three.js scene the player actually looks at.
//
// Keeping them in one file makes it obvious that they must stay in step. If the visual
// mesh and the collision box ever disagree, players shoot at walls that aren't there.

import * as THREE from 'three';
import type { MapData, MapBox } from '@shared/protocol';

export interface AABB {
  minX: number;
  minY: number;
  minZ: number;
  maxX: number;
  maxY: number;
  maxZ: number;
}

export interface RayHit {
  t: number;
  point: THREE.Vector3;
  normal: THREE.Vector3;
  material: string;
}

const CELL_SIZE = 4;
const EPS = 1e-6;

function aabbFromBox(b: MapBox): AABB {
  const [cx, cy, cz] = b.p;
  const [sx, sy, sz] = b.s;
  return {
    minX: cx - sx / 2,
    minY: cy - sy / 2,
    minZ: cz - sz / 2,
    maxX: cx + sx / 2,
    maxY: cy + sy / 2,
    maxZ: cz + sz / 2,
  };
}

/** Mirror of backend/app/game/world.py — same grid broadphase, same slab raycast. */
export class CollisionWorld {
  readonly boxes: AABB[] = [];
  readonly materials: string[] = [];
  readonly bounds: AABB;
  private grid = new Map<number, number[]>();

  constructor(map: MapData) {
    for (const b of map.boxes) {
      this.boxes.push(aabbFromBox(b));
      this.materials.push(b.m ?? 'concrete');
    }
    const [minX, minY, minZ, maxX, maxY, maxZ] = map.bounds;
    this.bounds = { minX, minY, minZ, maxX, maxY, maxZ };
    this.buildGrid();
  }

  // Cells are keyed by a packed integer rather than a string: this is queried several
  // times per frame per axis and string keys showed up in profiles.
  private static key(cx: number, cz: number): number {
    return ((cx + 512) << 12) | (cz + 512);
  }

  private buildGrid(): void {
    for (let i = 0; i < this.boxes.length; i++) {
      const b = this.boxes[i];
      const x0 = Math.floor(b.minX / CELL_SIZE);
      const x1 = Math.floor(b.maxX / CELL_SIZE);
      const z0 = Math.floor(b.minZ / CELL_SIZE);
      const z1 = Math.floor(b.maxZ / CELL_SIZE);
      for (let cx = x0; cx <= x1; cx++) {
        for (let cz = z0; cz <= z1; cz++) {
          const k = CollisionWorld.key(cx, cz);
          let cell = this.grid.get(k);
          if (!cell) {
            cell = [];
            this.grid.set(k, cell);
          }
          cell.push(i);
        }
      }
    }
  }

  /** Indices of brushes overlapping `box`. Reuses a scratch array — do not retain it. */
  private scratch: number[] = [];
  private seen = new Set<number>();

  overlapping(box: AABB): number[] {
    const out = this.scratch;
    out.length = 0;
    this.seen.clear();

    const x0 = Math.floor(box.minX / CELL_SIZE);
    const x1 = Math.floor(box.maxX / CELL_SIZE);
    const z0 = Math.floor(box.minZ / CELL_SIZE);
    const z1 = Math.floor(box.maxZ / CELL_SIZE);

    for (let cx = x0; cx <= x1; cx++) {
      for (let cz = z0; cz <= z1; cz++) {
        const cell = this.grid.get(CollisionWorld.key(cx, cz));
        if (!cell) continue;
        for (const i of cell) {
          if (this.seen.has(i)) continue;
          this.seen.add(i);
          const b = this.boxes[i];
          if (
            box.minX < b.maxX &&
            box.maxX > b.minX &&
            box.minY < b.maxY &&
            box.maxY > b.minY &&
            box.minZ < b.maxZ &&
            box.maxZ > b.minZ
          ) {
            out.push(i);
          }
        }
      }
    }
    return out;
  }

  isFree(box: AABB): boolean {
    return this.overlapping(box).length === 0;
  }

  /** Nearest brush hit, used for local tracers and impact decals before the server replies. */
  raycast(origin: THREE.Vector3, dir: THREE.Vector3, maxDist: number): RayHit | null {
    let bestT = maxDist;
    let bestI = -1;

    // Cheap version of the server's DDA: walk the cells the ray passes through.
    const steps = Math.ceil(maxDist / CELL_SIZE) + 1;
    const visited = new Set<number>();
    for (let s = 0; s <= steps; s++) {
      const d = Math.min(maxDist, s * CELL_SIZE);
      const cx = Math.floor((origin.x + dir.x * d) / CELL_SIZE);
      const cz = Math.floor((origin.z + dir.z * d) / CELL_SIZE);
      for (let ox = -1; ox <= 1; ox++) {
        for (let oz = -1; oz <= 1; oz++) {
          const cell = this.grid.get(CollisionWorld.key(cx + ox, cz + oz));
          if (!cell) continue;
          for (const i of cell) {
            if (visited.has(i)) continue;
            visited.add(i);
            const t = rayAabb(origin, dir, this.boxes[i], bestT);
            if (t >= 0 && t < bestT) {
              bestT = t;
              bestI = i;
            }
          }
        }
      }
    }

    if (bestI < 0) return null;
    const point = new THREE.Vector3(
      origin.x + dir.x * bestT,
      origin.y + dir.y * bestT,
      origin.z + dir.z * bestT,
    );
    return {
      t: bestT,
      point,
      normal: aabbNormalAt(this.boxes[bestI], point),
      material: this.materials[bestI],
    };
  }
}

export function rayAabb(
  origin: THREE.Vector3,
  dir: THREE.Vector3,
  box: AABB,
  maxT: number,
): number {
  let tmin = 0;
  let tmax = maxT;

  const lo = [box.minX, box.minY, box.minZ];
  const hi = [box.maxX, box.maxY, box.maxZ];
  const o = [origin.x, origin.y, origin.z];
  const d = [dir.x, dir.y, dir.z];

  for (let a = 0; a < 3; a++) {
    if (Math.abs(d[a]) > EPS) {
      const inv = 1 / d[a];
      let t1 = (lo[a] - o[a]) * inv;
      let t2 = (hi[a] - o[a]) * inv;
      if (t1 > t2) [t1, t2] = [t2, t1];
      if (t1 > tmin) tmin = t1;
      if (t2 < tmax) tmax = t2;
      if (tmin > tmax) return -1;
    } else if (o[a] < lo[a] || o[a] > hi[a]) {
      return -1;
    }
  }
  return tmin;
}

function aabbNormalAt(box: AABB, p: THREE.Vector3): THREE.Vector3 {
  const cx = (box.minX + box.maxX) / 2;
  const cy = (box.minY + box.maxY) / 2;
  const cz = (box.minZ + box.maxZ) / 2;
  const ex = (box.maxX - box.minX) / 2 || EPS;
  const ey = (box.maxY - box.minY) / 2 || EPS;
  const ez = (box.maxZ - box.minZ) / 2 || EPS;
  const dx = (p.x - cx) / ex;
  const dy = (p.y - cy) / ey;
  const dz = (p.z - cz) / ez;
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  const az = Math.abs(dz);
  if (ax >= ay && ax >= az) return new THREE.Vector3(Math.sign(dx) || 1, 0, 0);
  if (ay >= az) return new THREE.Vector3(0, Math.sign(dy) || 1, 0);
  return new THREE.Vector3(0, 0, Math.sign(dz) || 1);
}

// ─── Rendering ────────────────────────────────────────────────────────────────

const SKY_VERT = `
varying vec3 vWorld;
void main() {
  vWorld = position;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

const SKY_FRAG = `
uniform vec3 top;
uniform vec3 bottom;
varying vec3 vWorld;
void main() {
  float h = normalize(vWorld).y * 0.5 + 0.5;
  gl_FragColor = vec4(mix(bottom, top, smoothstep(0.35, 0.75, h)), 1.0);
}`;

/**
 * Build the level mesh.
 *
 * One InstancedMesh per material means the whole map is a handful of draw calls no matter
 * how many brushes it has — the single biggest thing keeping frame times low on
 * integrated graphics.
 */
export function buildLevel(map: MapData): THREE.Group {
  const group = new THREE.Group();
  group.name = 'level';

  const byMaterial = new Map<string, MapBox[]>();
  for (const b of map.boxes) {
    const key = b.m ?? 'concrete';
    const list = byMaterial.get(key);
    if (list) list.push(b);
    else byMaterial.set(key, [b]);
  }

  const unit = new THREE.BoxGeometry(1, 1, 1);
  const matrix = new THREE.Matrix4();
  const quat = new THREE.Quaternion();
  const pos = new THREE.Vector3();
  const scale = new THREE.Vector3();

  for (const [name, boxes] of byMaterial) {
    const def = map.materials?.[name] ?? { color: '#808080', roughness: 0.9, metalness: 0.05 };
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(def.color),
      roughness: def.roughness,
      metalness: def.metalness,
    });
    const mesh = new THREE.InstancedMesh(unit, material, boxes.length);
    mesh.name = `level:${name}`;
    mesh.castShadow = false;
    mesh.receiveShadow = false;
    boxes.forEach((b, i) => {
      pos.set(b.p[0], b.p[1], b.p[2]);
      scale.set(b.s[0], b.s[1], b.s[2]);
      matrix.compose(pos, quat, scale);
      mesh.setMatrixAt(i, matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
    group.add(mesh);
  }

  return group;
}

export function buildSky(map: MapData): THREE.Mesh {
  const [bottom, top] = map.sky ?? ['#1b2430', '#59657a'];
  const geo = new THREE.SphereGeometry(400, 16, 12);
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      top: { value: new THREE.Color(top) },
      bottom: { value: new THREE.Color(bottom) },
    },
    vertexShader: SKY_VERT,
    fragmentShader: SKY_FRAG,
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = 'sky';
  mesh.frustumCulled = false;
  return mesh;
}

export function buildLights(map: MapData): THREE.Group {
  const group = new THREE.Group();
  group.name = 'lights';
  group.add(new THREE.AmbientLight(new THREE.Color(map.ambient ?? '#404652'), 1.6));

  // One directional light does most of the shaping; the point lights are accents. Shadows
  // are off deliberately — they cost more than they add on a map this flat.
  const sun = new THREE.DirectionalLight(0xffe9c8, 1.15);
  sun.position.set(-40, 70, 30);
  group.add(sun);

  const fill = new THREE.DirectionalLight(0x93b8ff, 0.35);
  fill.position.set(40, 30, -30);
  group.add(fill);

  for (const l of map.lights ?? []) {
    const light = new THREE.PointLight(
      new THREE.Color(l.color),
      l.intensity * 12,
      l.distance,
      2,
    );
    light.position.set(l.p[0], l.p[1], l.p[2]);
    group.add(light);
  }
  return group;
}

export function applyFog(scene: THREE.Scene, map: MapData): void {
  if (!map.fog) return;
  scene.fog = new THREE.Fog(new THREE.Color(map.fog.color), map.fog.near, map.fog.far);
}
