// path: frontend/src/textures.ts
//
// Every texture in the game is drawn into a canvas at load time. Nothing is downloaded.
//
// This is what makes the map stop looking like untextured grey boxes without adding a
// single binary asset to the repository — which was a hard constraint from the start and
// is also why the whole client is still under 100 kB before three.js.
//
// The generators are seeded, so a given surface looks the same on every machine and every
// reload. That matters more than it sounds: a wall whose grain changes between rounds is
// distracting, and non-deterministic textures make screenshots useless for bug reports.
//
// ── Why everything here is nearly white ──────────────────────────────────────────────
// These are *detail* maps, not colour maps. Three.js multiplies `material.map` by
// `material.color`, so the surface colour comes from the map JSON and the texture only
// adds variation around it. The first version of this file painted the base colour into
// the canvas as well, which meant every surface got its colour applied twice — a mid-grey
// wall at 0.42 came out at 0.18 and the whole level looked like it was lit by a candle.
//
// The rule: keep the mean luminance of every generator near NEUTRAL, and express detail as
// small deviations around it.

import * as THREE from 'three';

const SIZE = 256;

/**
 * Mean brightness every generator aims for.
 *
 * Slightly under white so there is headroom for highlights without clipping. Multiplying
 * the map colour by ~0.9 is a deliberate, uniform, *predictable* dimming — unlike the
 * accidental squaring it replaces.
 */
const NEUTRAL = '#e6e6e6';

/** Small deterministic PRNG — Mulberry32. Same seed, same texture, everywhere. */
function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function canvas(): { c: HTMLCanvasElement; ctx: CanvasRenderingContext2D } {
  const c = document.createElement('canvas');
  c.width = SIZE;
  c.height = SIZE;
  return { c, ctx: c.getContext('2d')! };
}

function finish(c: HTMLCanvasElement, anisotropy: number): THREE.Texture {
  const tex = new THREE.CanvasTexture(c);
  tex.wrapS = THREE.RepeatWrapping;
  tex.wrapT = THREE.RepeatWrapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = anisotropy;
  // Mipmaps matter here: without them, tiled detail at a distance turns into a shimmering
  // mess the moment the player turns, which reads as "bad graphics" more than low
  // resolution ever does.
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  return tex;
}

