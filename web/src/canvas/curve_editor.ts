/**
 * The curve editor.
 *
 * Draws, in back-to-front order: the well, the grid, the input histogram, the
 * dashed identity diagonal, the ghost of the pre-edit curve while dragging, the
 * curve itself, and the control points.
 *
 * Interaction, per the brief:
 *  - click empty space to add a point
 *  - shift-click a point to remove it (minimum two retained)
 *  - drag to move, shift-drag for fine adjustment
 *  - double-click the canvas to reset the channel to identity
 *  - live in/out readout follows the hovered or dragged point
 *
 * The curve maths lives in `core/curve.ts` and is mirrored in Python. Nothing
 * in this file evaluates a curve except through `Curve`, so the editor cannot
 * show something the renderer will not reproduce.
 */

import { PW } from '../theme.ts';
import { Curve, IDENTITY_POINTS, type CurvePoint } from '../core/curve.ts';
import { fillPanel, formatValue, hairline, text, type Ctx, type Rect } from '../widgets/draw.ts';

const GRID_DIVISIONS = 4;
const POINT_RADIUS = 4;
const GRAB_SLOP = 10;
const MIN_POINTS = 2;
/** Closest two points may sit on the x axis. Below this they are the same point. */
const MIN_X_GAP = 0.008;

export type ChannelId = 'luma' | 'r' | 'g' | 'b';

export interface CurveEditorState {
  luma: CurvePoint[];
  r: CurvePoint[];
  g: CurvePoint[];
  b: CurvePoint[];
}

export function identityState(): CurveEditorState {
  return {
    luma: IDENTITY_POINTS.map((p) => [p[0], p[1]] as CurvePoint),
    r: IDENTITY_POINTS.map((p) => [p[0], p[1]] as CurvePoint),
    g: IDENTITY_POINTS.map((p) => [p[0], p[1]] as CurvePoint),
    b: IDENTITY_POINTS.map((p) => [p[0], p[1]] as CurvePoint),
  };
}

const CHANNEL_COLOUR: Record<ChannelId, string> = {
  luma: PW.channel.luma,
  r: PW.channel.r,
  g: PW.channel.g,
  b: PW.channel.b,
};

export class CurveEditor {
  state: CurveEditorState = identityState();
  channel: ChannelId = 'luma';
  /** 256-bin per-channel histogram of the node's input, or null before we have one. */
  histogram: { luma: Float32Array; r: Float32Array; g: Float32Array; b: Float32Array } | null = null;
  /** Called whenever the curve changes, so the node can re-bake and serialise. */
  onChange: (() => void) | null = null;

  private dragIndex = -1;
  private ghost: CurvePoint[] | null = null;
  private hoverIndex = -1;
  private lastClickTime = 0;
  private dragMoved = false;

  get points(): CurvePoint[] {
    return this.state[this.channel];
  }

  private set points(v: CurvePoint[]) {
    this.state[this.channel] = v;
  }

  // -- coordinate mapping ---------------------------------------------------
  // Curve space is (0,0) bottom-left to (1,1) top-right. Canvas y is inverted.

  private toCanvas(r: Rect, p: CurvePoint): [number, number] {
    return [r.x + p[0] * r.w, r.y + (1 - p[1]) * r.h];
  }

  private toCurve(r: Rect, x: number, y: number): CurvePoint {
    return [clamp01((x - r.x) / r.w), clamp01(1 - (y - r.y) / r.h)];
  }

  // -- drawing --------------------------------------------------------------

  draw(ctx: Ctx, r: Rect): void {
    fillPanel(ctx, r, PW.color.well, PW.metrics.radiusPanel, PW.color.border);

    ctx.save();
    ctx.beginPath();
    ctx.rect(r.x, r.y, r.w, r.h);
    ctx.clip();

    this.drawHistogram(ctx, r);
    this.drawGrid(ctx, r);
    this.drawIdentity(ctx, r);
    if (this.ghost) this.drawCurve(ctx, r, this.ghost, PW.color.textMute, 1, true);
    this.drawCurve(ctx, r, this.points, CHANNEL_COLOUR[this.channel], 2, false);
    this.drawPoints(ctx, r);

    ctx.restore();
    this.drawReadout(ctx, r);
  }

