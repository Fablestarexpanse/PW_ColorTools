/**
 * PW Look — node wiring.
 *
 * The headline interaction from the brief: **presets are rendered on the user's
 * own image**, not on generic thumbnails. A preset strip of stock photos tells
 * you nothing about what a look will do to *your* frame, so we fetch the node's
 * cached input proxy and bake each preset onto it with the same lattice code
 * the renderer uses. Presets lead; sliders follow.
 *
 * The eight-band HSL mixer is drawn here too, collapsed by default because it
 * is the least-used control on a busy node.
 */

import { app, chainHandler, getWidget, type NodeLike } from '../comfy.ts';
import { BADGE, PW } from '../theme.ts';
import { Preview } from '../canvas/preview.ts';
import { Lattice, DEFAULT_SIZE } from '../core/lattice.ts';
import { buildSampleFn } from '../core/ops.ts';
import { HSL_BANDS } from '../core/look_ops.ts';
import { isComparing, onCompareChange } from '../widgets/compare.ts';
import { fillPanel, hit, sectionHeader, text, type Ctx, type Rect } from '../widgets/draw.ts';
import { fitPanel, widgetHeight } from '../widgets/layout.ts';
import { Segmented } from '../widgets/segmented.ts';

const M = PW.metrics;
const THUMB_H = 74;
const THUMB_W = 96;
const HSL_ROW_H = 22;
const HEADER_H = 18;
const PREVIEW_H = 150;

interface Preset { id: string; name: string; description: string; params: Record<string, any> }

interface LookUI {
  presets: Preset[];
  /** Preset id -> thumbnail rendered on the user's own image. */
  thumbs: Map<string, ImageBitmap | HTMLCanvasElement>;
  source: ImageData | null;
  hslOpen: boolean;
  hslTab: Segmented;
  preview: Preview;
}

/** Rebuild the lattice the live preview samples from the node's current state. */
function refreshPreview(node: NodeLike): void {
  const ui = uis.get(node);
  if (!ui) return;
  const num = (name: string, d: number) => {
    const v = getWidget(node, name)?.value;
    return typeof v === 'number' ? v : d;
  };
  let bands: Record<string, any> = {};
  try {
    bands = JSON.parse(String(getWidget(node, 'hsl')?.value ?? '{}'));
  } catch { /* keep empty */ }

  const ops = [
    {
      type: 'tone',
      params: {
        exposure: num('exposure', 0), contrast: num('contrast', 0),
        highlights: num('highlights', 0), shadows: num('shadows', 0),
        whites: num('whites', 0), blacks: num('blacks', 0),
      },
    },
    {
      type: 'colour',
      params: {
        warmth: num('warmth', 0), tint: num('tint', 0),
        vibrance: num('vibrance', 0), saturation: num('saturation', 1),
      },
    },
    { type: 'hsl', params: { bands } },
  ];
  ui.preview.lattice = Lattice.fromFn(buildSampleFn(ops) as any, DEFAULT_SIZE);
  ui.preview.digest = JSON.stringify(ops);
}

const uis = new WeakMap<object, LookUI>();

// -- presets ------------------------------------------------------------------

let presetCache: Preset[] | null = null;

async function loadPresets(): Promise<Preset[]> {
  if (presetCache) return presetCache;
  try {
    const res = await fetch('/pw_color/presets');
    if (!res.ok) return (presetCache = []);
    presetCache = (await res.json()).presets ?? [];
  } catch {
    presetCache = [];
  }
  return presetCache!;
}

/** Slider names a preset can set, and the value each returns to when it does not. */
const PRESET_SLIDERS: Record<string, number> = {
  exposure: 0, contrast: 0, highlights: 0, shadows: 0, whites: 0, blacks: 0,
  warmth: 0, tint: 0, vibrance: 0, saturation: 1,
  glow: 0, glow_radius: 24, glow_threshold: 0.65,
  gradient_map: 0,
};

/**
 * Apply a preset by writing its values into the widgets.
 *
 * "Presets lead, sliders follow" means exactly this: after clicking a preset
 * the sliders must show what it did, so the next move is an adjustment rather
 * than a guess. Setting only the preset combo left every slider reading 0.00
 * while the image was clearly graded, which is a UI that lies.
 *
 * Sliders the preset does not mention are reset to neutral, so switching from
 * a heavy preset to a light one does not silently keep the heavy one's values.
 */
