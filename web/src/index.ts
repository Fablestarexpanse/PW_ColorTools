/**
 * Extension entry point. Everything the pack adds to the frontend starts here.
 */

import { app, warnIfUnsupported } from './comfy.ts';
import { PW } from './theme.ts';
import { registerCurves } from './nodes/curves.ts';
import { registerGrain } from './nodes/grain.ts';
import { registerPalette } from './nodes/palette.ts';
import { registerParityProbe } from './nodes/parity.ts';

/**
 * Port colours for our custom types.
 *
 * LiteGraph looks these up by type name at draw time, so setting them once at
 * registration is enough; there is no per-node work. IMAGE and MASK are set
 * too, deliberately — a PW_Look sitting next to a core node should not have two
 * different greens for the same wire.
 */
function registerPortColours(): void {
  const canvas = (app as any).canvas;
  if (!canvas) {
    console.warn('[PW Color] no canvas at setup — port colours not applied');
    return;
  }
  // Frontend 1.4x keeps two maps: one for the wire, one for the socket dot.
  // Setting only the first leaves purple wires ending in grey dots.
  for (const key of ['default_connection_color_byType', 'default_connection_color_byTypeOff']) {
    const map = canvas[key];
    if (!map) continue;
    for (const [type, colour] of Object.entries(PW.port)) map[type] = colour;
  }
}

app.registerExtension({
  name: 'pw.color',
  async setup() {
    warnIfUnsupported();
    registerPortColours();
  },
});

registerCurves();
registerGrain();
registerPalette();
registerParityProbe();
