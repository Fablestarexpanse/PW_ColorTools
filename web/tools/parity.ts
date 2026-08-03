/**
 * Parity harness. Not shipped — used by `tests/test_parity.py`.
 *
 * Reads a JSON job on stdin, writes a JSON result on stdout. The job carries
 * the LOOK ops and the exact test pixels, so both sides evaluate identical
 * inputs and any difference is genuinely a maths difference, not a sampling one.
 *
 * Run directly: node strips the types (node >= 22.6 with --experimental-strip-types,
 * default from node 23). No build step, deliberately — a parity test that needs
 * a bundler is a parity test people stop running.
 */

import { Lattice, DEFAULT_SIZE } from '../src/core/lattice.ts';
import { buildSampleFn, type LookOp } from '../src/core/ops.ts';

interface Job {
  ops: LookOp[];
  size?: number;
  /** Flat [r,g,b,r,g,b,...] test pixels in sRGB-encoded [0,1]. */
  pixels: number[];
  encoding?: 'u16' | 'f32';
}

declare const process: any;

function read(stream: any): Promise<string> {
  return new Promise((resolve, reject) => {
    let buf = '';
    stream.setEncoding('utf8');
    stream.on('data', (c: string) => (buf += c));
    stream.on('end', () => resolve(buf));
    stream.on('error', reject);
  });
}

const job: Job = JSON.parse(await read(process.stdin));
const size = job.size ?? DEFAULT_SIZE;
const encoding = job.encoding ?? 'u16';

const fn = buildSampleFn(job.ops);

// `fromFn` quantises on construction: this *is* the lattice the preview shader
// samples and the lattice the renderer receives, which is what makes parity a
// property of the code rather than a discipline.
const quantised = Lattice.fromFn(fn as any, size, encoding);
const transport = quantised.toTransport(encoding);

// The unquantised bake, for measuring how far float64 JS and float64 torch are
// apart before quantisation absorbs the difference.
const raw = Lattice.fromFn(fn as any, size, null);

// Direct per-pixel evaluation, for measuring lattice interpolation error.
const direct: number[] = [];
// Lattice-sampled evaluation — this is what the preview shader shows.
const sampled: number[] = [];
const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v);
for (let i = 0; i < job.pixels.length; i += 3) {
  const px: [number, number, number] = [job.pixels[i], job.pixels[i + 1], job.pixels[i + 2]];
  // Both paths clamp at the same place: after evaluation, after sampling.
  const d = fn(px);
  direct.push(clamp01(d[0]), clamp01(d[1]), clamp01(d[2]));
  const s = quantised.applyImage(px);
  sampled.push(s[0], s[1], s[2]);
}

process.stdout.write(
  JSON.stringify({
    transport,
    lattice_raw: Array.from(raw.data),
    direct,
    sampled,
  }),
);