function applyPreset(node: NodeLike, preset: Preset): void {
  const combo = getWidget(node, 'preset');
  if (combo) {
    combo.value = preset.id;
    combo.callback?.(combo.value);
  }
  if (preset.id !== 'none') {
    for (const [name, neutral] of Object.entries(PRESET_SLIDERS)) {
      const w = getWidget(node, name);
      if (!w) continue;
      const key = name === 'gradient_map' ? 'gradient_map_amount' : name;
      const next = typeof preset.params[key] === 'number' ? preset.params[key] : neutral;
      if (w.value !== next) {
        w.value = next;
        w.callback?.(w.value);
      }
    }
    const blend = getWidget(node, 'gradient_blend');
    if (blend && preset.params.gradient_map_blend) {
      blend.value = preset.params.gradient_map_blend;
      blend.callback?.(blend.value);
    }
    const hsl = getWidget(node, 'hsl');
    if (hsl) {
      hsl.value = JSON.stringify(preset.params.hsl ?? {});
      hsl.callback?.(hsl.value);
    }
  }
  refreshPreview(node);
  node.setDirtyCanvas?.(true, true);
}

/** Map a preset's flat params onto the LOOK ops the lattice understands. */
function presetOps(p: Record<string, any>): any[] {
  const num = (k: string, d = 0) => (typeof p[k] === 'number' ? p[k] : d);
  return [
    {
      type: 'tone',
      params: {
        exposure: num('exposure'), contrast: num('contrast'),
        highlights: num('highlights'), shadows: num('shadows'),
        whites: num('whites'), blacks: num('blacks'),
      },
    },
    {
      type: 'colour',
      params: {
        warmth: num('warmth'), tint: num('tint'),
        vibrance: num('vibrance'), saturation: num('saturation', 1),
      },
    },
    { type: 'hsl', params: { bands: p.hsl ?? {} } },
    {
      type: 'gradient_map',
      params: {
        amount: num('gradient_map_amount'),
        blend: p.gradient_map_blend ?? 'colour',
        stops: p.gradient_map_stops ?? [],
      },
    },
  ];
}

/**
 * Render every preset onto the node's own input.
 *
 * Uses a 33³ lattice regardless of the node's quality setting: a 96px
 * thumbnail cannot show the difference, and 65³ for eight presets would be a
 * visible stall on a control that should feel instant.
 */
function buildThumbnails(node: NodeLike, ui: LookUI): void {
  if (!ui.source) return;
  const { width, height, data } = ui.source;
  ui.thumbs.clear();

  for (const preset of ui.presets) {
    const cv = document.createElement('canvas');
    cv.width = width; cv.height = height;
    const ctx = cv.getContext('2d')!;
    const out = ctx.createImageData(width, height);

    if (preset.id === 'none') {
      out.data.set(data);
    } else {
      const lat = Lattice.fromFn(buildSampleFn(presetOps(preset.params)) as any, DEFAULT_SIZE);
      for (let i = 0; i < data.length; i += 4) {
        const c = lat.applyImage([data[i] / 255, data[i + 1] / 255, data[i + 2] / 255]);
        out.data[i] = c[0] * 255; out.data[i + 1] = c[1] * 255; out.data[i + 2] = c[2] * 255;
        out.data[i + 3] = 255;
      }
    }
    ctx.putImageData(out, 0, 0);
    ui.thumbs.set(preset.id, cv);
  }
  node.setDirtyCanvas?.(true, true);
}

/** Fetch the node's cached input and downscale it to thumbnail size. */
async function loadSource(node: NodeLike, ui: LookUI): Promise<void> {
  try {
    const res = await fetch(`/pw_color/input/${node.id}`);
    if (!res.ok) return; // 404 just means the graph has not run yet
    const blob = await res.blob();
    const bmp = await createImageBitmap(blob);
    const cv = document.createElement('canvas');
    cv.width = THUMB_W; cv.height = THUMB_H;
    const ctx = cv.getContext('2d')!;
    // Cover-fit: a letterboxed thumbnail wastes the little space we have.
    const scale = Math.max(THUMB_W / bmp.width, THUMB_H / bmp.height);
    const w = bmp.width * scale, h = bmp.height * scale;
    ctx.drawImage(bmp, (THUMB_W - w) / 2, (THUMB_H - h) / 2, w, h);
    bmp.close();
    ui.source = ctx.getImageData(0, 0, THUMB_W, THUMB_H);
    buildThumbnails(node, ui);
  } catch {
    /* no proxy yet; the strip shows a hint instead */
  }
}

