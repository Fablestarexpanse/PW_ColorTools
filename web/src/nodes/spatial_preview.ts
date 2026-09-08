/**
 * The preview panel for spatial nodes: PW Grain and PW Optics.
 *
 * Colour nodes bake to a lattice and the browser reproduces them exactly, live,
 * as you drag. Grain, halation, vignette and chromatic aberration cannot work
 * that way — they read pixel neighbourhoods, so there is nothing to bake.
 *
 * The choice is between an approximate shader that updates live but does not
 * match the render, and the node's *real output*, which matches exactly but
 * only updates when the graph runs. This takes the second: the pack's whole
 * premise is that what you see is what you get, and a grain preview that is
 * merely grain-like would undercut that for the sake of a slider feeling
 * livelier.
 *
 * The section is badged `render only` so the difference is stated rather than
 * discovered.
 *
 * Hosted on a DOM widget (`widgets/panel.ts`), so it renders in both node
 * designs. All coordinates below are panel-local.
 */

import type { NodeLike } from '../comfy.ts';
import { Preview } from '../canvas/preview.ts';
import { BADGE, PW } from '../theme.ts';
import { isComparing, onCompareChange } from '../widgets/compare.ts';
import { headerChip, hit, sectionHeader, type Ctx, type Rect } from '../widgets/draw.ts';
import { attachPanel, type Panel } from '../widgets/panel.ts';
import { resetNode } from '../widgets/reset.ts';
import { onRunComplete } from '../widgets/run_events.ts';

const M = PW.metrics;
const HEADER_H = 18;

export interface SpatialPreviewOptions {
  /** Preview height in pixels. */
  height: number;
  /** Minimum node width. */
  minWidth: number;
  /** Extra height this node draws above the preview, if any. */
  extra?: (node: NodeLike) => number;
  /** Drawn above the preview, from `top` (always 0) across `width`. */
  drawExtra?: (ctx: Ctx, node: NodeLike, top: number, width: number) => void;
  label?: string;
}

/**
 * Attach a result preview to a node type.
 *
 * Call from `beforeRegisterNodeDef`. Handles layout, fetching, pan/zoom,
 * compare and teardown; the node only supplies its own extra drawing.
 */
export function attachSpatialPreview(nodeType: any, opts: SpatialPreviewOptions): void {
  const onCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function (this: NodeLike) {
    const r = onCreated?.apply(this, arguments as any);
    const preview = new Preview();

    const extraH = () => opts.extra?.(this) ?? 0;
    const headerRect = (w: number): Rect => ({ x: 0, y: extraH(), w, h: HEADER_H });
    const previewRect = (w: number): Rect => ({ x: 0, y: extraH() + HEADER_H + 6, w, h: opts.height });

    const panel: Panel = attachPanel(this, {
      minWidth: opts.minWidth,
      height: () => extraH() + HEADER_H + 6 + opts.height + M.padding,
      draw: (ctx, rr) => {
        opts.drawExtra?.(ctx, this, 0, rr.w);
        const hr = headerRect(rr.w);
        sectionHeader(ctx, opts.label ?? 'Result', hr, BADGE.render);
        headerChip(ctx, hr, 'reset', BADGE.render.label);
        preview.comparing = isComparing();
        preview.draw(ctx, previewRect(rr.w));
      },
      onPointerDown: (x, y, m) => {
        const hr = headerRect(panel.width);
        if (hit(headerChip(panel.context, hr, 'reset', BADGE.render.label), x, y, 3)) {
          resetNode(this);
          return true;
        }
        const pr = previewRect(panel.width);
        if (!hit(pr, x, y)) return false;
        preview.onPointerDown(x, y, pr, m.shift, m.double);
        return true;
      },
      onPointerMove: (x, y) => preview.onPointerMove(x, y, previewRect(panel.width)),
      onPointerUp: () => preview.onPointerUp(),
      onWheel: (x, y, delta) => {
        const pr = previewRect(panel.width);
        return hit(pr, x, y) && preview.onWheel(x, y, pr, delta);
      },
    });
    const repaint = () => panel.invalidate();

    const refresh = () => {
      void preview.load(this.id, repaint);
      void preview.loadOutput(this.id, repaint);
    };
    // Deferred: the node has no id yet inside onNodeCreated (see curves.ts).
    setTimeout(refresh, 0);

    const stopCompare = onCompareChange(repaint);
    const stopRun = onRunComplete(refresh);
    const priorRemoved = this.onRemoved;
    this.onRemoved = function (this: NodeLike) {
      stopCompare();
      stopRun();
      priorRemoved?.call(this);
    };

    return r;
  };
}
