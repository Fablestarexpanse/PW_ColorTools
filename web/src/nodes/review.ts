/**
 * PW Review — node wiring.
 *
 * The panel *is* the node. Without it a held run has no way to continue
 * except being cancelled, so this draws the batch, takes the ratings and
 * posts them back.
 *
 * State arrives two ways. A websocket message wakes the panel when a run
 * reaches the node, and the panel asks the server for the hold when it is
 * created — which is what makes reloading the page mid-hold recover the batch
 * rather than stranding the run.
 *
 * Hosted on the shared DOM panel, so it draws in both node designs. All
 * coordinates below are panel-local.
 */

import { fetchPw, postPw } from '../fetch.ts';
import { api, app, type NodeLike } from '../comfy.ts';
import { BADGE, PW } from '../theme.ts';
import { STARS, keptCount, nextRating, starHit, type Cell } from '../core/stars.ts';
import { fillPanel, headerChip, hit, sectionHeader, text, type Ctx, type Rect } from '../widgets/draw.ts';
import { attachPanel, fitNode, panelOf, type Panel } from '../widgets/panel.ts';
import { addResetMenu } from '../widgets/reset.ts';

const M = PW.metrics;
const HEADER_H = 18;
const VIEW_H = 220;
const THUMB_H = 76;
const STAR_H = 16;
const CELL_H = THUMB_H + STAR_H;
const CELL_W = 96;
const CELL_GAP = 8;
const MIN_WIDTH = 420;

interface ReviewUI {
  holding: boolean;
  count: number;
  ratings: number[];
  focus: number;
  views: Map<number, HTMLImageElement>;
  thumbs: Map<number, HTMLImageElement>;
  /** What happened last, shown once the batch has gone. */
  note: string;
}

const uis = new WeakMap<object, ReviewUI>();

function blank(): ReviewUI {
  return { holding: false, count: 0, ratings: [], focus: 0, views: new Map(), thumbs: new Map(), note: '' };
}

function columns(width: number): number {
  return Math.max(1, Math.floor((width + CELL_GAP) / (CELL_W + CELL_GAP)));
}

function rowCount(width: number, count: number): number {
  return Math.max(1, Math.ceil(Math.max(1, count) / columns(width)));
}

function gridCells(strip: Rect, count: number): Cell[] {
  const cols = columns(strip.w);
  return Array.from({ length: count }, (_, i) => ({
    x: strip.x + (i % cols) * (CELL_W + CELL_GAP),
    y: strip.y + Math.floor(i / cols) * (CELL_H + CELL_GAP),
    w: CELL_W,
    h: CELL_H,
  }));
}

/** The strip's height comes from `panelHeight`; the cells place themselves. */
function layout(w: number): { header: Rect; view: Rect; strip: Rect } {
  let y = 0;
  const header = { x: 0, y, w, h: HEADER_H };
  y += HEADER_H + 6;
  const view = { x: 0, y, w, h: VIEW_H };
  y += VIEW_H + M.gapSection;
  return { header, view, strip: { x: 0, y, w, h: 0 } };
}

function panelHeight(w: number, ui: ReviewUI): number {
  const rows = rowCount(w, ui.count);
  return HEADER_H + 6 + VIEW_H + M.gapSection + rows * CELL_H + (rows - 1) * CELL_GAP + M.padding;
}

/**
 * A five-pointed star, drawn rather than typed.
 *
 * A font without U+2605 draws a box, and at this size a box and a star are
 * the difference between a rating control and a puzzle. The pack owns its own
 * drawing everywhere else too.
 */