// -- HSL mixer ----------------------------------------------------------------

function readHsl(node: NodeLike): Record<string, { hue: number; sat: number; lum: number }> {
  const out: Record<string, { hue: number; sat: number; lum: number }> = {};
  for (const [name] of HSL_BANDS) out[name] = { hue: 0, sat: 0, lum: 0 };
  try {
    const raw = JSON.parse(String(getWidget(node, 'hsl')?.value ?? '{}'));
    for (const [k, v] of Object.entries(raw)) {
      if (out[k] && v && typeof v === 'object') Object.assign(out[k], v);
    }
  } catch { /* keep the zeroed default */ }
  return out;
}

function writeHsl(node: NodeLike, bands: Record<string, any>): void {
  const w = getWidget(node, 'hsl');
  if (!w) return;
  // Drop zeroed bands so the workflow JSON stays small and readable.
  const trimmed: Record<string, any> = {};
  for (const [k, v] of Object.entries(bands)) {
    if (v.hue || v.sat || v.lum) trimmed[k] = v;
  }
  w.value = JSON.stringify(trimmed);
  node.setDirtyCanvas?.(true, true);
}

const HSL_AXES = ['hue', 'sat', 'lum'] as const;

function drawHsl(ctx: Ctx, r: Rect, node: NodeLike, ui: LookUI): void {
  const bands = readHsl(node);
  const axis = ui.hslTab.selected as (typeof HSL_AXES)[number];
  const rowW = r.w;
  HSL_BANDS.forEach(([name, hue], i) => {
    const y = r.y + i * HSL_ROW_H;
    const swatchW = 46;
    // The band's own hue as the label swatch, so the row is self-describing.
    const c = 0.11;
    const rgbCss = oklchCss(0.62, c, hue);
    fillPanel(ctx, { x: r.x, y: y + 3, w: swatchW, h: HSL_ROW_H - 7 }, rgbCss, M.radiusControl);
    text(ctx, name, r.x + swatchW + 8, y + HSL_ROW_H / 2, { colour: PW.color.textDim });

    const trackX = r.x + swatchW + 62;
    const trackW = rowW - (swatchW + 62) - 40;
    const track = { x: trackX, y: y + HSL_ROW_H / 2 - 2, w: trackW, h: 4 };
    fillPanel(ctx, track, PW.color.well, 2);
    const v = bands[name][axis] ?? 0;
    const mid = trackX + trackW / 2;
    const px = mid + (v / 1) * (trackW / 2);
    if (Math.abs(px - mid) > 0.5) {
      fillPanel(ctx, { x: Math.min(mid, px), y: track.y, w: Math.abs(px - mid), h: 4 }, PW.color.accent, 2);
    }
    ctx.beginPath();
    ctx.arc(px, track.y + 2, 4, 0, Math.PI * 2);
    ctx.fillStyle = PW.color.text;
    ctx.fill();
    text(ctx, v.toFixed(2), r.x + rowW, y + HSL_ROW_H / 2, {
      colour: PW.color.textMute, align: 'right', font: PW.font.mono,
    });
  });
}

/** OKLCh to a CSS colour, for the band swatches. */
function oklchCss(l: number, c: number, h: number): string {
  return `oklch(${(l * 100).toFixed(1)}% ${c.toFixed(3)} ${((h * 180) / Math.PI).toFixed(1)}deg)`;
}

// -- layout -------------------------------------------------------------------

const CELL_H = THUMB_H + 18;

/** How many thumbnails fit across, and therefore how many rows we need. */
function gridShape(width: number, count: number): { cols: number; rows: number; cellW: number } {
  const cols = Math.max(1, Math.floor((width + 8) / (THUMB_W + 8)));
  const rows = Math.max(1, Math.ceil(count / cols));
  // Spread the slack rather than leaving a ragged right edge.
  const cellW = (width - 8 * (cols - 1)) / cols;
  return { cols, rows, cellW };
}

