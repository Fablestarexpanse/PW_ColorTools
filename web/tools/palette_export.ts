/**
 * Palette-export parity harness. Not shipped — used by `tests/test_palette_io.py`.
 *
 * `.ase` and `.gpl` are written once in Python for the node's `save_as` and
 * once in TypeScript for the browser's Export button. Two hand-written writers
 * for the same binary format is precisely what ARCHITECTURE.md's duplication
 * registry is for, and this pair was not in it — so nothing would have caught
 * the two producing different files.
 *
 * Reads `{"hexes": ["#RRGGBB", ...], "name": "..."}` on stdin, writes
 * `{"ase": "<base64>", "gpl": "<text>"}` on stdout.
 */

import { toAseBytes, toGpl } from '../src/core/palette_export.ts';

declare const process: any;
declare const Buffer: any;

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
const colors = job.hexes.map((hex: string) => ({ hex }));
process.stdout.write(
  JSON.stringify({
    ase: Buffer.from(toAseBytes(colors)).toString('base64'),
    gpl: toGpl(colors, job.name),
  }),
);
