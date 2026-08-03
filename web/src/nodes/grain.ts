/**
 * PW Grain — node wiring.
 *
 * Grain is spatial, so there is no lattice and no exact preview. What this
 * draws is the tonal response curve, which is the control users actually reason
 * about ("where is my grain?") and which we *can* show exactly, because it is a
 * pure function of the three sliders and is evaluated by the same formula the
 * renderer uses.
 *
 * The section is badged `render only` rather than pretending otherwise. The
 * architecture is explicit that the LUT/spatial split gets surfaced.
 */

import { app, chainHandler, getWidget, type NodeLike } from '../comfy.ts';
import { BADGE, PW } from '../theme.ts';
import { fillPanel, hairline, sectionHeader, text, type Ctx, type Rect } from '../widgets/draw.ts';
import { fitPanel } from '../widgets/layout.ts';

const M = PW.metrics;
const PANEL_H = 96;
const EDGE_FALLOFF = 0.04; // mirrors EDGE_FALLOFF in pw_color/grain.py

function smoothstep(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/**
 * The tonal weight at a given perceptual position. Line-for-line the same as
 * `TonalResponse.weight` in Python, so the curve drawn here is the curve the
 * renderer applies — not an impression of it.
 */
function tonalWeight(t: number, shadows: number, mids: number, highlights: number): number {
  const shadow = 1 - smoothstep(0, 0.5, t);
  const highlight = smoothstep(0.5, 1, t);
  const mid = Math.max(0, 1 - shadow - highlight);
  const w = shadow * shadows + mid * mids + highlight * highlights;
  return w * smoothstep(0, EDGE_FALLOFF, t) * smoothstep(0, EDGE_FALLOFF, 1 - t);
}

function num(node: NodeLike, name: string, fallback: number): number {
  const v = getWidget(node, name)?.value;
  return typeof v === 'number' ? v : fallback;
}

function drawResponse(ctx: Ctx, r: Rect, node: NodeLike): void {
  fillPanel(ctx, r, PW.color.well, M.radiusPanel, PW.color.border);

  const s = num(node, 'shadows', 0.2);
  const m = num(node, 'midtones', 1.0);
  const h = num(node, 'highlights', 0.1);
  const peak = Math.max(1e-6, s, m, h);

  // A tonal ramp along the bottom, so "shadows" and "highlights" are anchored
  // to something visible rather than to the user's memory of the axis.
  const strip = 8;
  for (let px = 0; px < r.w; px++) {
    const v = Math.round((px / Math.max(1, r.w - 1)) * 255);
    ctx.fillStyle = `rgb(${v},${v},${v})`;
    ctx.fillRect(r.x + px, r.y + r.h - strip, 1, strip);
  }

  for (let i = 1; i < 4; i++) {
    const x = r.x + (i / 4) * r.w;
    hairline(ctx, x, r.y, x, r.y + r.h - strip, PW.color.grid);
  }

  const plotH = r.h - strip - 6;
  ctx.beginPath();
  ctx.moveTo(r.x, r.y + plotH);
  const steps = Math.max(48, Math.ceil(r.w));
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const w = tonalWeight(t, s, m, h) / peak;
    ctx.lineTo(r.x + t * r.w, r.y + plotH - w * (plotH - 6));
  }
  ctx.lineTo(r.x + r.w, r.y + plotH);
  ctx.closePath();
  ctx.fillStyle = PW.color.surface;
  ctx.fill();

  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const w = tonalWeight(t, s, m, h) / peak;
    const x = r.x + t * r.w;
    const y = r.y + plotH - w * (plotH - 6);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = PW.channel.warm;
  ctx.lineWidth = 2;
  ctx.stroke();

  text(ctx, 'shadows', r.x + 4, r.y + 10, { colour: PW.color.textMute });
  text(ctx, 'highlights', r.x + r.w - 4, r.y + 10, { colour: PW.color.textMute, align: 'right' });
}

export function registerGrain(): void {
  app.registerExtension({
    name: 'pw.color.grain',
    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Grain') return;

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const r = onCreated?.apply(this, arguments as any);
        // Size from LiteGraph's own widget measurement plus our panel, never
        // from a guessed constant — PW Grain has a lot of widgets and the
        // guess collided with them.
        fitPanel(this, PANEL_H + 22 + M.gapSection + M.padding, 320);

        chainHandler(this, 'onDrawForeground', function (this: NodeLike, ctx: Ctx) {
          if ((this as any).flags?.collapsed) return;
          const x = M.padding;
          const w = this.size[0] - M.padding * 2;
          const y = this.size[1] - PANEL_H - M.padding - 18;
          sectionHeader(ctx, 'Tonal response', { x, y, w, h: 18 }, BADGE.render);
          drawResponse(ctx, { x, y: y + 20, w, h: PANEL_H - 20 }, this);
        });

        return r;
      };

      // Redraw the response curve as the sliders move.
      const onWidgetChanged = nodeType.prototype.onWidgetChanged;
      nodeType.prototype.onWidgetChanged = function (this: NodeLike) {
        const res = onWidgetChanged?.apply(this, arguments as any);
        this.setDirtyCanvas?.(true, true);
        return res;
      };
    },
  });
}