function layout(node: NodeLike, ui: LookUI) {
  const x = M.padding;
  const w = node.size[0] - M.padding * 2;
  const { rows } = gridShape(w, Math.max(1, ui.presets.length));
  let y = widgetHeight(node) + M.gapSection;
  const previewHeader = { x, y, w, h: HEADER_H };
  y += HEADER_H + 6;
  const preview = { x, y, w, h: PREVIEW_H };
  y += PREVIEW_H + M.gapSection;
  const presetHeader = { x, y, w, h: HEADER_H };
  y += HEADER_H + 6;
  const strip = { x, y, w, h: rows * CELL_H + (rows - 1) * 6 };
  y += strip.h + M.gapSection;
  const hslHeader = { x, y, w, h: HEADER_H };
  y += HEADER_H + 6;
  const hslTabs = { x, y, w: Math.min(w, 220), h: 22 };
  const hslRows = { x, y: y + 26, w, h: HSL_BANDS.length * HSL_ROW_H };
  return { previewHeader, preview, presetHeader, strip, hslHeader, hslTabs, hslRows };
}

function panelHeight(node: NodeLike, ui: LookUI): number {
  const w = Math.max(200, node.size[0] - M.padding * 2);
  const { rows } = gridShape(w, Math.max(1, ui.presets.length));
  const strip = rows * CELL_H + (rows - 1) * 6;
  const base =
    HEADER_H + 6 + PREVIEW_H + M.gapSection + HEADER_H + 6 + strip + M.gapSection + HEADER_H + 6 + M.padding;
  return base + (ui.hslOpen ? 26 + HSL_BANDS.length * HSL_ROW_H + M.gapControl : 0);
}

export function registerLook(): void {
  app.registerExtension({
    name: 'pw.color.look',

    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Look') return;

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const r = onCreated?.apply(this, arguments as any);

        const hw = getWidget(this, 'hsl');
        if (hw) { hw.type = 'hidden'; hw.computeSize = () => [0, -4]; }

        const ui: LookUI = {
          presets: [], thumbs: new Map(), source: null, hslOpen: false,
          hslTab: new Segmented(HSL_AXES.map((a) => ({ id: a, label: a }))),
          preview: new Preview(),
        };
        uis.set(this, ui);
        fitPanel(this, panelHeight(this, ui), 420);
        refreshPreview(this);

        void (async () => {
          ui.presets = await loadPresets();
          // Re-fit: the strip's row count depends on how many presets exist,
          // and they arrive over HTTP after the node has already been sized.
          fitPanel(this, panelHeight(this, ui), 420);
          await loadSource(this, ui);
          await ui.preview.load(this.id, () => this.setDirtyCanvas?.(true, true));
          this.setDirtyCanvas?.(true, true);
        })();

        const unsubscribe = onCompareChange(() => this.setDirtyCanvas?.(true, true));
        const priorRemoved = this.onRemoved;
        this.onRemoved = function (this: NodeLike) {
          unsubscribe();
          priorRemoved?.call(this);
        };

        // Resizing changes how many thumbnails fit per row, so the panel
        // height has to follow the width.
        chainHandler(this, 'onResize', function (this: NodeLike) {
          const needed = panelHeight(this, ui);
          const min = widgetHeight(this) + needed;
          if (this.size[1] < min) this.size[1] = min;
        });

        chainHandler(this, 'onDrawForeground', function (this: NodeLike, ctx: Ctx) {
          if ((this as any).flags?.collapsed) return;
          const L = layout(this, ui);

          sectionHeader(ctx, 'Preview', L.previewHeader, BADGE.lut);
          ui.preview.comparing = isComparing();
          ui.preview.draw(ctx, L.preview);

          sectionHeader(ctx, 'Presets, on your image', L.presetHeader, BADGE.lut);
          drawStrip(ctx, L.strip, this, ui);

          const arrow = ui.hslOpen ? 'v' : '>';
          sectionHeader(ctx, `${arrow}  Colour mixer`, L.hslHeader, BADGE.lut);
          if (ui.hslOpen) {
            ui.hslTab.draw(ctx, L.hslTabs);
            drawHsl(ctx, L.hslRows, this, ui);
          }
        });

        chainHandler(this, 'onMouseDown', function (this: NodeLike, e: any, pos: [number, number]) {
          const L = layout(this, ui);
          const [x, y] = pos;

          if (hit(L.preview, x, y)) {
            ui.preview.onPointer(x, L.preview, true);
            this.setDirtyCanvas?.(true, true);
            return true;
          }

          if (hit(L.hslHeader, x, y)) {
            ui.hslOpen = !ui.hslOpen;
            fitPanel(this, panelHeight(this, ui), 420);
            this.setDirtyCanvas?.(true, true);
            return true;
          }

          if (hit(L.strip, x, y) && ui.presets.length) {
            const { cols, cellW } = gridShape(L.strip.w, ui.presets.length);
            const col = Math.floor((x - L.strip.x) / (cellW + 8));
            const row = Math.floor((y - L.strip.y) / (CELL_H + 6));
            const preset = col >= 0 && col < cols ? ui.presets[row * cols + col] : undefined;
            if (preset) applyPreset(this, preset);
            return true;
          }

          if (ui.hslOpen) {
            if (ui.hslTab.onPointerDown(x, y, L.hslTabs)) { this.setDirtyCanvas?.(true, true); return true; }
            const row = Math.floor((y - L.hslRows.y) / HSL_ROW_H);
            if (row >= 0 && row < HSL_BANDS.length && x >= L.hslRows.x && x <= L.hslRows.x + L.hslRows.w) {
              const bands = readHsl(this);
              const name = HSL_BANDS[row][0];
              const trackX = L.hslRows.x + 108;
              const trackW = L.hslRows.w - 148;
              const v = Math.max(-1, Math.min(1, ((x - trackX) / trackW) * 2 - 1));
              // Double-click resets the axis, matching every other control.
              (bands as any)[name][ui.hslTab.selected] = e?.detail === 2 ? 0 : Math.round(v * 100) / 100;
              writeHsl(this, bands);
              refreshPreview(this);
              return true;
            }
          }
          return false;
        });

        return r;
      };

      // Rebuild the preset strip whenever the node's input changes.
      const onExecuted = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function (this: NodeLike) {
        const res = onExecuted?.apply(this, arguments as any);
        const ui = uis.get(this);
        if (ui) {
          void loadSource(this, ui);
          void ui.preview.load(this.id, () => this.setDirtyCanvas?.(true, true));
        }
        return res;
      };

      // Any widget change reshapes the grade, so the preview must follow.
      const onWidgetChanged = nodeType.prototype.onWidgetChanged;
      nodeType.prototype.onWidgetChanged = function (this: NodeLike) {
        const res = onWidgetChanged?.apply(this, arguments as any);
        refreshPreview(this);
        this.setDirtyCanvas?.(true, true);
        return res;
      };
    },
  });
}

