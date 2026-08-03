/**
 * PW Palette — node wiring.
 *
 * Draws the extracted palette in the node: colour block, hex, coverage bar and
 * percentage. Click a swatch to copy its hex; click the header action to lock
 * the current palette as a target so it stops re-extracting.
 *
 * The palette comes from the node's own execution result rather than being
 * recomputed here — k-means in the browser would be a third implementation of
 * maths we already have twice, and unlike the lattice there is no interactive
 * reason to want it client-side.
 */

import { api, app, chainHandler, getWidget, type NodeLike } from '../comfy.ts';
import { PW } from '../theme.ts';
import { fillPanel, hit, roundRect, text, type Ctx, type Rect } from '../widgets/draw.ts';
import { fitPanel } from '../widgets/layout.ts';

const M = PW.metrics;
const STRIP_H = 92;
const BLOCK_H = 44;
/** Header + strip + the saved-path hint line, plus breathing room. */
const PANEL_BLOCK = STRIP_H + 22 + M.gapSection + M.padding;

interface Swatch {
  hex: string;
  oklab: [number, number, number];
  coverage: number;
}

interface PaletteData {
  schema: number;
  colors: Swatch[];
  source_hash: string;
  sort: string;
}

const palettes = new WeakMap<object, PaletteData>();
const toasts = new WeakMap<object, { text: string; until: number }>();
const saved = new WeakMap<object, string>();

function swatchRects(r: Rect, n: number): Rect[] {
  if (n === 0) return [];
  const gap = 4;
  const w = (r.w - gap * (n - 1)) / n;
  return Array.from({ length: n }, (_, i) => ({ x: r.x + i * (w + gap), y: r.y, w, h: r.h }));
}

function drawPalette(ctx: Ctx, r: Rect, node: NodeLike): void {
  const data = palettes.get(node);
  if (!data || data.colors.length === 0) {
    fillPanel(ctx, r, PW.color.well, M.radiusPanel, PW.color.border);
    text(ctx, 'Run the graph to extract a palette', r.x + r.w / 2, r.y + r.h / 2, {
      colour: PW.color.textMute,
      align: 'center',
    });
    return;
  }

  const cells = swatchRects(r, data.colors.length);
  const peak = Math.max(...data.colors.map((c) => c.coverage), 1e-6);

  data.colors.forEach((sw, i) => {
    const c = cells[i];
    // Colour block.
    roundRect(ctx, { x: c.x, y: c.y, w: c.w, h: BLOCK_H }, M.radiusControl);
    ctx.fillStyle = sw.hex;
    ctx.fill();
    ctx.strokeStyle = PW.color.border;
    ctx.lineWidth = M.borderHair;
    ctx.stroke();

    // Hex, centred under the block. Dropped when the cell is too narrow to
    // hold it — a clipped hex is worse than none.
    ctx.font = PW.font.mono;
    if (ctx.measureText(sw.hex).width <= c.w - 2) {
      text(ctx, sw.hex, c.x + c.w / 2, c.y + BLOCK_H + 12, {
        colour: PW.color.textDim,
        align: 'center',
        font: PW.font.mono,
      });
    }

    // Coverage bar, relative to the largest swatch rather than to 100%, or a
    // palette where nothing exceeds 30% renders as slivers.
    const barY = c.y + BLOCK_H + 22;
    fillPanel(ctx, { x: c.x, y: barY, w: c.w, h: 4 }, PW.color.well, 2);
    fillPanel(ctx, { x: c.x, y: barY, w: c.w * (sw.coverage / peak), h: 4 }, PW.color.accent, 2);

    text(ctx, `${Math.round(sw.coverage * 100)}%`, c.x + c.w / 2, barY + 14, {
      colour: PW.color.textMute,
      align: 'center',
      font: PW.font.mono,
    });
  });
}

/**
 * Header chips, laid out right to left. Measured with the real context rather
 * than estimated, and the same function is used for drawing and for hit
 * testing, so the two cannot disagree.
 */
function headerChips(ctx: Ctx, r: Rect, node: NodeLike): { lock: Rect; exp: Rect; lockLabel: string } {
  const locked = !!String(getWidget(node, 'locked')?.value ?? '').trim();
  const lockLabel = locked ? 'unlock' : 'lock as target';
  ctx.font = PW.font.body;
  const lockW = ctx.measureText(lockLabel).width + 14;
  const expW = ctx.measureText('export').width + 14;
  const y = r.y + (r.h - 18) / 2;
  return {
    lock: { x: r.x + r.w - lockW, y, w: lockW, h: 18 },
    exp: { x: r.x + r.w - lockW - expW - 6, y, w: expW, h: 18 },
    lockLabel,
  };
}

