// path: frontend/src/weapons3d.ts
//
// Weapon geometry, built from primitives in code.
//
// One builder serves both uses: the first-person view model and the weapon in a remote
// player's hands. That's deliberate — if they were separate, the gun you get shot by
// would eventually stop looking like the gun you carry, and that mismatch is exactly the
// kind of thing nobody notices until a player screenshots it.
//
// Convention for every weapon: the origin sits at the grip, the barrel points down −Z,
// and up is +Y. `MUZZLES` gives the muzzle position in that same local space, so effects
// can attach a flash without knowing anything about the model.

import * as THREE from 'three';
import type { WeaponId } from '@shared/protocol';

export const MUZZLES: Record<WeaponId, THREE.Vector3> = {
  rifle: new THREE.Vector3(0, 0.012, -0.62),
  smg: new THREE.Vector3(0, 0.01, -0.46),
  sniper: new THREE.Vector3(0, 0.015, -0.85),
  shotgun: new THREE.Vector3(0, 0.01, -0.66),
  pistol: new THREE.Vector3(0, 0.02, -0.26),
  knife: new THREE.Vector3(0, 0, -0.2),
};

/** Where the off hand rests, used to place the left arm on remote players. */
export const FOREGRIPS: Record<WeaponId, THREE.Vector3> = {
  rifle: new THREE.Vector3(0, -0.02, -0.34),
  smg: new THREE.Vector3(0, -0.02, -0.24),
  sniper: new THREE.Vector3(0, -0.02, -0.4),
  shotgun: new THREE.Vector3(0, -0.04, -0.36),
  pistol: new THREE.Vector3(0.04, -0.02, -0.08),
  knife: new THREE.Vector3(0, 0, -0.05),
};

interface Palette {
  body: THREE.MeshStandardMaterial;
  dark: THREE.MeshStandardMaterial;
  grip: THREE.MeshStandardMaterial;
  accent: THREE.MeshStandardMaterial;
  glass: THREE.MeshStandardMaterial;
}

let palette: Palette | null = null;

function materials(): Palette {
  if (palette) return palette;
  palette = {
    // Gunmetal: dark, fairly rough, quite metallic. Shiny black plastic is the classic
    // way to make a low-poly gun look like a toy.
    body: new THREE.MeshStandardMaterial({ color: 0x3a3f47, roughness: 0.42, metalness: 0.85 }),
    dark: new THREE.MeshStandardMaterial({ color: 0x1d2025, roughness: 0.55, metalness: 0.7 }),
    grip: new THREE.MeshStandardMaterial({ color: 0x23262b, roughness: 0.9, metalness: 0.08 }),
    accent: new THREE.MeshStandardMaterial({ color: 0x6b4a2a, roughness: 0.75, metalness: 0.05 }),
    glass: new THREE.MeshStandardMaterial({
      color: 0x8fd4ff,
      roughness: 0.12,
      metalness: 0.3,
      emissive: 0x123044,
      emissiveIntensity: 0.6,
    }),
  };
  return palette;
}

function box(
  g: THREE.Group,
  mat: THREE.Material,
  w: number,
  h: number,
  d: number,
  x: number,
  y: number,
  z: number,
  rot?: [number, number, number],
): THREE.Mesh {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  mesh.position.set(x, y, z);
  if (rot) mesh.rotation.set(rot[0], rot[1], rot[2]);
  mesh.castShadow = true;
  g.add(mesh);
  return mesh;
}

function tube(
  g: THREE.Group,
  mat: THREE.Material,
  r: number,
  len: number,
  x: number,
  y: number,
  z: number,
  axis: 'z' | 'y' = 'z',
  segments = 10,
): THREE.Mesh {
  const geo = new THREE.CylinderGeometry(r, r, len, segments);
  if (axis === 'z') geo.rotateX(Math.PI / 2);
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(x, y, z);
  mesh.castShadow = true;
  g.add(mesh);
  return mesh;
}

// ─── individual weapons ───────────────────────────────────────────────────────