  private drawGrid(ctx: Ctx, r: Rect): void {
    for (let i = 1; i < GRID_DIVISIONS; i++) {
      const t = i / GRID_DIVISIONS;
      hairline(ctx, r.x + t * r.w, r.y, r.x + t * r.w, r.y + r.h, PW.color.grid);
      hairline(ctx, r.x, r.y + t * r.h, r.x + r.w, r.y + t * r.h, PW.color.grid);
    }
  }

  /**
   * The input histogram, behind the grid.
   *
   * Drawn on a mild power scale rather than linear or log: a linear histogram
   * of a normal photograph is one spike and a flat line, and a log one makes
   * three stray pixels look like a tonal region. 0.4 is the usual compromise.
   */
  private drawHistogram(ctx: Ctx, r: Rect): void {
    const h = this.histogram;
    if (!h) return;
    const bins = this.channel === 'luma' ? h.luma : this.channel === 'r' ? h.r : this.channel === 'g' ? h.g : h.b;
    let peak = 0;
    for (let i = 0; i < bins.length; i++) peak = Math.max(peak, bins[i]);
    if (peak <= 0) return;

    ctx.fillStyle = PW.color.surface;
    ctx.beginPath();
    ctx.moveTo(r.x, r.y + r.h);
    for (let i = 0; i < bins.length; i++) {
      const t = i / (bins.length - 1);
      const v = Math.pow(bins[i] / peak, 0.4);
      ctx.lineTo(r.x + t * r.w, r.y + r.h - v * r.h * 0.92);
    }
    ctx.lineTo(r.x + r.w, r.y + r.h);
    ctx.closePath();
    ctx.fill();
  }

  private drawIdentity(ctx: Ctx, r: Rect): void {
    ctx.save();
    ctx.setLineDash([3, 4]);
    ctx.strokeStyle = PW.color.borderSoft;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(r.x, r.y + r.h);
    ctx.lineTo(r.x + r.w, r.y);
    ctx.stroke();
    ctx.restore();
  }

  private drawCurve(ctx: Ctx, r: Rect, pts: CurvePoint[], colour: string, width: number, dashed: boolean): void {
    const curve = new Curve(pts);
    ctx.save();
    if (dashed) {
      ctx.setLineDash([2, 3]);
      ctx.globalAlpha = 0.7;
    }
    ctx.strokeStyle = colour;
    ctx.lineWidth = width;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    // One sample per device pixel: any coarser and the S-curve shoulder
    // visibly facets at typical node sizes.
    const steps = Math.max(64, Math.ceil(r.w));
    for (let i = 0; i <= steps; i++) {
      const x = i / steps;
      const [cx, cy] = this.toCanvas(r, [x, curve.at(x)]);
      if (i === 0) ctx.moveTo(cx, cy);
      else ctx.lineTo(cx, cy);
    }
    ctx.stroke();
    ctx.restore();
  }