function star(ctx: Ctx, cx: number, cy: number, radius: number, filled: boolean): void {
  ctx.beginPath();
  for (let i = 0; i < 10; i++) {
    const r = i % 2 === 0 ? radius : radius * 0.45;
    const a = -Math.PI / 2 + (i * Math.PI) / 5;
    const x = cx + Math.cos(a) * r;
    const y = cy + Math.sin(a) * r;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.closePath();
  if (filled) {
    ctx.fillStyle = PW.color.accent;
    ctx.fill();
  } else {
    ctx.strokeStyle = PW.color.textMute;
    ctx.lineWidth = PW.metrics.border;
    ctx.stroke();
  }
}

/** Fetch one frame of the held batch and keep it. Cheap to call twice. */
async function loadFrame(node: NodeLike, ui: ReviewUI, kind: 'image' | 'thumb', index: number): Promise<void> {
  const into = kind === 'image' ? ui.views : ui.thumbs;
  if (into.has(index)) return;
  try {
    const res = await fetchPw(`/pw_color/review/${node.id}/${kind}/${index}`);
    if (!res.ok) return;
    const img = new Image();
    const url = URL.createObjectURL(await res.blob());
    await new Promise<void>((done) => {
      img.onload = () => done();
      img.onerror = () => done();
      img.src = url;
    });
    URL.revokeObjectURL(url);
    into.set(index, img);
    panelOf(node)?.invalidate();
  } catch {
    /* the hold went away underneath us; the next state fetch will say so */
  }
}

/** Ask the server what this node is holding, and draw whatever it says. */
async function syncState(node: NodeLike, ui: ReviewUI): Promise<void> {
  try {
    const res = await fetchPw(`/pw_color/review/${node.id}`);
    if (!res.ok) return;
    const state = await res.json();
    const arrived = !ui.holding && !!state.holding;
    ui.holding = !!state.holding;
    ui.count = state.count ?? 0;
    ui.ratings = Array.isArray(state.ratings) ? state.ratings.slice() : [];
    if (arrived) {
      // A new batch, so nothing decoded for the last one is worth keeping.
      ui.views.clear();
      ui.thumbs.clear();
      ui.focus = 0;
      ui.note = '';
    }
    const panel = panelOf(node);
    if (panel) fitNode(node, panel);
    if (ui.holding) {
      void loadFrame(node, ui, 'image', ui.focus);
      for (let i = 0; i < ui.count; i++) void loadFrame(node, ui, 'thumb', i);
    }
    panel?.invalidate();
  } catch {
    /* offline; the next message or the next run brings us back */
  }
}

async function releaseHold(node: NodeLike, ui: ReviewUI): Promise<void> {
  if (!ui.holding) return;
  const kept = keptCount(ui.ratings);
  try {
    const res = await postPw(`/pw_color/review/${node.id}/release`, { ratings: ui.ratings });
    ui.note = res.ok
      ? kept
        ? `sent ${kept} of ${ui.count}`
        : 'nothing kept'
      : 'that batch is no longer held';
  } catch {
    ui.note = 'could not reach the server';
  }
  ui.holding = false;
  ui.views.clear();
  ui.thumbs.clear();
  panelOf(node)?.invalidate();
}

export function registerReview(): void {
  app.registerExtension({
    name: 'pw.color.review',

    async setup() {
      // The node pushes this when a run reaches it, and again when it lets go.
      api.addEventListener('pw_color.review', (e: any) => {
        const node = app.graph?.getNodeById?.(e?.detail?.node_id);
        const ui = node ? uis.get(node) : undefined;
        if (node && ui) void syncState(node, ui);
      });
    },

    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Review') return;
      addResetMenu(nodeType);

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const created = onCreated?.apply(this, arguments as any);
        const ui = blank();
        uis.set(this, ui);

        const panel: Panel = attachPanel(this, {
          minWidth: MIN_WIDTH,
          height: (w) => panelHeight(w, ui),
          draw: (ctx, rr) => {
            const L = layout(rr.w);
            const label = ui.holding ? `Review — ${ui.count} held, ${keptCount(ui.ratings)} kept` : 'Review';
            sectionHeader(ctx, label, L.header, BADGE.render);
            if (ui.holding) headerChip(ctx, L.header, 'release', BADGE.render.label);

            fillPanel(ctx, L.view, PW.color.well, M.radiusPanel, PW.color.border);
            const focus = ui.views.get(ui.focus);
            if (focus) {
              ctx.save();
              fillPanel(ctx, L.view, PW.color.well, M.radiusPanel);
              ctx.clip();
              const s = Math.min(L.view.w / focus.width, L.view.h / focus.height);
              const w = focus.width * s;
              const h = focus.height * s;
              ctx.drawImage(focus, L.view.x + (L.view.w - w) / 2, L.view.y + (L.view.h - h) / 2, w, h);
              ctx.restore();
            } else {
              const idle = ui.holding ? 'loading' : ui.note || 'Nothing held. Run the graph.';
              text(ctx, idle, L.view.x + L.view.w / 2, L.view.y + L.view.h / 2, {
                colour: PW.color.textMute,
                align: 'center',
              });
            }

            gridCells(L.strip, ui.count).forEach((c, i) => {
              const cell = { x: c.x, y: c.y, w: c.w, h: THUMB_H };
              fillPanel(ctx, cell, PW.color.well, M.radiusControl);
              const thumb = ui.thumbs.get(i);
              if (thumb) {
                ctx.save();
                fillPanel(ctx, cell, PW.color.well, M.radiusControl);
                ctx.clip();
                // Cover-fit: a letterboxed thumbnail wastes what little there is.
                const s = Math.max(cell.w / thumb.width, THUMB_H / thumb.height);
                const w = thumb.width * s;
                const h = thumb.height * s;
                ctx.drawImage(thumb, c.x + (cell.w - w) / 2, c.y + (THUMB_H - h) / 2, w, h);
                ctx.restore();
              }
              const focused = i === ui.focus;
              ctx.strokeStyle = focused ? PW.color.accent : PW.color.borderSoft;
              ctx.lineWidth = focused ? 2 : 1;
              ctx.strokeRect(c.x + 0.5, c.y + 0.5, cell.w - 1, THUMB_H - 1);

              const rating = ui.ratings[i] ?? 0;
              for (let s = 1; s <= STARS; s++) {
                star(ctx, c.x + (s - 0.5) * STAR_H, c.y + THUMB_H + STAR_H / 2, STAR_H * 0.4, s <= rating);
              }
            });
          },
          onPointerDown: (x, y) => {
            const L = layout(panel.width);
            if (ui.holding && hit(headerChip(panel.context, L.header, 'release', BADGE.render.label), x, y, 3)) {
              void releaseHold(this, ui);
              return true;
            }
            if (!ui.holding) return false;
            const cells = gridCells(L.strip, ui.count);
            const onStar = starHit(x, y, cells, STAR_H);
            if (onStar) {
              ui.ratings[onStar.index] = nextRating(ui.ratings[onStar.index] ?? 0, onStar.star);
              return true;
            }
            const picked = cells.findIndex((c) => hit({ x: c.x, y: c.y, w: c.w, h: THUMB_H }, x, y));
            if (picked >= 0) {
              ui.focus = picked;
              void loadFrame(this, ui, 'image', picked);
              return true;
            }
            return false;
          },
        });

        // A page reloaded while a run is held has to find it again.
        setTimeout(() => void syncState(this, ui), 0);

        return created;
      };
    },
  });
}