function buildRifle(): THREE.Group {
  const g = new THREE.Group();
  const m = materials();

  box(g, m.body, 0.075, 0.11, 0.34, 0, 0.01, -0.06); // receiver
  box(g, m.dark, 0.062, 0.055, 0.26, 0, 0.055, -0.1); // top rail housing
  tube(g, m.dark, 0.016, 0.42, 0, 0.012, -0.4); // barrel
  box(g, m.dark, 0.05, 0.05, 0.2, 0, 0.005, -0.32); // handguard
  tube(g, m.body, 0.024, 0.07, 0, 0.012, -0.615); // muzzle brake

  // Curved magazine: two boxes at slightly different angles reads as a banana mag from
  // any distance a player will actually see it at.
  box(g, m.dark, 0.045, 0.14, 0.075, 0, -0.09, -0.02, [0.18, 0, 0]);
  box(g, m.dark, 0.043, 0.1, 0.07, 0, -0.19, 0.005, [0.42, 0, 0]);

  box(g, m.grip, 0.045, 0.13, 0.06, 0, -0.075, 0.12, [-0.32, 0, 0]); // pistol grip
  box(g, m.dark, 0.05, 0.09, 0.2, 0, 0.015, 0.21); // stock
  box(g, m.grip, 0.055, 0.035, 0.09, 0, 0.06, 0.24); // cheek rest

  // Small red-dot: the lens catching light is most of what sells a gun as modern.
  box(g, m.dark, 0.04, 0.045, 0.09, 0, 0.095, -0.1);
  const lens = new THREE.Mesh(new THREE.CircleGeometry(0.016, 12), m.glass);
  lens.position.set(0, 0.098, -0.055);
  g.add(lens);

  return g;
}

function buildSmg(): THREE.Group {
  const g = new THREE.Group();
  const m = materials();

  box(g, m.body, 0.07, 0.1, 0.24, 0, 0.01, -0.02);
  tube(g, m.dark, 0.014, 0.26, 0, 0.012, -0.28);
  box(g, m.dark, 0.05, 0.05, 0.14, 0, 0.008, -0.2);
  box(g, m.dark, 0.055, 0.03, 0.2, 0, 0.055, -0.05); // top rail

  box(g, m.dark, 0.04, 0.2, 0.06, 0, -0.11, -0.01); // straight mag
  box(g, m.grip, 0.042, 0.12, 0.055, 0, -0.06, 0.09, [-0.3, 0, 0]);
  box(g, m.grip, 0.036, 0.09, 0.045, 0, -0.06, -0.19, [0.12, 0, 0]); // foregrip

  // Folding stock: a thin frame rather than a solid block.
  box(g, m.dark, 0.012, 0.012, 0.18, -0.025, 0.03, 0.17);
  box(g, m.dark, 0.012, 0.012, 0.18, 0.025, 0.03, 0.17);
  box(g, m.dark, 0.07, 0.05, 0.02, 0, 0.03, 0.26);

  return g;
}

function buildSniper(): THREE.Group {
  const g = new THREE.Group();
  const m = materials();

  box(g, m.body, 0.07, 0.1, 0.38, 0, 0.01, -0.1);
  tube(g, m.dark, 0.018, 0.62, 0, 0.014, -0.52); // long heavy barrel
  tube(g, m.body, 0.026, 0.09, 0, 0.014, -0.83);

  // Scope: the defining silhouette of this weapon, so it gets real geometry.
  tube(g, m.dark, 0.035, 0.3, 0, 0.11, -0.14);
  tube(g, m.dark, 0.046, 0.06, 0, 0.11, -0.28); // objective bell
  tube(g, m.dark, 0.022, 0.05, 0, 0.11, 0.0); // eyepiece
  const lens = new THREE.Mesh(new THREE.CircleGeometry(0.04, 16), m.glass);
  lens.position.set(0, 0.11, -0.311);
  g.add(lens);
  box(g, m.dark, 0.02, 0.06, 0.03, 0, 0.06, -0.05); // front ring
  box(g, m.dark, 0.02, 0.06, 0.03, 0, 0.06, -0.22); // rear ring

  box(g, m.dark, 0.016, 0.016, 0.07, 0.05, 0.03, 0.02, [0, 0, -0.5]); // bolt handle
  box(g, m.accent, 0.055, 0.1, 0.26, 0, -0.01, 0.2); // stock
  box(g, m.accent, 0.06, 0.045, 0.11, 0, 0.055, 0.2); // cheek riser
  box(g, m.grip, 0.045, 0.12, 0.06, 0, -0.08, 0.1, [-0.3, 0, 0]);
  box(g, m.dark, 0.04, 0.06, 0.08, 0, -0.07, -0.06); // magazine

  return g;
}

