/**
 * Monotone cubic (Fritsch-Carlson) curve — mirror of `pw_color/curve.py`.
 *
 * See that file for why this is not Catmull-Rom. Short version: Catmull-Rom
 * overshoots and can reverse, which on a tone curve means a brighter input
 * mapping to a darker output. Fritsch-Carlson cannot do that.
 *
 * `CurvePoint` is `[x, y]` rather than `{x, y}` so that the control points
 * serialise into the workflow JSON as compact arrays.
 */

export type CurvePoint = [number, number];

export const IDENTITY_POINTS: CurvePoint[] = [
  [0, 0],
  [1, 1],
];

export function isIdentity(points: CurvePoint[]): boolean {
  if (points.length !== 2) return false;
  const [[x0, y0], [x1, y1]] = points;
  return Math.abs(x0) < 1e-9 && Math.abs(y0) < 1e-9 && Math.abs(x1 - 1) < 1e-9 && Math.abs(y1 - 1) < 1e-9;
}

/**
 * Sort by x and collapse duplicate x, keeping the last. Dragging a point past
 * its neighbour is a normal mid-gesture state, so this is handled here rather
 * than forbidden in the editor.
 */
function sortedUnique(points: CurvePoint[]): { xs: number[]; ys: number[] } {
  const pts = points.map((p) => [p[0], p[1]] as CurvePoint).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const xs: number[] = [];
  const ys: number[] = [];
  for (const [x, y] of pts) {
    if (xs.length && Math.abs(x - xs[xs.length - 1]) < 1e-7) ys[ys.length - 1] = y;
    else {
      xs.push(x);
      ys.push(y);
    }
  }
  if (xs.length < 2) {
    const y = ys.length ? ys[0] : 0;
    return { xs: [0, 1], ys: [y, ys.length ? y : 1] };
  }
  return { xs, ys };
}

export function monotoneTangents(xs: number[], ys: number[]): number[] {
  const n = xs.length;
  const h: number[] = [];
  const delta: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    h.push(xs[i + 1] - xs[i]);
    delta.push((ys[i + 1] - ys[i]) / h[i]);
  }

  const m = new Array<number>(n).fill(0);
  m[0] = delta[0];
  m[n - 1] = delta[n - 2];
  for (let i = 1; i < n - 1; i++) {
    if (delta[i - 1] * delta[i] <= 0) {
      m[i] = 0; // local extremum — the flat tangent is what kills the overshoot
    } else {
      const w1 = 2 * h[i] + h[i - 1];
      const w2 = h[i] + 2 * h[i - 1];
      m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i]);
    }
  }

  // Fritsch-Carlson circle condition — also constrains the endpoint tangents,
  // which the weighted mean above never touches.
  for (let i = 0; i < n - 1; i++) {
    if (delta[i] === 0) {
      m[i] = 0;
      m[i + 1] = 0;
      continue;
    }
    const a = m[i] / delta[i];
    const b = m[i + 1] / delta[i];
    const s = a * a + b * b;
    if (s > 9) {
      const t = 3 / Math.sqrt(s);
      m[i] = t * a * delta[i];
      m[i + 1] = t * b * delta[i];
    }
  }
  return m;
}

/**
 * A prepared curve. Building the tangents once and evaluating many times is
 * what keeps a 35 937-point lattice build off the frame budget.
 */
export class Curve {
  readonly xs: number[];
  readonly ys: number[];
  readonly m: number[];

  constructor(points: CurvePoint[]) {
    const { xs, ys } = sortedUnique(points);
    this.xs = xs;
    this.ys = ys;
    this.m = monotoneTangents(xs, ys);
  }

  /** Evaluate at x, linearly extended past the ends, clamped to [0,1]. */
  at(x: number): number {
    const { xs, ys, m } = this;
    const n = xs.length;
    if (x < xs[0]) return clamp01(ys[0] + (x - xs[0]) * m[0]);
    if (x > xs[n - 1]) return clamp01(ys[n - 1] + (x - xs[n - 1]) * m[n - 1]);

    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (xs[mid] <= x) lo = mid;
      else hi = mid;
    }
    const h = xs[lo + 1] - xs[lo];
    const t = (x - xs[lo]) / h;
    const t2 = t * t;
    const t3 = t2 * t;
    const h00 = 2 * t3 - 3 * t2 + 1;
    const h10 = t3 - 2 * t2 + t;
    const h01 = -2 * t3 + 3 * t2;
    const h11 = t3 - t2;
    return clamp01(h00 * ys[lo] + h10 * h * m[lo] + h01 * ys[lo + 1] + h11 * h * m[lo + 1]);
  }
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}
