/**
 * PW Optics — node wiring.
 *
 * Halation, vignette and chromatic aberration are all spatial, so there is no
 * lattice to sample and nothing the browser can reproduce. The preview shows
 * the node's real output, badged `render only`.
 *
 * Vignette in particular needs the *whole frame* to be judged — its whole
 * character is at the edges — which is why the preview fits by default rather
 * than cropping, and why the 1:1 crop is something you opt into by zooming.
 */

import { app } from '../comfy.ts';
import { attachSpatialPreview } from './spatial_preview.ts';

export function registerOptics(): void {
  app.registerExtension({
    name: 'pw.color.optics',
    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Optics') return;
      attachSpatialPreview(nodeType, { height: 200, minWidth: 340, label: 'Result' });
    },
  });
}
