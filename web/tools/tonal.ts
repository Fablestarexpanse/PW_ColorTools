/**
 * Tonal-response parity harness. Not shipped — used by `tests/test_grain.py`.
 *
 * The grain node draws its tonal response curve in the browser and applies it
 * in torch. That is a second place where the same maths lives in two languages,
 * so it gets the same treatment as the lattice: a harness and a test that fails
 * the moment they disagree.
 *
 * This harness *imports* the shipped `tonalWeight` rather than restating it.
 * It used to hold its own copy, which meant the test compared Python against a
 * second transcription of the browser's formula — so a change to the real one
 * would have gone straight past it, which is the only failure the test exists
 * to catch.
 *
 * Reads `{"t": [...], "shadows": n, "midtones": n, "highlights": n}` on stdin,
 * writes `{"weights": [...]}` on stdout.
 */

import { tonalWeight } from '../src/core/tonal.ts';

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

const job = JSON.parse(await read(process.stdin));
const weights = job.t.map((t: number) => tonalWeight(t, job.shadows, job.midtones, job.highlights));
process.stdout.write(JSON.stringify({ weights }));
