/**
 * PW Look grade ops — mirror of `pw_color/look.py`.
 *
 * Same contract as everything in `core/`: sRGB-encoded triple in, sRGB-encoded
 * triple out, pure, so the whole grade bakes into one lattice and the preview
 * is exact. Parity with the torch implementation is enforced by
 * `tests/test_parity.py`.
 *
 * Glow is absent on purpose — it blurs, so it is not a lattice op and the node
 * badges that section `render only`.
 */

import { oklabToSrgb, srgbToLinear, linearToSrgb, srgbToOklab, type Vec3 } from './colour.ts';

/** OKLab hue angles of the mixer bands. Must match HSL_BANDS in look.py. */
export const HSL_BANDS: [string, number][] = [
  ['red', 0.510228],
  ['orange', 0.924757],
  ['yellow', 1.915835],
  ['green', 2.487012],
  ['aqua', -2.883826],
  ['blue', -1.674608],
  ['purple', -1.153006],
  ['magenta', -0.552163],
];

const BAND_HALF = 0.36;
const TONE_CENTRES = [0.0, 0.33, 0.67, 1.0];

export function smoothstep(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/** Smooth bump peaking at `centre`. C1, because a kink cannot be baked. */
function band(x: number, centre: number, half = BAND_HALF): number {
  return smoothstep(centre - half, centre, x) * (1 - smoothstep(centre, centre + half, x));
}

export interface ToneParams {
  exposure?: number; contrast?: number;
  blacks?: number; shadows?: number; highlights?: number; whites?: number;
}

export function opTone(rgb: Vec3, p: ToneParams): Vec3 {
  const exposure = p.exposure ?? 0, contrast = p.contrast ?? 0;
  let out: Vec3 = [rgb[0], rgb[1], rgb[2]];

  if (exposure !== 0 || contrast !== 0) {
    const k = Math.pow(2, exposure);
    const f = (v: number) => {
      let lin = srgbToLinear(v);
      if (exposure !== 0) lin *= k;
      if (contrast !== 0) lin = Math.pow(Math.max(lin, 1e-6) / 0.18, 1 + contrast) * 0.18;
      return linearToSrgb(lin);
    };
    out = [f(out[0]), f(out[1]), f(out[2])];
  }

  const amounts = [p.blacks ?? 0, p.shadows ?? 0, p.highlights ?? 0, p.whites ?? 0];
  if (amounts.some((a) => a !== 0)) {
    const lab = srgbToOklab(out[0], out[1], out[2]);
    let delta = 0;
    for (let i = 0; i < 4; i++) {
      if (amounts[i] !== 0) delta += amounts[i] * 0.25 * band(lab[0], TONE_CENTRES[i]);
    }
    out = oklabToSrgb(lab[0] + delta, lab[1], lab[2]);
  }
  return out;
}

export interface ColourParams {
  warmth?: number; tint?: number; vibrance?: number; saturation?: number;
}

export function opColour(rgb: Vec3, p: ColourParams): Vec3 {
  const warmth = p.warmth ?? 0, tint = p.tint ?? 0;
  const vibrance = p.vibrance ?? 0, saturation = p.saturation ?? 1;
  if (warmth === 0 && tint === 0 && vibrance === 0 && saturation === 1) return rgb;

  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const l = lab[0];
  let a = lab[1], b = lab[2];
  if (warmth !== 0) b += warmth * 0.1 * l;
  if (tint !== 0) a += tint * 0.1 * l;

  if (vibrance !== 0 || saturation !== 1) {
    const c = Math.sqrt(a * a + b * b);
    let scale = saturation;
    if (vibrance !== 0) {
      const headroom = 1 - Math.min(1, Math.max(0, c / 0.25));
      scale *= 1 + vibrance * headroom;
    }
    if (c > 1e-9) { a *= scale; b *= scale; }
  }
  return oklabToSrgb(l, a, b);
}

/** Signed angular distance wrapped to [-pi, pi]. */
function hueDistance(h: number, centre: number): number {
  const d = h - centre;
  return d - 2 * Math.PI * Math.round(d / (2 * Math.PI));
}

export interface HslBand { hue?: number; sat?: number; lum?: number }
export interface HslParams { bands?: Record<string, HslBand> }

export function opHsl(rgb: Vec3, p: HslParams): Vec3 {
  const bands = p.bands ?? {};
  const active = Object.entries(bands).filter(
    ([, v]) => v && ((v.hue ?? 0) !== 0 || (v.sat ?? 0) !== 0 || (v.lum ?? 0) !== 0),
  );
  if (active.length === 0) return rgb;

  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const l = lab[0], a = lab[1], b = lab[2];
  const c = Math.sqrt(a * a + b * b);
  const h = Math.atan2(b, a);

  const half = Math.PI / HSL_BANDS.length;
  // Gate on chroma: near-neutral pixels have a numerically defined hue that
  // means nothing visually, and tugging at them is what makes skies blotchy.
  const chromaGate = Math.min(1, Math.max(0, c / 0.04));

  let dHue = 0, satScale = 1, dLum = 0;
  for (const [name, centre] of HSL_BANDS) {
    const bandv = bands[name];
    if (!bandv) continue;
    const dist = Math.abs(hueDistance(h, centre));
    const w = (1 - smoothstep(0, half * 1.6, dist)) * chromaGate;
    if (bandv.hue) dHue += w * bandv.hue * (Math.PI / 12);
    if (bandv.sat) satScale *= 1 + w * bandv.sat;
    if (bandv.lum) dLum += w * bandv.lum * 0.15;
  }

  const h2 = h + dHue;
  const c2 = Math.max(0, c * satScale);
  return oklabToSrgb(l + dLum, c2 * Math.cos(h2), c2 * Math.sin(h2));
}

export type Stop = [number, [number, number, number]];
export interface GradientParams { stops?: Stop[]; amount?: number; blend?: string }

export function opGradientMap(rgb: Vec3, p: GradientParams): Vec3 {
  const stops = p.stops ?? [];
  const amount = p.amount ?? 0;
  if (amount <= 0 || stops.length < 2) return rgb;

  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const l = Math.min(1, Math.max(0, lab[0]));

  let i = 0;
  while (i < stops.length - 2 && l >= stops[i + 1][0]) i++;
  const [p0, c0] = stops[i];
  const [p1, c1] = stops[i + 1];
  const t = Math.min(1, Math.max(0, (l - p0) / Math.max(p1 - p0, 1e-9)));
  const mapped: Vec3 = [
    c0[0] + (c1[0] - c0[0]) * t,
    c0[1] + (c1[1] - c0[1]) * t,
    c0[2] + (c1[2] - c0[2]) * t,
  ];

  const mode = p.blend ?? 'normal';
  let blended: Vec3;
  if (mode === 'normal') blended = mapped;
  else if (mode === 'multiply') blended = [rgb[0] * mapped[0], rgb[1] * mapped[1], rgb[2] * mapped[2]];
  else if (mode === 'screen') blended = [0, 1, 2].map(i2 => 1 - (1 - rgb[i2]) * (1 - mapped[i2])) as Vec3;
  else if (mode === 'overlay')
    blended = [0, 1, 2].map(i2 =>
      rgb[i2] <= 0.5 ? 2 * rgb[i2] * mapped[i2] : 1 - 2 * (1 - rgb[i2]) * (1 - mapped[i2])) as Vec3;
  else if (mode === 'soft light')
    blended = [0, 1, 2].map(i2 => {
      const base = rgb[i2], layer = mapped[i2];
      const d = base <= 0.25 ? ((16 * base - 12) * base + 4) * base : Math.sqrt(Math.max(base, 0));
      return layer <= 0.5
        ? base - (1 - 2 * layer) * base * (1 - base)
        : base + (2 * layer - 1) * (d - base);
    }) as Vec3;
  else if (mode === 'colour') {
    // Keep the image's lightness, take the ramp's hue and chroma.
    const rl = srgbToOklab(mapped[0], mapped[1], mapped[2]);
    blended = oklabToSrgb(lab[0], rl[1], rl[2]);
  } else throw new Error(`unknown gradient map blend ${mode}`);

  return [
    rgb[0] + (blended[0] - rgb[0]) * amount,
    rgb[1] + (blended[1] - rgb[1]) * amount,
    rgb[2] + (blended[2] - rgb[2]) * amount,
  ];
}

/** Gradient stops from palette hexes, ordered dark to light. */
export function rampFromPalette(hexes: string[]): Stop[] {
  const entries = hexes.map((hx) => {
    const rgb: Vec3 = [
      parseInt(hx.slice(1, 3), 16) / 255,
      parseInt(hx.slice(3, 5), 16) / 255,
      parseInt(hx.slice(5, 7), 16) / 255,
    ];
    return { l: srgbToOklab(rgb[0], rgb[1], rgb[2])[0], rgb };
  });
  entries.sort((a, b) => a.l - b.l);
  if (entries.length === 1) return [[0, entries[0].rgb], [1, entries[0].rgb]];
  const n = entries.length - 1;
  return entries.map((e, i) => [i / n, e.rgb] as Stop);
}
