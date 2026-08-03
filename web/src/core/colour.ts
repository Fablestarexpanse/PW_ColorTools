/**
 * Colour space conversions — the TypeScript mirror of `pw_color/colour.py`.
 *
 * Scalar-per-component rather than vectorised, because the only two callers are
 * (a) building a 33³ lattice, which is 35 937 points and takes under a
 * millisecond, and (b) single swatches. Neither is on a hot path, and readable
 * code that matches the Python line-for-line is worth more here than speed.
 *
 * JS arithmetic is float64; torch is float32. Values therefore differ by ~1e-7
 * between the two. That is below the u16 lattice transport quantisation, so
 * after transport both sides hold identical numbers. See `tests/test_parity.py`.
 */

export type Vec3 = [number, number, number];

const SRGB_LINEAR_CUTOFF = 0.0031308;
const SRGB_ENCODED_CUTOFF = 0.04045;

export function srgbToLinear(x: number): number {
  const s = Math.sign(x);
  const a = Math.abs(x);
  return s * (a <= SRGB_ENCODED_CUTOFF ? a / 12.92 : Math.pow((a + 0.055) / 1.055, 2.4));
}

export function linearToSrgb(x: number): number {
  const s = Math.sign(x);
  const a = Math.abs(x);
  return s * (a <= SRGB_LINEAR_CUTOFF ? a * 12.92 : 1.055 * Math.pow(Math.max(a, 1e-12), 1 / 2.4) - 0.055);
}

/** Signed cube root: LMS can go negative for out-of-gamut colours. */
function cbrt(x: number): number {
  return Math.sign(x) * Math.pow(Math.abs(x), 1 / 3);
}

export function linearToOklab(r: number, g: number, b: number): Vec3 {
  const l = cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

export function oklabToLinear(L: number, a: number, bb: number): Vec3 {
  const l_ = L + 0.3963377774 * a + 0.2158037573 * bb;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * bb;
  const s_ = L - 0.0894841775 * a - 1.291485548 * bb;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

export function srgbToOklab(r: number, g: number, b: number): Vec3 {
  return linearToOklab(srgbToLinear(r), srgbToLinear(g), srgbToLinear(b));
}

export function oklabToSrgb(L: number, a: number, b: number): Vec3 {
  const lin = oklabToLinear(L, a, b);
  return [linearToSrgb(lin[0]), linearToSrgb(lin[1]), linearToSrgb(lin[2])];
}

export function oklabToOklch(L: number, a: number, b: number): Vec3 {
  return [L, Math.sqrt(a * a + b * b), Math.atan2(b, a)];
}

export function oklchToOklab(L: number, c: number, h: number): Vec3 {
  return [L, c * Math.cos(h), c * Math.sin(h)];
}

/** Rec.709 relative luminance of *linear* values. */
export function lumaBt709(r: number, g: number, b: number): number {
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function hexToSrgb(value: string): Vec3 {
  let v = value.trim().replace(/^#/, '');
  if (v.length === 3) v = v.split('').map((c) => c + c).join('');
  if (v.length !== 6) throw new Error(`not a hex colour: ${value}`);
  return [
    parseInt(v.slice(0, 2), 16) / 255,
    parseInt(v.slice(2, 4), 16) / 255,
    parseInt(v.slice(4, 6), 16) / 255,
  ];
}

export function srgbToHex(rgb: Vec3): string {
  const h = rgb.map((c) => {
    const v = Math.round(Math.min(1, Math.max(0, c)) * 255);
    return v.toString(16).padStart(2, '0').toUpperCase();
  });
  return `#${h.join('')}`;
}
