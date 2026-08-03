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
 */

import { chainHandler, type NodeLike } from '../comfy.ts';
import { Preview } from '../canvas/preview.ts';
import { BADGE, PW } from '../theme.ts';
import { isComparing, onCompareChange } from '../widgets/compare.ts';
import { headerChip, hit, sectionHeader, type Ctx, type Rect } from '../widgets/draw.ts';
import { resetNode } from '../widgets/reset.ts';
import { collapseInternalPreview, ensureHeight, fitPanel, widgetHeight } from '../widgets/layout.ts';
import { onRunComplete } from '../widgets/run_events.ts';

const M = PW.metrics;
const HEADER_H = 18;

export interface SpatialPreviewOptions {
  /** Panel height in pixels. */
  height: number;
  /** Minimum node width. */
  minWidth: number;
  /** Extra height this node needs below the preview, if any. */
  extra?: (node: NodeLike) => number;
  /** Drawn between the widgets and the preview. */
  drawExtra?: (ctx: Ctx, node: NodeLike, top: number, width: number) => void;
  label?: string;
}

export interface SpatialPreviewHandle {
  preview: Preview;
  previewRect: (node: NodeLike) => Rect;
  extraTop: (node: NodeLike) => number;
}

const handles = new WeakMap<object, SpatialPreviewHandle>();

export function spatialPreviewOf(node: NodeLike): SpatialPreviewHandle | undefined {
  return handles.get(node);
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

    const extraTop = (n: NodeLike) => widgetHeight(n) + M.gapSection;
    const previewRect = (n: NodeLike): Rect => {
      const x = M.padding;
      const w = n.size[0] - M.padding * 2;
      const y = extraTop(n) + (opts.extra?.(n) ?? 0) + HEADER_H + 6;
      return { x, y, w, h: opts.height };
    };
    handles.set(this, { preview, previewRect, extraTop });

    const panelHeight = () => (opts.extra?.(this) ?? 0) + HEADER_H + 6 + opts.height + M.gapSection + M.padding;
    fitPanel(this, panelHeight(), opts.minWidth);

    const refresh = () => {
      void preview.load(this.id, () => this.setDirtyCanvas?.(true, true));
      void preview.loadOutput(this.id, () => this.setDirtyCanvas?.(true, true));
    };
    refresh();

    const stopCompare = onCompareChange(() => this.setDirtyCanvas?.(true, true));
    const stopRun = onRunComplete(refresh);
    const priorRemoved = this.onRemoved;
    this.onRemoved = function (this: NodeLike) {
      stopCompare();
      stopRun();
      priorRemoved?.call(this);
    };

    chainHandler(this, 'onDrawForeground', function (this: NodeLike, ctx: Ctx) {
      if ((this as any).flags?.collapsed) return;
      // A workflow saved before this panel existed restores a size that is too
      // short, and it is applied after onConfigure — so enforce it here.
      collapseInternalPreview(this);
      if (ensureHeight(this, panelHeight(), opts.minWidth)) this.setDirtyCanvas?.(true, true);
      const x = M.padding;
      const w = this.size[0] - M.padding * 2;
      opts.drawExtra?.(ctx, this, extraTop(this), w);
      const pr = previewRect(this);
      const hr = { x, y: pr.y - HEADER_H - 6, w, h: HEADER_H };
      sectionHeader(ctx, opts.label ?? 'Result', hr, BADGE.render);
      headerChip(ctx, hr, 'reset', BADGE.render.label);
      preview.comparing = isComparing();
      preview.draw(ctx, pr);
    });

    chainHandler(this, 'onMouseDown', function (this: NodeLike, e: any, pos: [number, number]) {
      const pr = previewRect(this);
      const hr = { x: M.padding, y: pr.y - HEADER_H - 6, w: this.size[0] - M.padding * 2, h: HEADER_H };
      const ctx2 = (globalThis as any).app?.canvas?.ctx ?? null;
      if (hit(headerChip(ctx2, hr, 'reset', BADGE.render.label), pos[0], pos[1], 3)) {
        resetNode(this);
        return true;
      }
      if (!hit(pr, pos[0], pos[1])) return false;
      preview.onPointerDown(pos[0], pos[1], pr, !!e?.shiftKey, e?.detail === 2);
      this.setDirtyCanvas?.(true, true);
      return true;
    });

    chainHandler(this, 'onMouseMove', function (this: NodeLike, _e: any, pos: [number, number]) {
      if (!preview.onPointerMove(pos[0], pos[1], previewRect(this))) return false;
      this.setDirtyCanvas?.(true, true);
      return true;
    });

    chainHandler(this, 'onMouseUp', function (this: NodeLike) {
      if (!preview.onPointerUp()) return false;
      this.setDirtyCanvas?.(true, true);
      return true;
    });

    chainHandler(this, 'onMouseWheel', function (this: NodeLike, e: any, pos: [number, number]) {
      const pr = previewRect(this);
      if (!hit(pr, pos[0], pos[1])) return false;
      const delta = e?.deltaY ?? -(e?.wheelDelta ?? 0);
      if (!preview.onWheel(pos[0], pos[1], pr, delta)) return false;
      e?.preventDefault?.();
      e?.stopPropagation?.();
      this.setDirtyCanvas?.(true, true);
      return true;
    });

    return r;
  };
}