function drawStrip(ctx: Ctx, r: Rect, node: NodeLike, ui: LookUI): void {
  if (!ui.presets.length) {
    fillPanel(ctx, r, PW.color.well, M.radiusPanel, PW.color.border);
    text(ctx, 'Loading presets...', r.x + r.w / 2, r.y + r.h / 2, { colour: PW.color.textMute, align: 'center' });
    return;
  }

  const current = String(getWidget(node, 'preset')?.value ?? 'none');
  const { cols, cellW } = gridShape(r.w, ui.presets.length);
  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x, r.y, r.w, r.h);
  ctx.clip();

  ui.presets.forEach((p, i) => {
    const col = i % cols, row = Math.floor(i / cols);
    const x = r.x + col * (cellW + 8);
    const cell = { x, y: r.y + row * (CELL_H + 6), w: cellW, h: THUMB_H };
    const thumb = ui.thumbs.get(p.id);
    if (thumb) {
      ctx.save();
      fillPanel(ctx, cell, PW.color.well, M.radiusControl);
      ctx.clip();
      ctx.drawImage(thumb as CanvasImageSource, cell.x, cell.y, cell.w, cell.h);
      ctx.restore();
    } else {
      fillPanel(ctx, cell, PW.color.well, M.radiusControl);
      text(ctx, 'run once', cell.x + cell.w / 2, cell.y + cell.h / 2, {
        colour: PW.color.textMute, align: 'center',
      });
    }
    // Selected preset gets an accent frame, not a tint: tinting a thumbnail
    // whose entire job is showing colour would be self-defeating.
    ctx.strokeStyle = p.id === current ? PW.color.accent : PW.color.borderSoft;
    ctx.lineWidth = p.id === current ? 2 : 1;
    ctx.strokeRect(cell.x + 0.5, cell.y + 0.5, cell.w - 1, cell.h - 1);
    text(ctx, p.name, cell.x + cell.w / 2, cell.y + THUMB_H + 10, {
      colour: p.id === current ? PW.color.text : PW.color.textMute,
      align: 'center',
    });
  });
  ctx.restore();
}
