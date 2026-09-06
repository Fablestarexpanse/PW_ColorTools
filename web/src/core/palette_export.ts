/**
 * Palette export formats, in the host-free core.
 *
 * `.ase` and `.gpl` are written twice: here for the browser's Export button,
 * and in `pw_color/palette_io.py` for the node's `save_as`. Two hand-written
 * binary/text writers for the same format is exactly the situation
 * ARCHITECTURE.md's duplication registry exists to police, and this pair was
 * missing from it — so a divergence would have shipped a file that opens in
 * Photoshop from one path and not the other.
 *
 * They live here rather than in `nodes/palette.ts` so that
 * `web/tools/palette_export.ts` can load them under plain node and
 * `tests/test_palette_io.py` can compare the bytes against Python's.
 */

export interface ExportSwatch {
  hex: string;
}

function channels(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

/** GIMP palette. Mirrors `_to_gpl` in pw_color/palette_io.py. */
export function toGpl(colors: ExportSwatch[], name: string): string {
  const lines = ['GIMP Palette', `Name: ${name}`, `Columns: ${Math.min(colors.length, 8)}`, '#'];
  for (const sw of colors) {
    const [r, g, b] = channels(sw.hex);
    lines.push(`${String(r).padStart(3)} ${String(g).padStart(3)} ${String(b).padStart(3)}\t${sw.hex}`);
  }
  return lines.join('\n') + '\n';
}

/**
 * Adobe Swatch Exchange, as raw bytes. Mirrors `Palette.to_ase_bytes` in
 * Python — big-endian throughout, UTF-16BE names, RGB float triples.
 */
export function toAseBytes(colors: ExportSwatch[]): Uint8Array<ArrayBuffer> {
  const blocks: ArrayBuffer[] = [];
  for (const sw of colors) {
    const name = sw.hex + '\0';
    const bodyLen = 2 + name.length * 2 + 4 + 12 + 2;
    const buf = new ArrayBuffer(6 + bodyLen);
    const view = new DataView(buf);
    let o = 0;
    view.setUint16(o, 0x0001); o += 2;
    view.setUint32(o, bodyLen); o += 4;
    view.setUint16(o, name.length); o += 2;
    for (let i = 0; i < name.length; i++) { view.setUint16(o, name.charCodeAt(i)); o += 2; }
    for (const ch of 'RGB ') { view.setUint8(o, ch.charCodeAt(0)); o += 1; }
    for (let i = 0; i < 3; i++) {
      view.setFloat32(o, parseInt(sw.hex.slice(1 + i * 2, 3 + i * 2), 16) / 255);
      o += 4;
    }
    view.setUint16(o, 0);
    blocks.push(buf);
  }
  const head = new ArrayBuffer(12);
  const hv = new DataView(head);
  for (let i = 0; i < 4; i++) hv.setUint8(i, 'ASEF'.charCodeAt(i));
  hv.setUint16(4, 1);
  hv.setUint16(6, 0);
  hv.setUint32(8, colors.length);

  const total = 12 + blocks.reduce((n, b) => n + b.byteLength, 0);
  const out = new Uint8Array(new ArrayBuffer(total));
  out.set(new Uint8Array(head), 0);
  let at = 12;
  for (const b of blocks) {
    out.set(new Uint8Array(b), at);
    at += b.byteLength;
  }
  return out;
}
