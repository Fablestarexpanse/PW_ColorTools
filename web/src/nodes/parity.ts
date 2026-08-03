/**
 * Wiring for PW_ParityProbe — the Phase 0 gate, in the running app.
 *
 * Throwaway alongside the node itself. It does the one thing that matters:
 * bakes the LOOK in the browser exactly as the preview shader would, and hands
 * the resulting lattice to Python so the node can compare byte for byte.
 */

import { app, getWidget, type NodeLike } from '../comfy.ts';
import { Lattice, DEFAULT_SIZE, FINAL_SIZE } from '../core/lattice.ts';
import { buildSampleFn } from '../core/ops.ts';

function bake(node: NodeLike): void {
  const lookW = getWidget(node, 'look_json');
  const outW = getWidget(node, 'js_lattice');
  const finalW = getWidget(node, 'final_quality');
  if (!lookW || !outW) return;

  let ops: any[];
  try {
    ops = JSON.parse(String(lookW.value)).ops ?? [];
  } catch {
    outW.value = '';
    return;
  }

  const size = finalW?.value ? FINAL_SIZE : DEFAULT_SIZE;
  const t0 = performance.now();
  // fromFn quantises on construction — this is the same object the preview
  // shader would upload, not a separate "close enough" bake.
  const lattice = Lattice.fromFn(buildSampleFn(ops) as any, size);
  outW.value = JSON.stringify(lattice.toTransport('u16'));
  console.debug(`[PW Color] baked ${size}³ lattice in ${(performance.now() - t0).toFixed(1)}ms`);
}

export function registerParityProbe(): void {
  app.registerExtension({
    name: 'pw.color.parity',
    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_ParityProbe') return;

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const r = onCreated?.apply(this, arguments as any);
        // Re-bake whenever an input the lattice depends on changes. Chaining
        // the widget's own callback rather than replacing it, for the same
        // reason we chain node handlers.
        for (const name of ['look_json', 'final_quality']) {
          const w = getWidget(this, name);
          if (!w) continue;
          const prev = w.callback;
          w.callback = (v: any) => {
            const out = prev?.call(w, v);
            bake(this);
            return out;
          };
        }
        bake(this);
        return r;
      };
    },
  });
}
