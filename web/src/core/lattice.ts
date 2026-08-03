/**
 * 3D LUT lattice — mirror of `pw_color/lattice.py`.
 *
 * Conventions, identical to the Python side:
 *  - flat storage is **red fastest** (`for b: for g: for r:`), the `.cube` order
 *  - the input domain is display-referred sRGB in [0,1]
 *
 * `applyPoints` is written as an explicit 8-corner gather to match the torch
 * implementation instruction-for-instruction. The WebGL preview uses the same
 * arithmetic in the fragment shader rather than relying on hardware trilinear
 * filtering, because texture filtering precision is not something we can pin
 * across GPUs — and a preview that differs by a code value from the render is
 * exactly the failure this architecture exists to prevent.
 */

export const DEFAULT_SIZE = 33;
export const FINAL_SIZE = 65;

/**
 * The lattice stores the *unclamped* op stack over this range; the clamp to
 * [0,1] happens after sampling, in `applyImage` here and in `Lattice.apply` in
 * Python. See the comment on `OUT_MIN` in `pw_color/lattice.py` for the
 * measurements — baking the clamp in costs real code values, and a clamp is
 * trivially identical in torch and in a fragment shader.
 *
 * Fixed constants, not fitted to the data: a fitted range would make the
 * quantisation grid depend on floats that agree across JS and torch only to
 * ~1e-6, which could quantise differently on the two sides.
 */
export const OUT_MIN = -0.5;
export const OUT_MAX = 2.0;

export interface LatticeTransport {
  schema: 1;
  size: number;
  encoding: 'u16' | 'f32';
  out_min: number;
  out_max: number;
  /** base64 of little-endian uint16 or float32, red-fastest, 3 per entry. */
  data: string;
}

export class Lattice {
  readonly size: number;
  /** Red-fastest flat storage, length size³ * 3. */
  readonly data: Float32Array;

  constructor(size: number, data: Float32Array) {
    if (data.length !== size * size * size * 3) {
      throw new Error(`lattice data length ${data.length} does not match size ${size}`);
    }
    this.size = size;
    this.data = data;
  }

  static identity(size = DEFAULT_SIZE): Lattice {
    const d = new Float32Array(size * size * size * 3);
    let i = 0;
    for (let bi = 0; bi < size; bi++) {
      for (let gi = 0; gi < size; gi++) {
        for (let ri = 0; ri < size; ri++) {
          d[i++] = ri / (size - 1);
          d[i++] = gi / (size - 1);
          d[i++] = bi / (size - 1);
        }
      }
    }
    return new Lattice(size, d);
  }

  /**
   * Bake a pure per-pixel function. `fn` must not close over mutable state.
   *
   * Two things here are load-bearing for preview/render parity, and both are
   * mirrored in `Lattice.from_fn` on the Python side:
   *
   * - The bake accumulates into a **Float64Array**. Rounding each sample to
   *   float32 before quantising would put us half a float32 ULP away from
   *   torch's float64 bake, which is enough to flip a u16 code wherever an op
   *   runs off the edge of the sRGB gamut and the local gain is large.
   * - The result is **quantised on construction**, so an unquantised lattice
   *   can never reach the preview shader while the renderer holds a quantised
   *   one. Pass `encoding: null` only for `.cube` authoring.
   */
  static fromFn(
    fn: (rgb: [number, number, number]) => [number, number, number],
    size = DEFAULT_SIZE,
    encoding: 'u16' | 'f32' | null = 'u16',
  ): Lattice {
    const d = new Float64Array(size * size * size * 3);
    const s = size - 1;
    let i = 0;
    for (let bi = 0; bi < size; bi++) {
      const b = bi / s;
      for (let gi = 0; gi < size; gi++) {
        const g = gi / s;
        for (let ri = 0; ri < size; ri++) {
          const out = fn([ri / s, g, b]);
          d[i++] = out[0];
          d[i++] = out[1];
          d[i++] = out[2];
        }
      }
    }
    if (encoding === null) return new Lattice(size, Float32Array.from(d));
    return Lattice.fromTransport(quantise(size, d, encoding));
  }

  /** Sample and clamp — the image-output path. Mirrors `Lattice.apply`. */
  applyImage(rgb: [number, number, number]): [number, number, number] {
    const o = this.applyPoints(rgb);
    return [clamp(o[0], 0, 1), clamp(o[1], 0, 1), clamp(o[2], 0, 1)];
  }

  /** Lerp toward identity, so the strength slider is itself LUT-exportable. */
  blendToIdentity(strength: number): Lattice {
    if (strength >= 1) return this;
    const id = Lattice.identity(this.size);
    const d = new Float32Array(this.data.length);
    for (let i = 0; i < d.length; i++) d[i] = id.data[i] + (this.data[i] - id.data[i]) * strength;
    return new Lattice(this.size, d);
  }

