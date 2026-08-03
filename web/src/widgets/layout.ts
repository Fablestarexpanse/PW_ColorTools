/**
 * Node sizing helpers.
 *
 * Every node in the pack draws a custom panel *below* ComfyUI's own widgets.
 * The height that panel needs is ours to know; the height the widgets need is
 * LiteGraph's. Guessing the second with a constant works until someone adds an
 * input, at which point the panel silently sits on top of a widget — which is
 * precisely what happened when save/load landed on PW Palette.
 *
 * So: ask LiteGraph, then add our own block.
 */

import { PW } from '../theme.ts';
import type { NodeLike } from '../comfy.ts';

/**
 * The height LiteGraph wants for this node's title, ports and widgets.
 *
 * `computeSize()` accounts for hidden widgets (they report a negative height),
 * so this stays correct for the widgets we replace with our own controls.
 */
export function widgetHeight(node: NodeLike): number {
  const compute = (node as any).computeSize;
  if (typeof compute === 'function') {
    const size = compute.call(node);
    if (Array.isArray(size) && Number.isFinite(size[1])) return size[1];
  }
  // Fallback for a frontend that drops computeSize: a row per visible widget.
  const visible = (node.widgets ?? []).filter((w) => w.type !== 'hidden').length;
  return 40 + visible * (PW.metrics.controlHeight + 4);
}

/** Grow a node to fit a custom panel, never shrinking a user's manual resize. */
export function fitPanel(node: NodeLike, panelHeight: number, minWidth: number): void {
  node.size[0] = Math.max(node.size[0], minWidth);
  node.size[1] = Math.max(node.size[1], widgetHeight(node) + panelHeight);
}