  private drawPoints(ctx: Ctx, r: Rect): void {
    const colour = CHANNEL_COLOUR[this.channel];
    this.points.forEach((p, i) => {
      const [x, y] = this.toCanvas(r, p);
      const active = i === this.dragIndex || i === this.hoverIndex;
      ctx.beginPath();
      ctx.arc(x, y, active ? POINT_RADIUS + 1.5 : POINT_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = active ? PW.color.text : PW.color.panel;
      ctx.fill();
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    });
  }

  private drawReadout(ctx: Ctx, r: Rect): void {
    const i = this.dragIndex >= 0 ? this.dragIndex : this.hoverIndex;
    if (i < 0 || i >= this.points.length) return;
    const p = this.points[i];
    const label = `in ${formatValue(p[0], 3)}   out ${formatValue(p[1], 3)}`;
    text(ctx, label, r.x + r.w - 8, r.y + 12, {
      colour: PW.color.textMute,
      align: 'right',
      font: PW.font.mono,
    });
  }

  // -- interaction ----------------------------------------------------------

  private findPoint(r: Rect, x: number, y: number): number {
    let best = -1;
    let bestD = GRAB_SLOP;
    this.points.forEach((p, i) => {
      const [px, py] = this.toCanvas(r, p);
      const d = Math.hypot(px - x, py - y);
      if (d <= bestD) {
        bestD = d;
        best = i;
      }
    });
    return best;
  }

  onPointerDown(x: number, y: number, r: Rect, shift: boolean, now: number): boolean {
    const idx = this.findPoint(r, x, y);

    if (shift && idx >= 0) {
      if (this.points.length <= MIN_POINTS) return true; // refuse, keep the curve valid
      this.points = this.points.filter((_, i) => i !== idx);
      this.hoverIndex = -1;
      this.changed();
      return true;
    }

    if (now - this.lastClickTime < 300 && idx < 0) {
      this.resetChannel();
      this.lastClickTime = 0;
      return true;
    }
    this.lastClickTime = now;

    // Ghost is captured before the edit so the user can see what they are
    // changing *from*, which is the whole reason it exists.
    this.ghost = this.points.map((p) => [p[0], p[1]] as CurvePoint);
    this.dragMoved = false;

    if (idx >= 0) {
      this.dragIndex = idx;
      return true;
    }

    const p = this.toCurve(r, x, y);
    if (this.points.some((q) => Math.abs(q[0] - p[0]) < MIN_X_GAP)) {
      this.ghost = null;
      return true;
    }
    const next = [...this.points, p].sort((a, b) => a[0] - b[0]);
    this.points = next;
    this.dragIndex = next.findIndex((q) => q === p);
    this.changed();
    return true;
  }

  onPointerMove(x: number, y: number, r: Rect, shift: boolean): boolean {
    if (this.dragIndex < 0) {
      const idx = this.findPoint(r, x, y);
      const changed = idx !== this.hoverIndex;
      this.hoverIndex = idx;
      return changed;
    }

    let p = this.toCurve(r, x, y);
    if (shift && this.ghost) {
      // Fine adjust is relative to where the point started, not to the last
      // frame, so releasing and re-pressing shift mid-drag does not accumulate.
      const start = this.ghost[Math.min(this.dragIndex, this.ghost.length - 1)];
      p = [
        clamp01(start[0] + (p[0] - start[0]) * PW.interaction.fineDragScale),
        clamp01(start[1] + (p[1] - start[1]) * PW.interaction.fineDragScale),
      ];
    }

    // Endpoints keep their x. Letting the user drag the black point inward
    // looks like a feature and is actually how you get a curve with an
    // undefined region at one end.
    const isFirst = this.dragIndex === 0;
    const isLast = this.dragIndex === this.points.length - 1;
    if (isFirst) p = [0, p[1]];
    if (isLast) p = [1, p[1]];

    // Keep x ordering with a minimum gap, rather than allowing a swap. A swap
    // mid-drag makes the point the user is holding jump under the cursor.
    if (!isFirst && !isLast) {
      const lo = this.points[this.dragIndex - 1][0] + MIN_X_GAP;
      const hi = this.points[this.dragIndex + 1][0] - MIN_X_GAP;
      p = [Math.min(Math.max(p[0], lo), hi), p[1]];
    }

    const cur = this.points[this.dragIndex];
    if (cur[0] === p[0] && cur[1] === p[1]) return false;
    this.points = this.points.map((q, i) => (i === this.dragIndex ? p : q));
    this.dragMoved = true;
    this.changed();
    return true;
  }

  onPointerUp(): boolean {
    const was = this.dragIndex >= 0;
    this.dragIndex = -1;
    this.ghost = null;
    if (was && !this.dragMoved) this.changed();
    return was;
  }

  get isDragging(): boolean {
    return this.dragIndex >= 0;
  }

  resetChannel(): void {
    this.points = IDENTITY_POINTS.map((p) => [p[0], p[1]] as CurvePoint);
    this.hoverIndex = -1;
    this.changed();
  }

  resetAll(): void {
    this.state = identityState();
    this.changed();
  }

  applyPreset(preset: Partial<CurveEditorState>): void {
    for (const k of ['luma', 'r', 'g', 'b'] as ChannelId[]) {
      const v = preset[k];
      if (v) this.state[k] = v.map((p) => [p[0], p[1]] as CurvePoint);
    }
    this.changed();
  }

  private changed(): void {
    this.onChange?.();
  }
}

function clamp01(v: number): number {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}
