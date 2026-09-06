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

import { tonalWeight } from '../core/tonal.ts';
import { app, getWidget, type NodeLike } from '../comfy.ts';
import { BADGE, PW } from '../theme.ts';
import { fillPanel, hairline, sectionHeader, text, type Ctx, type Rect } from '../widgets/draw.ts';
import { attachSpatialPreview } from './spatial_preview.ts';

const M = PW.metrics;
const PANEL_H = 96;

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

      // The tonal response curve sits above the result preview: it is what
      // you set, the preview is what you got.
      attachSpatialPreview(nodeType, {
        height: 190,
        minWidth: 340,
        label: 'Result',
        extra: () => PANEL_H + 18 + M.gapSection,
        drawExtra: (ctx, node, top, w) => {
          const x = M.padding;
          sectionHeader(ctx, 'Tonal response', { x, y: top, w, h: 18 }, BADGE.render);
          drawResponse(ctx, { x, y: top + 20, w, h: PANEL_H - 20 }, node);
        },
      });

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