  /** Trilinear sample. Must stay identical to `Lattice.apply_points` in Python. */
  applyPoints(rgb: [number, number, number]): [number, number, number] {
    const n = this.size;
    const d = this.data;
    const max = n - 1;

    const cr = clamp(rgb[0], 0, 1) * max;
    const cg = clamp(rgb[1], 0, 1) * max;
    const cb = clamp(rgb[2], 0, 1) * max;

    const r0 = Math.min(Math.floor(cr), n - 2);
    const g0 = Math.min(Math.floor(cg), n - 2);
    const b0 = Math.min(Math.floor(cb), n - 2);
    const fr = cr - r0;
    const fg = cg - g0;
    const fb = cb - b0;

    // Flat index for (r, g, b) in red-fastest order.
    const idx = (r: number, g: number, b: number) => ((b * n + g) * n + r) * 3;

    const out: [number, number, number] = [0, 0, 0];
    for (let c = 0; c < 3; c++) {
      const c000 = d[idx(r0, g0, b0) + c];
      const c100 = d[idx(r0 + 1, g0, b0) + c];
      const c010 = d[idx(r0, g0 + 1, b0) + c];
      const c110 = d[idx(r0 + 1, g0 + 1, b0) + c];
      const c001 = d[idx(r0, g0, b0 + 1) + c];
      const c101 = d[idx(r0 + 1, g0, b0 + 1) + c];
      const c011 = d[idx(r0, g0 + 1, b0 + 1) + c];
      const c111 = d[idx(r0 + 1, g0 + 1, b0 + 1) + c];

      const x00 = c000 + (c100 - c000) * fr;
      const x10 = c010 + (c110 - c010) * fr;
      const x01 = c001 + (c101 - c001) * fr;
      const x11 = c011 + (c111 - c011) * fr;
      const y0 = x00 + (x10 - x00) * fg;
      const y1 = x01 + (x11 - x01) * fg;
      out[c] = y0 + (y1 - y0) * fb;
    }
    return out;
  }

  // -- transport ------------------------------------------------------------

  toTransport(encoding: 'u16' | 'f32' = 'u16'): LatticeTransport {
    return quantise(this.size, this.data, encoding);
  }

  static fromTransport(t: LatticeTransport): Lattice {
    const bytes = base64Decode(t.data);
    const n = t.size;
    const count = n * n * n * 3;
    const out = new Float32Array(count);
    if (t.encoding === 'u16') {
      const lo = t.out_min ?? 0;
      const hi = t.out_max ?? 1;
      const span = hi - lo;
      const q = new Uint16Array(bytes.buffer, bytes.byteOffset, count);
      for (let i = 0; i < count; i++) out[i] = (q[i] / 65535) * span + lo;
    } else {
      out.set(new Float32Array(bytes.buffer, bytes.byteOffset, count));
    }
    return new Lattice(n, out);
  }

  /** Round-trip through the transport encoding, so preview holds exactly the
   *  numbers the renderer will hold. Called before the preview texture upload. */
  quantised(encoding: 'u16' | 'f32' = 'u16'): Lattice {
    return Lattice.fromTransport(this.toTransport(encoding));
  }

  toCube(title = 'PW Color'): string {
    const lines = [`TITLE "${title}"`, `LUT_3D_SIZE ${this.size}`, 'DOMAIN_MIN 0.0 0.0 0.0', 'DOMAIN_MAX 1.0 1.0 1.0', ''];
    for (let i = 0; i < this.data.length; i += 3) {
      lines.push(
        `${clamp(this.data[i], 0, 1).toFixed(6)} ${clamp(this.data[i + 1], 0, 1).toFixed(6)} ${clamp(this.data[i + 2], 0, 1).toFixed(6)}`,
      );
    }
    return lines.join('\n') + '\n';
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

/**
 * Quantise a bake to transport. Takes Float64Array straight from `fromFn` so a
 * float32 rounding step never sits between the maths and the quantiser — that
 * half-ULP is enough to flip a u16 code where an op has high local gain.
 */
function quantise(size: number, d: Float64Array | Float32Array, encoding: 'u16' | 'f32'): LatticeTransport {
  let bytes: Uint8Array;
  if (encoding === 'u16') {
    const span = OUT_MAX - OUT_MIN;
    const q = new Uint16Array(d.length);
    for (let i = 0; i < q.length; i++) {
      // Match Python's `int(v * 65535 + 0.5)` — round half up, not banker's.
      q[i] = Math.min(65535, Math.max(0, Math.floor(clamp((d[i] - OUT_MIN) / span, 0, 1) * 65535 + 0.5)));
    }
    bytes = new Uint8Array(q.buffer);
  } else {
    bytes = new Uint8Array(Float32Array.from(d).buffer);
  }
  return { schema: 1, size, encoding, out_min: OUT_MIN, out_max: OUT_MAX, data: base64Encode(bytes) };
}

// Node has Buffer, browsers have btoa. Kept local so core/ has no imports.
function base64Encode(bytes: Uint8Array): string {
  const g: any = globalThis as any;
  if (g.Buffer) return g.Buffer.from(bytes).toString('base64');
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return g.btoa(s);
}

function base64Decode(text: string): Uint8Array {
  const g: any = globalThis as any;
  if (g.Buffer) {
    const b = g.Buffer.from(text, 'base64');
    return new Uint8Array(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
  }
  const bin = g.atob(text);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
