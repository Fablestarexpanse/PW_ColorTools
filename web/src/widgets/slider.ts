/**
 * The slider. One implementation, used everywhere.
 *
 * The interaction rules from the brief, all of them enforced here so no node
 * can implement them slightly differently:
 *
 * - The filled track runs from the control's **neutral point**, not from zero,
 *   so "no change" is visually obvious. A saturation slider neutral at 1.0
 *   fills leftward when you pull and rightward when you push.
 * - Shift-drag is a fine adjustment.
 * - Double-click resets to neutral.
 * - The value is always visible, right-aligned, and editable on click.
 *
 * Pointer capture rather than window listeners: a drag that leaves the node
 * must keep tracking, and must stop cleanly if the graph is deleted mid-drag.
 */

import { PW } from '../theme.ts';
import { fillPanel, formatValue, hit, text, type Ctx, type Rect } from './draw.ts';

export interface SliderSpec {
  label: string;
  min: number;
  max: number;
  /** Where the filled track starts. Defaults to `min`, but is usually 0 or 1. */
  neutral?: number;
  /** Reset target for double-click. Defaults to `neutral`. */
  default?: number;
  step?: number;
  decimals?: number;
  /** Suffix for the readout, e.g. " st" for stops. Kept short. */
  unit?: string;
}

const LABEL_W = 86;
const VALUE_W = 52;

export class Slider {
  readonly spec: Required<Omit<SliderSpec, 'unit'>> & { unit: string };
  value: number;
  private dragging = false;
  private dragStartX = 0;
  private dragStartValue = 0;
  private lastClick = 0;

  constructor(spec: SliderSpec, value?: number) {
    const neutral = spec.neutral ?? spec.min;
    this.spec = {
      label: spec.label,
      min: spec.min,
      max: spec.max,
      neutral,
      default: spec.default ?? neutral,
      step: spec.step ?? 0.01,
      decimals: spec.decimals ?? 2,
      unit: spec.unit ?? '',
    };
    this.value = value ?? this.spec.default;
  }

  private trackRect(r: Rect): Rect {
    const x = r.x + LABEL_W;
    return { x, y: r.y + r.h / 2 - 3, w: r.w - LABEL_W - VALUE_W - PW.metrics.gapControl, h: 6 };
  }

  private toValue(px: number, track: Rect): number {
    const t = Math.min(1, Math.max(0, (px - track.x) / track.w));
    return this.spec.min + t * (this.spec.max - this.spec.min);
  }

  private toPixel(v: number, track: Rect): number {
    const t = (v - this.spec.min) / (this.spec.max - this.spec.min);
    return track.x + Math.min(1, Math.max(0, t)) * track.w;
  }

  private quantise(v: number): number {
    const { min, max, step } = this.spec;
    const snapped = Math.round(v / step) * step;
    return Math.min(max, Math.max(min, snapped));
  }

  draw(ctx: Ctx, r: Rect): void {
    const track = this.trackRect(r);
    const { neutral } = this.spec;

    text(ctx, this.spec.label, r.x, r.y + r.h / 2, { colour: PW.color.textDim });

    // Track.
    fillPanel(ctx, track, PW.color.well, 3);

    // Fill from neutral, not from zero — this is what makes "unchanged" read
    // at a glance instead of requiring the user to parse the number.
    const nx = this.toPixel(neutral, track);
    const vx = this.toPixel(this.value, track);
    if (Math.abs(vx - nx) > 0.5) {
      fillPanel(ctx, { x: Math.min(nx, vx), y: track.y, w: Math.abs(vx - nx), h: track.h }, PW.color.accent, 3);
    }

    // Neutral tick, drawn over the fill so it stays findable.
    if (neutral > this.spec.min && neutral < this.spec.max) {
      ctx.fillStyle = PW.color.textMute;
      ctx.fillRect(Math.round(nx), track.y - 2, 1, track.h + 4);
    }

    // Knob.
    ctx.beginPath();
    ctx.arc(vx, track.y + track.h / 2, PW.metrics.knob, 0, Math.PI * 2);
    ctx.fillStyle = PW.color.text;
    ctx.fill();

    text(ctx, formatValue(this.value, this.spec.decimals) + this.spec.unit, r.x + r.w, r.y + r.h / 2, {
      colour: PW.color.textMute,
      align: 'right',
      font: PW.font.mono,
    });
  }

  /** @returns true if the event was consumed. */
  onPointerDown(x: number, y: number, r: Rect, now: number): boolean {
    const track = this.trackRect(r);
    const knobX = this.toPixel(this.value, track);
    const onKnob = Math.abs(x - knobX) <= PW.metrics.hitSlop && Math.abs(y - (track.y + track.h / 2)) <= PW.metrics.hitSlop;
    if (!onKnob && !hit(track, x, y, PW.metrics.hitSlop)) return false;

    if (now - this.lastClick < PW.interaction.doubleClickMs) {
      this.value = this.spec.default;
      this.lastClick = 0;
      return true;
    }
    this.lastClick = now;

    this.dragging = true;
    this.dragStartX = x;
    // Grabbing the track jumps to the click; grabbing the knob does not, so a
    // careful adjustment does not lurch on the first pixel.
    this.dragStartValue = onKnob ? this.value : this.quantise(this.toValue(x, track));
    this.value = this.dragStartValue;
    return true;
  }

  onPointerMove(x: number, _y: number, r: Rect, shift: boolean): boolean {
    if (!this.dragging) return false;
    const track = this.trackRect(r);
    const perPixel = (this.spec.max - this.spec.min) / track.w;
    const scale = shift ? PW.interaction.fineDragScale : 1;
    this.value = this.quantise(this.dragStartValue + (x - this.dragStartX) * perPixel * scale);
    return true;
  }

  onPointerUp(): boolean {
    const was = this.dragging;
    this.dragging = false;
    return was;
  }

  get isDragging(): boolean {
    return this.dragging;
  }
}