function buildShotgun(): THREE.Group {
  const g = new THREE.Group();
  const m = materials();

  box(g, m.body, 0.07, 0.11, 0.26, 0, 0.01, -0.04);
  tube(g, m.dark, 0.021, 0.5, 0, 0.03, -0.4); // barrel
  tube(g, m.dark, 0.018, 0.42, 0, -0.015, -0.36); // magazine tube under the barrel

  // Pump: sits forward on the tube, and slides when the weapon cycles.
  const pump = tube(g, m.accent, 0.032, 0.14, 0, -0.012, -0.34);
  pump.name = 'pump';

  box(g, m.accent, 0.06, 0.1, 0.28, 0, -0.01, 0.19); // wooden stock
  box(g, m.accent, 0.062, 0.04, 0.1, 0, 0.05, 0.16);
  box(g, m.dark, 0.05, 0.03, 0.09, 0, -0.06, 0.02); // trigger guard
  box(g, m.dark, 0.03, 0.02, 0.02, 0, 0.075, -0.6); // bead sight

  return g;
}

function buildPistol(): THREE.Group {
  const g = new THREE.Group();
  const m = materials();

  const slide = box(g, m.body, 0.045, 0.06, 0.24, 0, 0.03, -0.08);
  slide.name = 'slide';
  box(g, m.dark, 0.042, 0.04, 0.2, 0, -0.012, -0.06); // frame
  box(g, m.grip, 0.042, 0.14, 0.06, 0, -0.095, 0.02, [-0.22, 0, 0]); // grip
  box(g, m.dark, 0.03, 0.02, 0.03, 0, 0.062, -0.17); // front sight
  box(g, m.dark, 0.036, 0.018, 0.025, 0, 0.062, 0.02); // rear sight
  tube(g, m.dark, 0.011, 0.05, 0, 0.026, -0.21, 'z', 8); // barrel tip

  return g;
}

function buildKnife(): THREE.Group {
  const g = new THREE.Group();
  const m = materials();

  // Tapered blade: a scaled box would look like a ruler, so narrow the tip explicitly.
  const blade = new THREE.BoxGeometry(0.022, 0.06, 0.26);
  const pos = blade.getAttribute('position') as THREE.BufferAttribute;
  for (let i = 0; i < pos.count; i++) {
    const z = pos.getZ(i);
    if (z < -0.1) {
      pos.setX(i, pos.getX(i) * 0.25);
      pos.setY(i, pos.getY(i) * 0.35);
    }
  }
  pos.needsUpdate = true;
  blade.computeVertexNormals();

  const bladeMesh = new THREE.Mesh(
    blade,
    new THREE.MeshStandardMaterial({ color: 0xc8d0da, roughness: 0.18, metalness: 0.95 }),
  );
  bladeMesh.position.set(0, 0, -0.15);
  bladeMesh.castShadow = true;
  g.add(bladeMesh);

  box(g, m.dark, 0.05, 0.018, 0.02, 0, 0, -0.02); // guard
  box(g, m.grip, 0.03, 0.035, 0.11, 0, 0, 0.05); // handle
  box(g, m.dark, 0.034, 0.02, 0.02, 0, 0, 0.105); // pommel

  return g;
}

const BUILDERS: Record<WeaponId, () => THREE.Group> = {
  rifle: buildRifle,
  smg: buildSmg,
  sniper: buildSniper,
  shotgun: buildShotgun,
  pistol: buildPistol,
  knife: buildKnife,
};

/**
 * Build a weapon model.
 *
 * Geometry is rebuilt per call rather than cloned from a cache because the view model
 * animates named sub-parts (the pistol slide, the shotgun pump) and sharing those between
 * instances would make every player's gun cycle at once.
 */
export function buildWeaponModel(id: WeaponId): THREE.Group {
  const builder = BUILDERS[id] ?? buildRifle;
  const g = builder();
  g.name = `weapon:${id}`;
  return g;
}

export function muzzleOf(id: WeaponId): THREE.Vector3 {
  return (MUZZLES[id] ?? MUZZLES.rifle).clone();
}

export function foregripOf(id: WeaponId): THREE.Vector3 {
  return (FOREGRIPS[id] ?? FOREGRIPS.rifle).clone();
}

/** Free the shared materials. Geometries belong to the models and go with them. */
export function disposeWeaponMaterials(): void {
  if (!palette) return;
  for (const mat of Object.values(palette)) mat.dispose();
  palette = null;
}