function drawHeader(ctx: Ctx, r: Rect, node: NodeLike): void {
  const locked = !!String(getWidget(node, 'locked')?.value ?? '').trim();
  text(ctx, locked ? 'Palette (locked)' : 'Palette', r.x, r.y + r.h / 2, {
    colour: locked ? PW.color.accent : PW.color.textDim,
  });

  const { lock, exp, lockLabel } = headerChips(ctx, r, node);
  const hasPalette = !!palettes.get(node);

  fillPanel(ctx, exp, PW.color.chip, M.radiusControl, PW.color.borderSoft);
  text(ctx, 'export', exp.x + exp.w / 2, r.y + r.h / 2, {
    colour: hasPalette ? PW.color.textMute : PW.color.borderSoft,
    align: 'center',
  });

  fillPanel(ctx, lock, locked ? PW.color.chipActive : PW.color.chip, M.radiusControl, PW.color.borderSoft);
  text(ctx, lockLabel, lock.x + lock.w / 2, r.y + r.h / 2, {
    colour: locked ? PW.color.text : PW.color.textMute,
    align: 'center',
  });
}

function layout(node: NodeLike) {
  const x = M.padding;
  const w = node.size[0] - M.padding * 2;
  const y = node.size[1] - STRIP_H - M.padding - 22;
  return { header: { x, y, w, h: 18 }, strip: { x, y: y + 22, w, h: STRIP_H } };
}

// -- export ------------------------------------------------------------------

function toGpl(data: PaletteData, name: string): string {
  const lines = ['GIMP Palette', `Name: ${name}`, `Columns: ${Math.min(data.colors.length, 8)}`, '#'];
  for (const sw of data.colors) {
    const r = parseInt(sw.hex.slice(1, 3), 16);
    const g = parseInt(sw.hex.slice(3, 5), 16);
    const b = parseInt(sw.hex.slice(5, 7), 16);
    lines.push(`${String(r).padStart(3)} ${String(g).padStart(3)} ${String(b).padStart(3)}\t${sw.hex}`);
  }
  return lines.join('\n') + '\n';
}

/**
 * Adobe Swatch Exchange. Mirrors `Palette.to_ase_bytes` in Python — big-endian
 * throughout, UTF-16BE names, RGB float triples.
 */
function toAse(data: PaletteData): Blob {
  const blocks: ArrayBuffer[] = [];
  for (const sw of data.colors) {
    const name = sw.hex + '\0';
    const bodyLen = 2 + name.length * 2 + 4 + 12 + 2;
    const buf = new ArrayBuffer(6 + bodyLen);
    const view = new DataView(buf);
    let o = 0;
    view.setUint16(o, 0x0001); o += 2;
    view.setUint32(o, bodyLen); o += 4;
    view.setUint16(o, name.length); o += 2;
    for (let i = 0; i < name.length; i++) { view.setUint16(o, name.charCodeAt(i)); o += 2; }
    for (const ch of 'RGB ') { view.setUint8(o, ch.charCodeAt(0)); o += 1; }
    for (let i = 0; i < 3; i++) {
      view.setFloat32(o, parseInt(sw.hex.slice(1 + i * 2, 3 + i * 2), 16) / 255);
      o += 4;
    }
    view.setUint16(o, 0);
    blocks.push(buf);
  }
  const head = new ArrayBuffer(12);
  const hv = new DataView(head);
  for (let i = 0; i < 4; i++) hv.setUint8(i, 'ASEF'.charCodeAt(i));
  hv.setUint16(4, 1);
  hv.setUint16(6, 0);
  hv.setUint32(8, data.colors.length);
  return new Blob([head, ...blocks], { type: 'application/octet-stream' });
}

function download(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoke on the next tick: revoking synchronously can beat the download in
  // some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/**
 * Export straight from the browser, with no execution needed.
 *
 * The node can also write to `output/palettes` on run, which is the right thing
 * for a repeatable workflow. This is for the other case: you liked what you
 * just saw and want the file now.
 */
function exportPalette(node: NodeLike, format: string): void {
  const data = palettes.get(node);
  if (!data) return;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  const name = `pw-palette-${stamp}`;

  if (format === 'ase') {
    download(toAse(data), `${name}.ase`);
  } else if (format === 'gpl') {
    download(new Blob([toGpl(data, name)], { type: 'text/plain' }), `${name}.gpl`);
  } else if (format === 'txt') {
    download(new Blob([data.colors.map((c) => c.hex).join('\n') + '\n'], { type: 'text/plain' }), `${name}.txt`);
  } else if (format === 'css') {
    const css = `:root {\n${data.colors.map((c, i) => `  --palette-${i + 1}: ${c.hex};`).join('\n')}\n}\n`;
    download(new Blob([css], { type: 'text/css' }), `${name}.css`);
  } else {
    download(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }), `${name}.json`);
  }
  toast(node, `exported .${format}`);
}

