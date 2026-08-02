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
import { surfaceTexture, TEXTURE_SCALE } from './textures';

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

// Gradient plus a sun disc and its halo. Doing this in a shader instead of with a
// skybox texture keeps it resolution-independent and costs one full-screen-ish pass.
const SKY_FRAG = `
uniform vec3 top;
uniform vec3 bottom;
uniform vec3 sunColor;
uniform vec3 sunDir;
varying vec3 vWorld;

void main() {
  vec3 dir = normalize(vWorld);
  float h = dir.y * 0.5 + 0.5;

  vec3 sky = mix(bottom, top, smoothstep(0.30, 0.78, h));

  // Warm the horizon slightly; a hard gradient reads as a painted backdrop.
  float horizon = 1.0 - smoothstep(0.0, 0.22, abs(dir.y));
  sky = mix(sky, sky * 1.18 + vec3(0.05, 0.035, 0.02), horizon * 0.55);

  float d = max(dot(dir, normalize(sunDir)), 0.0);
  float disc = smoothstep(0.9975, 0.9992, d);
  float halo = pow(d, 220.0) * 0.55 + pow(d, 18.0) * 0.12;
  sky += sunColor * (disc * 1.6 + halo);

  gl_FragColor = vec4(sky, 1.0);
}`;

/** Direction the sun sits in. Shared by the sky shader and the shadow-casting light. */
export const SUN_DIRECTION = new THREE.Vector3(-0.45, 0.72, 0.35).normalize();

/**
 * Build the level mesh.
 *
 * One merged geometry per material: the whole map is about five draw calls regardless of
 * brush count, which is what keeps this comfortable on integrated graphics.
 *
 * UVs are computed here on the CPU from each face's world extent rather than coming from
 * a unit cube. That is the difference between textures that stretch grotesquely across a
 * 56-metre floor and textures that tile at a consistent real-world scale on every surface.
 * It also avoids patching three's shaders, which would be the other way to do it and is
 * far easier to get subtly wrong.
 */
export function buildLevel(map: MapData, anisotropy = 4): THREE.Group {
  const group = new THREE.Group();
  group.name = 'level';

  const byMaterial = new Map<string, MapBox[]>();
  for (const b of map.boxes) {
    const key = b.m ?? 'concrete';
    const list = byMaterial.get(key);
    if (list) list.push(b);
    else byMaterial.set(key, [b]);
  }

  for (const [name, boxes] of byMaterial) {
    const def = map.materials?.[name] ?? { color: '#808080', roughness: 0.9, metalness: 0.05 };
    const scale = 1 / (TEXTURE_SCALE[name] ?? 2);

    const geometry = buildBoxesGeometry(boxes, scale);
    // `color` tints, `map` adds detail. The map is deliberately near-white so these two
    // multiply to roughly the colour the level designer chose, rather than to its square.
    const material = new THREE.MeshStandardMaterial({
      color: new THREE.Color(def.color),
      roughness: def.roughness,
      metalness: def.metalness,
      map: surfaceTexture(name, anisotropy),
      vertexColors: true,
    });

    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = `level:${name}`;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    group.add(mesh);
  }

  return group;
}

/** Deterministic 0..1 hash, used to tint each brush slightly differently. */
function hash3(x: number, y: number, z: number): number {
  const n = Math.sin(x * 12.9898 + y * 78.233 + z * 37.719) * 43758.5453;
  return n - Math.floor(n);
}

/**
 * Merge axis-aligned boxes into one geometry with world-scaled UVs and per-brush tint.
 *
 * The tint is what stops a wall of twenty identical crates looking like a repeating
 * texture — it's ±6% brightness, invisible as an effect but very visible in its absence.
 */
function buildBoxesGeometry(boxes: MapBox[], uvScale: number): THREE.BufferGeometry {
  const faceCount = boxes.length * 6;
  const positions = new Float32Array(faceCount * 4 * 3);
  const normals = new Float32Array(faceCount * 4 * 3);
  const uvs = new Float32Array(faceCount * 4 * 2);
  const colors = new Float32Array(faceCount * 4 * 3);
  const indices = new Uint32Array(faceCount * 6);

  let v = 0; // vertex counter
  let i = 0; // index counter

  const pushFace = (
    corners: number[][],
    normal: number[],
    uvOf: (p: number[]) => number[],
    tint: number,
  ) => {
    const base = v;
    for (const p of corners) {
      positions[v * 3] = p[0];
      positions[v * 3 + 1] = p[1];
      positions[v * 3 + 2] = p[2];
      normals[v * 3] = normal[0];
      normals[v * 3 + 1] = normal[1];
      normals[v * 3 + 2] = normal[2];
      const uv = uvOf(p);
      uvs[v * 2] = uv[0] * uvScale;
      uvs[v * 2 + 1] = uv[1] * uvScale;
      colors[v * 3] = tint;
      colors[v * 3 + 1] = tint;
      colors[v * 3 + 2] = tint;
      v++;
    }
    indices[i++] = base;
    indices[i++] = base + 1;
    indices[i++] = base + 2;
    indices[i++] = base;
    indices[i++] = base + 2;
    indices[i++] = base + 3;
  };

  for (const b of boxes) {
    const ax = b.p[0] - b.s[0] / 2;
    const ay = b.p[1] - b.s[1] / 2;
    const az = b.p[2] - b.s[2] / 2;
    const bx = b.p[0] + b.s[0] / 2;
    const by = b.p[1] + b.s[1] / 2;
    const bz = b.p[2] + b.s[2] / 2;

    const tint = 0.94 + hash3(b.p[0], b.p[1], b.p[2]) * 0.12;

    // Corner order is counter-clockwise seen from outside, so backface culling keeps the
    // inside of every brush invisible.
    pushFace(
      [
        [bx, ay, bz],
        [bx, ay, az],
        [bx, by, az],
        [bx, by, bz],
      ],
      [1, 0, 0],
      (p) => [p[2], p[1]],
      tint,
    );
    pushFace(
      [
        [ax, ay, az],
        [ax, ay, bz],
        [ax, by, bz],
        [ax, by, az],
      ],
      [-1, 0, 0],
      (p) => [p[2], p[1]],
      tint,
    );
    pushFace(
      [
        [ax, by, bz],
        [bx, by, bz],
        [bx, by, az],
        [ax, by, az],
      ],
      [0, 1, 0],
      (p) => [p[0], p[2]],
      tint,
    );
    pushFace(
      [
        [ax, ay, az],
        [bx, ay, az],
        [bx, ay, bz],
        [ax, ay, bz],
      ],
      [0, -1, 0],
      (p) => [p[0], p[2]],
      tint * 0.82, // undersides never catch the sun; darken them so they read as solid
    );
    pushFace(
      [
        [ax, ay, bz],
        [bx, ay, bz],
        [bx, by, bz],
        [ax, by, bz],
      ],
      [0, 0, 1],
      (p) => [p[0], p[1]],
      tint,
    );
    pushFace(
      [
        [bx, ay, az],
        [ax, ay, az],
        [ax, by, az],
        [bx, by, az],
      ],
      [0, 0, -1],
      (p) => [p[0], p[1]],
      tint,
    );
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geometry.setAttribute('uv', new THREE.BufferAttribute(uvs, 2));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));
  geometry.computeBoundingSphere();
  return geometry;
}