/** Speckled noise, used as a base layer by most of the surfaces. */
function speckle(
  ctx: CanvasRenderingContext2D,
  rand: () => number,
  count: number,
  alpha: number,
  maxRadius: number,
): void {
  for (let i = 0; i < count; i++) {
    const x = rand() * SIZE;
    const y = rand() * SIZE;
    const r = rand() * maxRadius + 0.4;
    const shade = Math.floor(rand() * 255);
    ctx.fillStyle = `rgba(${shade},${shade},${shade},${alpha})`;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** Rough concrete: fine grain, a few stains, occasional hairline cracks. */
function concrete(seed: number): HTMLCanvasElement {
  const { c, ctx } = canvas();
  const rand = rng(seed);

  ctx.fillStyle = NEUTRAL;
  ctx.fillRect(0, 0, SIZE, SIZE);
  speckle(ctx, rand, 2600, 0.035, 2.2);

  // Broad blotches so the surface reads as uneven at a distance, not just noisy up close.
  // Balanced light and dark, so the mean stays put.
  for (let i = 0; i < 14; i++) {
    const x = rand() * SIZE;
    const y = rand() * SIZE;
    const r = 18 + rand() * 46;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    const dark = rand() > 0.5;
    g.addColorStop(0, dark ? 'rgba(0,0,0,0.09)' : 'rgba(255,255,255,0.12)');
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }

  ctx.strokeStyle = 'rgba(0,0,0,0.13)';
  ctx.lineWidth = 1;
  for (let i = 0; i < 5; i++) {
    ctx.beginPath();
    let x = rand() * SIZE;
    let y = rand() * SIZE;
    ctx.moveTo(x, y);
    for (let s = 0; s < 7; s++) {
      x += (rand() - 0.5) * 44;
      y += (rand() - 0.5) * 44;
      ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  return c;
}

/** Painted breeze block: concrete plus a mortar grid. */
function blockWall(seed: number): HTMLCanvasElement {
  const c = concrete(seed);
  const ctx = c.getContext('2d')!;
  const rand = rng(seed + 99);

  const rows = 4;
  const h = SIZE / rows;
  // Mortar reads as a groove from the contrast between the dark line and the highlight
  // just below it, so the line itself doesn't need to be anywhere near black.
  ctx.strokeStyle = 'rgba(0,0,0,0.22)';
  ctx.lineWidth = 2;
  for (let r = 0; r <= rows; r++) {
    const y = r * h;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(SIZE, y);
    ctx.stroke();

    // Offset every other course, or it reads as tiles rather than blocks.
    const offset = r % 2 === 0 ? 0 : SIZE / 4;
    for (let k = 0; k < 2; k++) {
      const x = offset + k * (SIZE / 2);
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.lineTo(x, y + h);
      ctx.stroke();
    }
  }

  // A highlight under each mortar line fakes a lip catching the light.
  ctx.strokeStyle = 'rgba(255,255,255,0.22)';
  ctx.lineWidth = 1;
  for (let r = 0; r <= rows; r++) {
    const y = r * h + 2;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(SIZE, y);
    ctx.stroke();
  }
  speckle(ctx, rand, 400, 0.03, 1.6);
  return c;
}

/** Scuffed floor: concrete with a faint expansion-joint grid and drag marks. */
function floorSlab(seed: number): HTMLCanvasElement {
  const c = concrete(seed);
  const ctx = c.getContext('2d')!;
  const rand = rng(seed + 7);

  ctx.strokeStyle = 'rgba(0,0,0,0.18)';
  ctx.lineWidth = 3;
  ctx.strokeRect(0, 0, SIZE, SIZE);

  ctx.strokeStyle = 'rgba(255,255,255,0.09)';
  ctx.lineWidth = 6;
  for (let i = 0; i < 9; i++) {
    ctx.beginPath();
    const y = rand() * SIZE;
    ctx.moveTo(0, y);
    ctx.bezierCurveTo(SIZE * 0.3, y + (rand() - 0.5) * 26, SIZE * 0.6, y + (rand() - 0.5) * 26, SIZE, y);
    ctx.stroke();
  }
  return c;
}

/** Wooden crate: planks, grain, nails at the corners. */
function crateWood(seed: number): HTMLCanvasElement {
  const { c, ctx } = canvas();
  const rand = rng(seed);

  ctx.fillStyle = NEUTRAL;
  ctx.fillRect(0, 0, SIZE, SIZE);

  const planks = 5;
  const h = SIZE / planks;
  for (let p = 0; p < planks; p++) {
    const y = p * h;
    // Each plank gets its own tone so the crate doesn't look like printed wallpaper.
    // Symmetric around zero, so the average plank is exactly NEUTRAL.
    const shade = (rand() - 0.5) * 0.22;
    ctx.fillStyle = shade < 0
      ? `rgba(0,0,0,${-shade})`
      : `rgba(255,255,255,${shade})`;
    ctx.fillRect(0, y, SIZE, h);

    // Grain.
    ctx.strokeStyle = 'rgba(70,45,20,0.16)';
    ctx.lineWidth = 1;
    for (let g = 0; g < 9; g++) {
      const gy = y + 2 + rand() * (h - 4);
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.bezierCurveTo(SIZE * 0.35, gy + (rand() - 0.5) * 5, SIZE * 0.7, gy + (rand() - 0.5) * 5, SIZE, gy);
      ctx.stroke();
    }

    // Shadow gap between planks, with a highlight on the lip above it.
    ctx.fillStyle = 'rgba(0,0,0,0.26)';
    ctx.fillRect(0, y + h - 2, SIZE, 2);
    ctx.fillStyle = 'rgba(255,255,255,0.18)';
    ctx.fillRect(0, y, SIZE, 1);
  }

  // Nails.
  ctx.fillStyle = 'rgba(40,40,46,0.5)';
  for (let p = 0; p < planks; p++) {
    for (const x of [8, SIZE - 8]) {
      ctx.beginPath();
      ctx.arc(x, p * h + h / 2, 2.4, 0, Math.PI * 2);
      ctx.fill();
    }
  }
  return c;
}

/** Brushed metal: horizontal streaks, rivets, a little rust. */
function brushedMetal(seed: number): HTMLCanvasElement {
  const { c, ctx } = canvas();
  const rand = rng(seed);

  ctx.fillStyle = NEUTRAL;
  ctx.fillRect(0, 0, SIZE, SIZE);

  ctx.lineWidth = 1;
  for (let i = 0; i < 900; i++) {
    const y = rand() * SIZE;
    const alpha = rand() * 0.06;
    ctx.strokeStyle = rand() > 0.5 ? `rgba(255,255,255,${alpha})` : `rgba(0,0,0,${alpha})`;
    ctx.beginPath();
    ctx.moveTo(rand() * SIZE, y);
    ctx.lineTo(rand() * SIZE, y);
    ctx.stroke();
  }

  // Panel seam plus rivets: gives the eye a sense of scale on large surfaces.
  ctx.strokeStyle = 'rgba(0,0,0,0.26)';
  ctx.lineWidth = 2;
  ctx.strokeRect(4, 4, SIZE - 8, SIZE - 8);
  ctx.fillStyle = 'rgba(255,255,255,0.3)';
  for (let i = 0; i < 8; i++) {
    const t = (i + 0.5) / 8;
    for (const [x, y] of [
      [t * SIZE, 10],
      [t * SIZE, SIZE - 10],
      [10, t * SIZE],
      [SIZE - 10, t * SIZE],
    ]) {
      ctx.beginPath();
      ctx.arc(x, y, 2.2, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  for (let i = 0; i < 5; i++) {
    const x = rand() * SIZE;
    const y = rand() * SIZE;
    const r = 8 + rand() * 22;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, 'rgba(150,88,44,0.2)');
    g.addColorStop(1, 'rgba(150,88,44,0)');
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  }
  return c;
}

const GENERATORS: Record<string, (seed: number) => HTMLCanvasElement> = {
  floor: floorSlab,
  wall: blockWall,
  concrete: concrete,
  crate: crateWood,
  metal: brushedMetal,
};

/** How many world metres one tile of the texture covers, per material. */
export const TEXTURE_SCALE: Record<string, number> = {
  floor: 4,
  wall: 2.5,
  concrete: 2.5,
  crate: 1.2,
  metal: 2,
};

const cache = new Map<string, THREE.Texture>();

/**
 * Detail texture for a material name. Cached, so the twenty crates on the map share one.
 *
 * The surface *colour* comes from `material.color`, fed by the map JSON. This only
 * supplies variation around it, which is why changing a colour in `gen_map.py` recolours
 * the surface without touching this file — and why nothing here darkens the level.
 */
export function surfaceTexture(name: string, anisotropy = 4): THREE.Texture {
  const key = `${name}|${anisotropy}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const gen = GENERATORS[name] ?? concrete;
  // Seed from the name so each material is distinct but stable across reloads.
  let seed = 0;
  for (let i = 0; i < name.length; i++) seed = (seed * 31 + name.charCodeAt(i)) >>> 0;

  const tex = finish(gen(seed + 1), anisotropy);
  cache.set(key, tex);
  return tex;
}

/**
 * Mean brightness of a generated texture, 0..1.
 *
 * Exists so the build can assert that these really are neutral detail maps. A generator
 * that quietly drifts dark is exactly the bug this module already shipped once.
 */
export function meanLuminance(name: string): number {
  const gen = GENERATORS[name] ?? concrete;
  let seed = 0;
  for (let i = 0; i < name.length; i++) seed = (seed * 31 + name.charCodeAt(i)) >>> 0;
  const c = gen(seed + 1);
  const data = c.getContext('2d')!.getImageData(0, 0, SIZE, SIZE).data;
  let total = 0;
  for (let i = 0; i < data.length; i += 4) {
    total += 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
  }
  return total / (SIZE * SIZE) / 255;
}

/** Soft radial blob used for muzzle flashes and impact sparks. */
export function glowTexture(): THREE.Texture {
  const key = 'glow';
  const hit = cache.get(key);
  if (hit) return hit;

  const { c, ctx } = canvas();
  const g = ctx.createRadialGradient(SIZE / 2, SIZE / 2, 0, SIZE / 2, SIZE / 2, SIZE / 2);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.25, 'rgba(255,235,180,0.75)');
  g.addColorStop(0.6, 'rgba(255,170,60,0.22)');
  g.addColorStop(1, 'rgba(255,140,40,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, SIZE, SIZE);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  cache.set(key, tex);
  return tex;
}

/** Irregular star used for the muzzle flash card, so it isn't a perfect circle. */
export function flashTexture(): THREE.Texture {
  const key = 'flash';
  const hit = cache.get(key);
  if (hit) return hit;

  const { c, ctx } = canvas();
  const cx = SIZE / 2;
  const cy = SIZE / 2;
  const rand = rng(4242);

  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, SIZE * 0.28);
  g.addColorStop(0, 'rgba(255,255,240,1)');
  g.addColorStop(0.5, 'rgba(255,214,130,0.65)');
  g.addColorStop(1, 'rgba(255,150,40,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, SIZE, SIZE);

  ctx.globalCompositeOperation = 'lighter';
  for (let i = 0; i < 9; i++) {
    const a = (i / 9) * Math.PI * 2 + rand() * 0.4;
    const len = SIZE * (0.18 + rand() * 0.28);
    const width = 5 + rand() * 12;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(a);
    const lg = ctx.createLinearGradient(0, 0, len, 0);
    lg.addColorStop(0, 'rgba(255,240,200,0.85)');
    lg.addColorStop(1, 'rgba(255,150,40,0)');
    ctx.fillStyle = lg;
    ctx.beginPath();
    ctx.moveTo(0, -width / 2);
    ctx.lineTo(len, 0);
    ctx.lineTo(0, width / 2);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  cache.set(key, tex);
  return tex;
}

/** Bullet hole decal: a dark pit with a bright rim. */
export function bulletHoleTexture(): THREE.Texture {
  const key = 'hole';
  const hit = cache.get(key);
  if (hit) return hit;

  const { c, ctx } = canvas();
  const rand = rng(77);
  const cx = SIZE / 2;
  const cy = SIZE / 2;

  ctx.clearRect(0, 0, SIZE, SIZE);

  // Dust ring first, so the pit draws over it.
  const ring = ctx.createRadialGradient(cx, cy, SIZE * 0.12, cx, cy, SIZE * 0.42);
  ring.addColorStop(0, 'rgba(210,205,195,0.55)');
  ring.addColorStop(1, 'rgba(210,205,195,0)');
  ctx.fillStyle = ring;
  ctx.beginPath();
  ctx.arc(cx, cy, SIZE * 0.42, 0, Math.PI * 2);
  ctx.fill();

  ctx.fillStyle = 'rgba(18,16,14,0.95)';
  ctx.beginPath();
  for (let i = 0; i <= 18; i++) {
    const a = (i / 18) * Math.PI * 2;
    const r = SIZE * (0.13 + rand() * 0.035);
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  ctx.fill();

  ctx.strokeStyle = 'rgba(255,255,255,0.22)';
  ctx.lineWidth = 2;
  ctx.stroke();

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  cache.set(key, tex);
  return tex;
}

export function disposeTextures(): void {
  for (const tex of cache.values()) tex.dispose();
  cache.clear();
}
