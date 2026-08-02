// path: frontend/tools/texture-luminance.ts
//
// Reports how bright the generated textures actually are, and how bright each surface
// ends up once the map colour is multiplied in.
//
// This tool exists because of a real bug. The first version of textures.ts painted the
// material's base colour into the canvas *and* left it on `material.color`, so every
// surface got its colour applied twice: the floor's 0.24 albedo came out at 0.06 and the
// entire level looked like it was lit by a candle. Nothing failed — it just looked wrong,
// and "looks a bit dark" is easy to argue about and hard to act on. A number is not.
//
// The rule it checks: the generators are *detail* maps, so their mean luminance should sit
// near 0.85–0.95. Much lower and they are secretly dimming the level.
//
// Needs a canvas implementation, which is not a dependency of the client:
//
//   cd frontend
//   npm i -D @napi-rs/canvas
//   ./node_modules/.bin/esbuild tools/texture-luminance.ts --bundle --platform=node \
//       --format=cjs --external:@napi-rs/canvas --outfile=lum.cjs
//   node lum.cjs ../assets/maps/alley.json

import { createCanvas } from '@napi-rs/canvas';

// textures.ts reaches for document.createElement('canvas'); give it one.
(globalThis as unknown as { document: unknown }).document = {
  createElement: (tag: string) => (tag === 'canvas' ? createCanvas(256, 256) : {}),
};

import { readFileSync } from 'node:fs';
import { meanLuminance, TEXTURE_SCALE } from '../src/textures';

/** Rec. 709 luminance of a #rrggbb string, in sRGB units (not linearised). */
function hexLuminance(hex: string): number {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

const MIN_TEXTURE_MEAN = 0.8;
const MIN_SURFACE_ALBEDO = 0.3;

function main(): void {
  const mapPath = process.argv[2] ?? '../assets/maps/alley.json';
  const map = JSON.parse(readFileSync(mapPath, 'utf-8'));

  console.log('material     texture   colour   surface');
  const problems: string[] = [];

  for (const name of Object.keys(TEXTURE_SCALE)) {
    const texture = meanLuminance(name);
    const colour = hexLuminance(map.materials?.[name]?.color ?? '#808080');
    const surface = texture * colour;

    console.log(
      `${name.padEnd(12)} ${texture.toFixed(3)}     ${colour.toFixed(3)}    ${surface.toFixed(3)}`,
    );

    if (texture < MIN_TEXTURE_MEAN) {
      problems.push(
        `${name}: texture mean ${texture.toFixed(3)} is below ${MIN_TEXTURE_MEAN} — ` +
          `it is a detail map, it should not be darkening the surface`,
      );
    }
    if (surface < MIN_SURFACE_ALBEDO) {
      problems.push(
        `${name}: final albedo ${surface.toFixed(3)} is below ${MIN_SURFACE_ALBEDO} — ` +
          `players will struggle to see anything against it`,
      );
    }
  }

  if (problems.length) {
    console.log('\nPROBLEMS');
    for (const p of problems) console.log(`  ! ${p}`);
    process.exit(1);
  }
  console.log('\nok — textures are neutral detail maps and surfaces are readable');
}

main();
