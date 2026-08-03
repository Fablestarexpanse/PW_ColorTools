/**
 * Per-pixel operations — mirror of `pw_color/ops.py`.
 *
 * Same contract: sRGB-encoded triple in, sRGB-encoded triple out, pure. Only
 * functions with this shape can be baked into a lattice, which is what keeps
 * preview and render identical. Spatial operations (grain, halation, vignette,
 * chromatic aberration) deliberately cannot be expressed here.
 */

import {
  linearToSrgb,
  oklabToOklch,
  oklabToSrgb,
  oklchToOklab,
  srgbToLinear,
  srgbToOklab,
  type Vec3,
} from './colour.ts';
import { Curve, isIdentity, type CurvePoint } from './curve.ts';

export interface LookOp {
  type: string;
  params?: Record<string, any>;
  enabled?: boolean;
  strength?: number;
  blend?: string;
  lut_safe?: boolean;
}

/** Ops that can be baked into a lattice. Anything else is render-only. */
export const LUT_SAFE_OPS = ['exposure', 'contrast', 'saturation', 'curves', 'warmth'] as const;

/** Exposure in stops, in linear light — the only place stops mean anything. */
export function opExposure(rgb: Vec3, stops: number): Vec3 {
  const k = Math.pow(2, stops);
  return [
    linearToSrgb(srgbToLinear(rgb[0]) * k),
    linearToSrgb(srgbToLinear(rgb[1]) * k),
    linearToSrgb(srgbToLinear(rgb[2]) * k),
  ];
}

/** Contrast about a linear pivot; 0.18 middle grey keeps skin where it was. */
export function opContrast(rgb: Vec3, amount: number, pivot = 0.18): Vec3 {
  const k = 1 + amount;
  const f = (v: number) => linearToSrgb(Math.pow(Math.max(srgbToLinear(v), 1e-6) / pivot, k) * pivot);
  return [f(rgb[0]), f(rgb[1]), f(rgb[2])];
}

/** Saturation as a scale on OKLab chroma — hue and lightness held. */
export function opSaturation(rgb: Vec3, amount: number): Vec3 {
  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  const lch = oklabToOklch(lab[0], lab[1], lab[2]);
  const back = oklchToOklab(lch[0], lch[1] * amount, lch[2]);
  return oklabToSrgb(back[0], back[1], back[2]);
}

/** Warm/cool along the OKLab b axis, scaled by L to stay out of deep shadow. */
export function opWarmth(rgb: Vec3, amount: number): Vec3 {
  const lab = srgbToOklab(rgb[0], rgb[1], rgb[2]);
  return oklabToSrgb(lab[0], lab[1], lab[2] + amount * 0.1 * lab[0]);
}

export interface CurvesParams {
  luma?: CurvePoint[];
  r?: CurvePoint[];
  g?: CurvePoint[];
  b?: CurvePoint[];
  preserve_hue?: boolean;
}

/**
 * Cached curve evaluators. Rebuilding tangents for all 35 937 lattice points
 * would be absurd; the caller builds this once per lattice bake.
 */
export class CurvesOp {
  private readonly chan: (Curve | null)[];
  private readonly luma: Curve | null;
  private readonly preserveHue: boolean;

  constructor(params: CurvesParams) {
    const prep = (p?: CurvePoint[]) => (p && p.length >= 2 && !isIdentity(p) ? new Curve(p) : null);
    this.chan = [prep(params.r), prep(params.g), prep(params.b)];
    this.luma = prep(params.luma);
    this.preserveHue = params.preserve_hue !== false;
  }

  apply(rgb: Vec3): Vec3 {
    let out: Vec3 = [rgb[0], rgb[1], rgb[2]];
    for (let i = 0; i < 3; i++) {
      const c = this.chan[i];
      if (c) out[i] = c.at(out[i]);
    }
    if (this.luma) {
      if (this.preserveHue) {
        // Drive OKLab L only. Applying the same curve to R, G and B separately
        // — what every other curve node does — raises R faster than B through
        // the steep part of an S-curve, which *is* a hue shift. This is why
        // their contrast sends skin orange.
        const lab = srgbToOklab(out[0], out[1], out[2]);
        out = oklabToSrgb(this.luma.at(lab[0]), lab[1], lab[2]);
      } else {
        out = [this.luma.at(out[0]), this.luma.at(out[1]), this.luma.at(out[2])];
      }
    }
    return out;
  }
}

type Evaluator = (rgb: Vec3) => Vec3;

/** Fold a list of LOOK ops into a single sample function for a lattice bake. */
export function buildSampleFn(ops: LookOp[]): Evaluator {
  const stages: Evaluator[] = [];
  for (const op of ops) {
    if (op.enabled === false) continue;
    const p = op.params ?? {};
    let fn: Evaluator | null = null;
    switch (op.type) {
      case 'exposure':
        fn = (rgb) => opExposure(rgb, p.stops ?? 0);
        break;
      case 'contrast':
        fn = (rgb) => opContrast(rgb, p.amount ?? 0, p.pivot ?? 0.18);
        break;
      case 'saturation':
        fn = (rgb) => opSaturation(rgb, p.amount ?? 1);
        break;
      case 'warmth':
        fn = (rgb) => opWarmth(rgb, p.amount ?? 0);
        break;
      case 'curves': {
        const c = new CurvesOp(p as CurvesParams);
        fn = (rgb) => c.apply(rgb);
        break;
      }
      default:
        // Unknown or render-only: pass through. LOOK.lut_exportable is what
        // tells the user a .cube export would drop it.
        continue;
    }
    const s = op.strength ?? 1;
    stages.push(
      s >= 1
        ? fn
        : (rgb) => {
            const o = fn!(rgb);
            return [
              rgb[0] + (o[0] - rgb[0]) * s,
              rgb[1] + (o[1] - rgb[1]) * s,
              rgb[2] + (o[2] - rgb[2]) * s,
            ];
          },
    );
  }

  return (rgb: Vec3) => {
    let out = rgb;
    for (const st of stages) out = st(out);
    // Deliberately unclamped. The clamp to [0,1] happens after lattice
    // sampling — see OUT_MIN in core/lattice.ts for why baking it in is
    // expensive.
    return out;
  };
}
