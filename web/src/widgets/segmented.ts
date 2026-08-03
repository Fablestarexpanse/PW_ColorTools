/**
 * Segmented control and chip row — the channel tabs, sort modes, blend modes.
 *
 * One control rather than two, because a "chip row" and a "segmented control"
 * differ only in whether the selection is exclusive, and shipping both would
 * guarantee they drift apart in padding.
 */

import { PW } from '../theme.ts';
import { fillPanel, text, type Ctx, type Rect } from './draw.ts';

export interface Segment {
  id: string;
  label: string;
  /** Optional accent for the selected state — the curve channel colours. */
  colour?: string;
}

export class Segmented {
  segments: Segment[];
  selected: string;

  constructor(segments: Segment[], selected?: string) {
    if (segments.length === 0) throw new Error('Segmented needs at least one segment');
    this.segments = segments;
    this.selected = selected ?? segments[0].id;
  }

  private cellRects(r: Rect): Rect[] {
    const gap = 4;
    const w = (r.w - gap * (this.segments.length - 1)) / this.segments.length;
    return this.segments.map((_, i) => ({ x: r.x + i * (w + gap), y: r.y, w, h: r.h }));
  }

  draw(ctx: Ctx, r: Rect): void {
    const cells = this.cellRects(r);
    this.segments.forEach((seg, i) => {
      const active = seg.id === this.selected;
      const cell = cells[i];
      fillPanel(
        ctx,
        cell,
        active ? PW.color.chipActive : PW.color.chip,
        PW.metrics.radiusControl,
        active ? PW.color.border : PW.color.borderSoft,
      );
      text(ctx, seg.label, cell.x + cell.w / 2, cell.y + cell.h / 2, {
        colour: active ? (seg.colour ?? PW.color.text) : PW.color.textMute,
        align: 'center',
      });
      // A selected channel tab carries its channel colour as an underline
      // rather than as text colour alone — at 12px, coloured text on a dark
      // chip is not reliably legible for red.
      if (active && seg.colour) {
        ctx.fillStyle = seg.colour;
        ctx.fillRect(cell.x + 6, cell.y + cell.h - 3, cell.w - 12, 2);
      }
    });
  }

  /** @returns the id that was clicked, or null. */
  onPointerDown(x: number, y: number, r: Rect): string | null {
    const cells = this.cellRects(r);
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i];
      if (x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h) {
        this.selected = this.segments[i].id;
        return this.selected;
      }
    }
    return null;
  }
}
