/**
 * Tonal-response parity harness. Not shipped — used by `tests/test_grain.py`.
 *
 * The grain node draws its tonal response curve in the browser and applies it
 * in torch. That is a second place where the same maths lives in two languages,
 * so it gets the same treatment as the lattice: a harness and a test that fails
 * the moment they disagree.
 *
 * Reads `{"t": [...], "shadows": n, "mids": n, "highlights": n}` on stdin,
 * writes `{"weights": [...]}` on stdout.
 */

export {}; // makes this a module, so top-level await is allowed

declare const process: any;

const EDGE_FALLOFF = 0.04;

function smoothstep(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

function tonalWeight(t: number, shadows: number, mids: number, highlights: number): number {
  const shadow = 1 - smoothstep(0, 0.5, t);
  const highlight = smoothstep(0.5, 1, t);
  const mid = Math.max(0, 1 - shadow - highlight);
  const w = shadow * shadows + mid * mids + highlight * highlights;
  return w * smoothstep(0, EDGE_FALLOFF, t) * smoothstep(0, EDGE_FALLOFF, 1 - t);
}

function read(stream: any): Promise<string> {
  return new Promise((resolve, reject) => {
    let buf = '';
    stream.setEncoding('utf8');
    stream.on('data', (c: string) => (buf += c));
    stream.on('end', () => resolve(buf));
    stream.on('error', reject);
  });
}

const job = JSON.parse(await read(process.stdin));
const weights = job.t.map((t: number) => tonalWeight(t, job.shadows, job.mids, job.highlights));
process.stdout.write(JSON.stringify({ weights }));