const EXPORT_FORMATS: { id: string; label: string }[] = [
  { id: 'json', label: 'PW palette (.json) - reopens here' },
  { id: 'ase', label: 'Adobe (.ase) - Photoshop, Illustrator' },
  { id: 'gpl', label: 'GIMP (.gpl) - GIMP, Krita, Inkscape' },
  { id: 'txt', label: 'Hex list (.txt)' },
  { id: 'css', label: 'CSS variables (.css)' },
];

function toast(node: NodeLike, message: string): void {
  toasts.set(node, { text: message, until: performance.now() + 1400 });
  node.setDirtyCanvas?.(true, true);
}

async function copyHex(node: NodeLike, hex: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(hex);
    toast(node, `copied ${hex}`);
  } catch {
    // Clipboard needs a secure context; ComfyUI over plain http on a LAN
    // address does not get one. Say so rather than failing silently.
    toast(node, 'clipboard blocked (needs https)');
  }
}

export function registerPalette(): void {
  app.registerExtension({
    name: 'pw.color.palette',
    async setup() {
      // The node reports its palette through the standard execution message,
      // so we pick it up from there rather than inventing a channel.
      api.addEventListener('executed', (e: any) => {
        const detail = e?.detail;
        const node = app.graph?.getNodeById?.(detail?.node);
        if (!node || node.type !== 'PW_Palette') return;
        const raw = detail?.output?.pw_palette?.[0];
        if (!raw) return;
        try {
          palettes.set(node, typeof raw === 'string' ? JSON.parse(raw) : raw);
          const path = detail?.output?.pw_saved?.[0];
          if (path) saved.set(node, String(path));
          else saved.delete(node);
          node.setDirtyCanvas?.(true, true);
        } catch {
          /* malformed payload — leave the previous palette on screen */
        }
      });
    },

    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Palette') return;

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const r = onCreated?.apply(this, arguments as any);

        const lockedWidget = getWidget(this, 'locked');
        if (lockedWidget) {
          lockedWidget.type = 'hidden';
          lockedWidget.computeSize = () => [0, -4];
        }

        // Size from LiteGraph's own widget measurement plus the panel we draw,
        // rather than from a guessed constant. Guessing means the panel
        // collides with the widgets the moment an input is added — which is
        // exactly what happened when save/load landed.
        fitPanel(this, PANEL_BLOCK, 360);

        chainHandler(this, 'onDrawForeground', function (this: NodeLike, ctx: Ctx) {
          if ((this as any).flags?.collapsed) return;
          const L = layout(this);
          drawHeader(ctx, L.header, this);
          drawPalette(ctx, L.strip, this);
          drawSavedHint(ctx, L.strip, this);

          const t = toasts.get(this);
          if (t && performance.now() < t.until) {
            text(ctx, t.text, L.header.x + L.header.w / 2, L.strip.y + L.strip.h + 12, {
              colour: PW.color.accent,
              align: 'center',
            });
          }
        });

        chainHandler(this, 'onMouseDown', function (this: NodeLike, e: any, pos: [number, number]) {
          const L = layout(this);
          const [x, y] = pos;
          const ctx = (app as any).canvas?.ctx;
          if (!ctx) return false;
          const { lock, exp } = headerChips(ctx, L.header, this);

          if (hit(exp, x, y)) {
            if (!palettes.get(this)) {
              toast(this, 'nothing to export — run the graph first');
              return true;
            }
            // A menu rather than a fixed format: which file you want depends
            // entirely on where the palette is going.
            new (globalThis as any).LiteGraph.ContextMenu(
              EXPORT_FORMATS.map((f) => ({ content: f.label, callback: () => exportPalette(this, f.id) })),
              { event: e, title: 'Export palette' },
            );
            return true;
          }

          if (hit(lock, x, y)) {
            const w = getWidget(this, 'locked');
            if (!w) return true;
            if (String(w.value ?? '').trim()) {
              w.value = '';
              toast(this, 'unlocked');
            } else {
              const data = palettes.get(this);
              if (!data) {
                toast(this, 'nothing to lock — run the graph first');
                return true;
              }
              w.value = JSON.stringify(data);
              toast(this, 'locked');
            }
            w.callback?.(w.value);
            return true;
          }

          const data = palettes.get(this);
          if (data && y >= L.strip.y && y <= L.strip.y + BLOCK_H) {
            const cells = swatchRects(L.strip, data.colors.length);
            const i = cells.findIndex((c) => x >= c.x && x <= c.x + c.w);
            if (i >= 0) {
              void copyHex(this, data.colors[i].hex);
              return true;
            }
          }
          return false;
        });

        return r;
      };
    },
  });
}

/** Confirm where the last run wrote a file, so 'save_as' is not a leap of faith. */
function drawSavedHint(ctx: Ctx, strip: Rect, node: NodeLike): void {
  const path = saved.get(node);
  if (!path) return;
  const name = path.split(/[\\/]/).pop() ?? path;
  text(ctx, `saved ${name}`, strip.x, strip.y + strip.h + 12, { colour: PW.color.textMute });
}
