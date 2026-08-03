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
/**
 * ComfyUI's own widgets, which are not ours to lay out around.
 *
 * Frontend 1.4x attaches a `$$canvas-image-preview` widget to any node with an
 * IMAGE output. It is ~700px tall and, on a node that returns no UI images, it
 * reserves all of that as blank space — which `computeSize` dutifully includes,
 * pushing our panels a screen-height down the node.
 */
const INTERNAL_WIDGET = /^\$\$/;

/**
 * Collapse ComfyUI's built-in image preview on nodes that draw their own.
 *
 * Two previews stacked on one node is worse than either alone, and ours has
 * compare, pan, zoom and a true-scale crop. Called at draw time because the
 * widget is attached lazily, after `onNodeCreated` has run.
 */
export function collapseInternalPreview(node: NodeLike): void {
  for (const w of node.widgets ?? []) {
    if (!INTERNAL_WIDGET.test(w.name) || (w as any).__pwCollapsed) continue;
    (w as any).__pwCollapsed = true;
    w.computeSize = () => [0, -4];
    (w as any).computedHeight = 0;
    (w as any).hidden = true;
  }
}

/**
 * The height LiteGraph wants for this node's title, ports and widgets.
 *
 * Measured by walking the widgets rather than trusting `computeSize`, because
 * that total includes ComfyUI's internal image-preview widget. Hidden widgets
 * report a negative height, so counting rows would over-estimate instead.
 */
export function widgetHeight(node: NodeLike): number {
  const widgets = (node.widgets ?? []).filter(
    (w) => w.type !== 'hidden' && !INTERNAL_WIDGET.test(w.name) && !(w as any).hidden,
  );
  // `last_y` is where LiteGraph actually drew the widget, so it accounts for
  // every layout rule the frontend applies without us reimplementing them.
  let bottom = 0;
  for (const w of widgets) {
    const y = (w as any).last_y;
    const h = (w as any).computedHeight ?? (w as any).height ?? PW.metrics.controlHeight;
    if (Number.isFinite(y)) bottom = Math.max(bottom, y + h);
  }
  if (bottom > 0) return bottom;

  // Before the first paint there is no last_y; fall back to computeSize minus
  // whatever the internal widgets claimed.
  const compute = (node as any).computeSize;
  if (typeof compute === 'function') {
    const size = compute.call(node);
    if (Array.isArray(size) && Number.isFinite(size[1])) {
      let internal = 0;
      for (const w of node.widgets ?? []) {
        if (INTERNAL_WIDGET.test(w.name)) internal += (w as any).computedHeight ?? 0;
      }
      return Math.max(40, size[1] - internal);
    }
  }
  return 40 + widgets.length * (PW.metrics.controlHeight + 4);
}

/** Grow a node to fit a custom panel, never shrinking a user's manual resize. */
export function fitPanel(node: NodeLike, panelHeight: number, minWidth: number): void {
  node.size[0] = Math.max(node.size[0], minWidth);
  node.size[1] = Math.max(node.size[1], widgetHeight(node) + panelHeight);
}

/**
 * Enforce the minimum height at draw time.
 *
 * Doing this only at creation is not enough: LiteGraph applies a saved
 * workflow's size *after* `onConfigure` runs, so a node saved before a panel
 * existed comes back too short and clips it. Checking while drawing is
 * self-correcting whatever the ordering, converges in one frame, and costs a
 * comparison.
 *
 * @returns true if the node was resized, so the caller can request a redraw.
 */
export function ensureHeight(node: NodeLike, panelHeight: number, minWidth: number): boolean {
  const needed = widgetHeight(node) + panelHeight;
  const grew = node.size[1] < needed - 0.5 || node.size[0] < minWidth - 0.5;
  if (grew) fitPanel(node, panelHeight, minWidth);
  return grew;
}
