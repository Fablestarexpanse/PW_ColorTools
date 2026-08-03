/**
 * Canvas drawing primitives shared by every widget and canvas in the pack.
 *
 * The rule from the brief: if two nodes draw a slider differently, that is a
 * bug. These functions are how that is made structurally hard rather than a
 * matter of remembering. Nothing below `widgets/` or `canvas/` should call
 * `ctx.fillRect` with a literal colour.
 */

import { PW } from '../theme.ts';

export type Ctx = CanvasRenderingContext2D;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function hit(r: Rect, x: number, y: number, slop = 0): boolean {
  return x >= r.x - slop && x <= r.x + r.w + slop && y >= r.y - slop && y <= r.y + r.h + slop;
}

/** Rounded rectangle path. Uses the native roundRect where the browser has it. */
export function roundRect(ctx: Ctx, r: Rect, radius: number): void {
  ctx.beginPath();
  if (typeof (ctx as any).roundRect === 'function') {
    (ctx as any).roundRect(r.x, r.y, r.w, r.h, radius);
    return;
  }
  const rad = Math.min(radius, r.w / 2, r.h / 2);
  ctx.moveTo(r.x + rad, r.y);
  ctx.arcTo(r.x + r.w, r.y, r.x + r.w, r.y + r.h, rad);
  ctx.arcTo(r.x + r.w, r.y + r.h, r.x, r.y + r.h, rad);
  ctx.arcTo(r.x, r.y + r.h, r.x, r.y, rad);
  ctx.arcTo(r.x, r.y, r.x + r.w, r.y, rad);
  ctx.closePath();
}

export function fillPanel(ctx: Ctx, r: Rect, fill: string, radius: number = PW.metrics.radiusPanel, border?: string): void {
  roundRect(ctx, r, radius);
  ctx.fillStyle = fill;
  ctx.fill();
  if (border) {
    ctx.strokeStyle = border;
    ctx.lineWidth = PW.metrics.border;
    ctx.stroke();
  }
}

/**
 * A hairline. Canvas strokes straddle the path, so a 1px line on an integer
 * coordinate renders as two half-lit pixels. The half-pixel offset is what
 * makes our 0.5–1px borders look like borders rather than smudges.
 */
export function hairline(
  ctx: Ctx,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
  colour: string = PW.color.borderSoft,
): void {
  ctx.strokeStyle = colour;
  ctx.lineWidth = PW.metrics.borderHair;
  ctx.beginPath();
  ctx.moveTo(Math.round(x0) + 0.5, Math.round(y0) + 0.5);
  ctx.lineTo(Math.round(x1) + 0.5, Math.round(y1) + 0.5);
  ctx.stroke();
}

export type Align = 'left' | 'right' | 'center';

export function text(
  ctx: Ctx,
  s: string,
  x: number,
  y: number,
  opts: { font?: string; colour?: string; align?: Align; baseline?: CanvasTextBaseline } = {},
): void {
  ctx.font = opts.font ?? PW.font.body;
  ctx.fillStyle = opts.colour ?? PW.color.textDim;
  ctx.textAlign = opts.align ?? 'left';
  ctx.textBaseline = opts.baseline ?? 'middle';
  ctx.fillText(s, x, y);
}

/**
 * A labelled section header with the LUT / render-only badge on the right.
 *
 * The badge is not decoration. The architecture splits per-pixel operations
 * (bakeable, preview-exact) from spatial ones (render-only, preview
 * approximate), and the brief is explicit that we surface that split rather
 * than pretend it is seamless. Every section that has one shows one.
 */
export function sectionHeader(ctx: Ctx, label: string, r: Rect, badge?: { label: string; fill: string; text: string }): void {
  text(ctx, label, r.x, r.y + r.h / 2, { colour: PW.color.textDim });
  if (!badge) return;
  ctx.font = PW.font.body;
  const w = ctx.measureText(badge.label).width + 12;
  const bx = r.x + r.w - w;
  fillPanel(ctx, { x: bx, y: r.y + (r.h - 16) / 2, w, h: 16 }, badge.fill, PW.metrics.radiusControl);
  text(ctx, badge.label, bx + w / 2, r.y + r.h / 2, { colour: badge.text, align: 'center' });
}

/**
 * A clickable chip on the right of a section header, to the left of its badge.
 *
 * Reset lives here as well as in the right-click menu because the menu is not
 * ours: on a loaded-up install it sits eighth in a list of entries from other
 * packs, which is indistinguishable from not existing. "Is this node doing
 * anything, and how do I stop it" deserves to be visible on the node.
 *
 * Pure geometry when `ctx` is null, so hit testing and drawing cannot drift.
 */
export function headerChip(
  ctx: Ctx | null,
  r: Rect,
  label: string,
  badgeLabel?: string,
): Rect {
  // Measuring needs a context; fall back to an estimate for hit tests made
  // before the first paint.
  const measure = (s: string) => (ctx ? ((ctx.font = PW.font.body), ctx.measureText(s).width) : s.length * 6.2);
  const badgeW = badgeLabel ? measure(badgeLabel) + 12 + 6 : 0;
  const w = measure(label) + 14;
  const rect = { x: r.x + r.w - badgeW - w, y: r.y + (r.h - 16) / 2, w, h: 16 };
  if (ctx) {
    fillPanel(ctx, rect, PW.color.chip, PW.metrics.radiusControl, PW.color.borderSoft);
    text(ctx, label, rect.x + rect.w / 2, r.y + r.h / 2, { colour: PW.color.textMute, align: 'center' });
  }
  return rect;
}

/** Format a number for a readout: fixed width so digits do not jitter mid-drag. */
export function formatValue(v: number, decimals = 2): string {
  const s = v.toFixed(decimals);
  return s === '-0.00' || s === '-0.0' || s === '-0' ? s.slice(1) : s;
}