export function buildSky(map: MapData): THREE.Mesh {
  const [bottom, top] = map.sky ?? ['#1b2430', '#59657a'];
  const geo = new THREE.SphereGeometry(400, 32, 20);
  const mat = new THREE.ShaderMaterial({
    uniforms: {
      top: { value: new THREE.Color(top) },
      bottom: { value: new THREE.Color(bottom) },
      sunColor: { value: new THREE.Color('#ffdca8') },
      sunDir: { value: SUN_DIRECTION.clone() },
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
  mesh.renderOrder = -1;
  return mesh;
}

export function buildLights(map: MapData, quality: 'low' | 'high' = 'high'): THREE.Group {
  const group = new THREE.Group();
  group.name = 'lights';

  // A hemisphere light instead of flat ambient: sky colour from above, ground bounce from
  // below. Costs the same and immediately makes everything look less like plastic.
  //
  // These are set generously on purpose. There is no global illumination here, so
  // anything the sun cannot reach — inside the building, behind every crate — is lit by
  // these two terms alone. Too low and half the map becomes a place players simply cannot
  // see into, which is a gameplay problem long before it is an aesthetic one.
  const [skyLow, skyHigh] = map.sky ?? ['#8fa3bb', '#c7d8ec'];
  group.add(new THREE.HemisphereLight(new THREE.Color(skyHigh), new THREE.Color(skyLow), 2.0));
  group.add(new THREE.AmbientLight(new THREE.Color(map.ambient ?? '#93a1b5'), 1.1));

  const sun = new THREE.DirectionalLight(0xfff0d6, 2.6);
  sun.position.copy(SUN_DIRECTION).multiplyScalar(90);
  sun.castShadow = true;

  // Fit the shadow camera to the map. Too loose and the shadows turn to mush; too tight
  // and geometry outside the box stops casting.
  const b = map.bounds;
  const half = Math.max(b[3] - b[0], b[5] - b[2]) * 0.62;
  const cam = sun.shadow.camera as THREE.OrthographicCamera;
  cam.left = -half;
  cam.right = half;
  cam.top = half;
  cam.bottom = -half;
  cam.near = 10;
  cam.far = 260;
  cam.updateProjectionMatrix();

  const shadowSize = quality === 'high' ? 2048 : 1024;
  sun.shadow.mapSize.set(shadowSize, shadowSize);
  // normalBias fixes shadow acne on the large flat faces this map is made of; a plain
  // depth bias big enough to do the same job would detach shadows from their casters.
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.035;
  group.add(sun);
  group.add(sun.target);

  // Fill from the opposite side so the shadow side of a player is still readable — the
  // difference between "in shadow" and "a silhouette you cannot identify".
  const fill = new THREE.DirectionalLight(0xbcd4ff, 0.85);
  fill.position.set(40, 30, -30);
  group.add(fill);

  for (const l of map.lights ?? []) {
    const light = new THREE.PointLight(
      new THREE.Color(l.color),
      l.intensity * 22,
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

/**
 * Renderer setup that has to happen once.
 *
 * Tone mapping choice matters more than it looks. ACES has a filmic shoulder *and* a toe
 * that crushes the low end — great for cinematic renders, wrong for a shooter where the
 * darkest 20% of the image is where enemies hide. `NeutralToneMapping` keeps highlights
 * from clipping without eating the shadows, so it is the better fit here.
 *
 * `exposure` is user-controlled (the brightness slider): monitors vary enormously, and no
 * single value is right for a bright laptop panel and a dim office display both.
 */
export function configureRenderer(
  renderer: THREE.WebGLRenderer,
  quality: 'low' | 'high' = 'high',
  exposure = 1.25,
): void {
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping;
  renderer.toneMappingExposure = exposure;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = quality === 'high' ? THREE.PCFSoftShadowMap : THREE.PCFShadowMap;
}
