/**
 * PW Review — node wiring.
 *
 * The panel is the tray. Runs drop their images into it on the server and
 * finish; this draws what has collected, keeps the ratings on the server as
 * they change (so the next arrival does not wipe them), and on release queues
 * the one run that delivers the keepers.
 *
 * That run is queued at the front and aimed only at the output nodes
 * downstream of this one, so it touches nothing else in the graph. The node
 * itself asks for no new image on that run, which is what skips the sampler.
 *
 * auto_pass is reported to the server whenever it changes. ComfyUI copies
 * widget values into a run when it is queued, so without this, flipping the
 * switch would do nothing to runs already waiting in the queue.
 *
 * Hosted on the shared DOM panel, so it draws in both node designs. All
 * coordinates below are panel-local.
 */

import { fetchPw, postPw } from '../fetch.ts';
import { api, app, getWidget, type NodeLike } from '../comfy.ts';
import { PW } from '../theme.ts';
import { outputsDownstream, type NodeId } from '../core/reach.ts';
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
  count: number;
  ratings: number[];
  /** Keepers chosen and waiting for the run that delivers them. */
  pending: number;
  /** Changes whenever frames leave the tray, renumbering the rest. */
  epoch: number;
  focus: number;
  views: Map<number, HTMLImageElement>;
  thumbs: Map<number, HTMLImageElement>;
  /** What happened last, shown while the tray is empty. */
  note: string;
  /** The auto_pass value the server last heard from this panel. */
  sentAutoPass: boolean | null;
}

const uis = new WeakMap<object, ReviewUI>();

function blank(): ReviewUI {
  return {
    count: 0,
    ratings: [],
    pending: 0,
    epoch: -1,
    focus: 0,
    views: new Map(),
    thumbs: new Map(),
    note: '',
    sentAutoPass: null,
  };
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

/** The header's chips, right to left. Only drawn while there is a tray to act on. */
function chips(ctx: Ctx | null, header: Rect): { release: Rect; clear: Rect } {
  const release = headerChip(ctx, header, 'release');
  const clear = headerChip(ctx, { ...header, w: release.x - header.x - 6 }, 'clear');
  return { release, clear };
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

/** Fetch one frame of the tray and keep it. Cheap to call twice. */
async function loadFrame(node: NodeLike, ui: ReviewUI, kind: 'image' | 'thumb', index: number): Promise<void> {
  const into = kind === 'image' ? ui.views : ui.thumbs;
  if (into.has(index)) return;
  const epoch = ui.epoch;
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
    // Frames left while this was in flight, so index now names another frame.
    if (ui.epoch !== epoch) return;
    into.set(index, img);
    panelOf(node)?.invalidate();
  } catch {
    /* the tray changed underneath us; the next state fetch will say so */
  }
}

/** Tell the server what the switch says, if it has not heard it yet. */
function reportAutoPass(node: NodeLike, ui: ReviewUI): void {
  const value = !!getWidget(node, 'auto_pass')?.value;
  if (ui.sentAutoPass === value || node.id == null || Number(node.id) < 0) return;
  ui.sentAutoPass = value;
  postPw(`/pw_color/review/${node.id}/mode`, { auto_pass: value }).catch(() => {
    ui.sentAutoPass = null; // try again on the next change or sync
  });
}

/** Ask the server what this node's tray holds, and draw whatever it says. */
async function syncState(node: NodeLike, ui: ReviewUI): Promise<void> {
  reportAutoPass(node, ui);
  try {
    const res = await fetchPw(`/pw_color/review/${node.id}`);
    if (!res.ok) return;
    const state = await res.json();
    const epoch = state.epoch ?? 0;
    if (epoch !== ui.epoch) {
      // Frames left the tray, so every cached index may name another frame.
      ui.views.clear();
      ui.thumbs.clear();
      ui.focus = 0;
      ui.epoch = epoch;
    }
    const hadPending = ui.pending;
    ui.count = state.count ?? 0;
    ui.ratings = Array.isArray(state.ratings) ? state.ratings.slice() : [];
    ui.pending = state.pending ?? 0;
    if (hadPending && !ui.pending && ui.note.startsWith('sending')) ui.note = `sent ${hadPending}`;
    if (ui.focus >= ui.count) ui.focus = Math.max(0, ui.count - 1);
    const panel = panelOf(node);
    if (panel) fitNode(node, panel);
    if (ui.count) {
      void loadFrame(node, ui, 'image', ui.focus);
      for (let i = 0; i < ui.count; i++) void loadFrame(node, ui, 'thumb', i);
    }
    panel?.invalidate();
  } catch {
    /* offline; the next message or the next run brings us back */
  }
}

function saveRatings(node: NodeLike, ui: ReviewUI): void {
  postPw(`/pw_color/review/${node.id}/ratings`, { ratings: ui.ratings }).catch(() => {
    /* kept locally; the release sends them again anyway */
  });
}

/** The output nodes this node feeds, for aiming the delivery run. */
function deliveryTargets(node: NodeLike): string[] {
  const graph: any = (node as any).graph ?? app.graph;
  const byId = (id: NodeId) => graph?.getNodeById?.(id);
  const link = (id: number) => graph?.getLink?.(id) ?? graph?.links?.get?.(id) ?? graph?.links?.[id];
  try {
    return outputsDownstream(node.id, {
      next: (id) =>
        (byId(id)?.outputs ?? [])
          .flatMap((o: any) => o?.links ?? [])
          .map((l: number) => link(l)?.target_id)
          .filter((t: unknown) => t != null),
      isOutput: (id) => !!byId(id)?.constructor?.nodeData?.output_node,
      mode: (id) => byId(id)?.mode ?? 0,
    });
  } catch {
    return [];
  }
}

async function release(node: NodeLike, ui: ReviewUI): Promise<void> {
  if (!ui.count || ui.pending) return;
  try {
    const res = await postPw(`/pw_color/review/${node.id}/release`, { ratings: ui.ratings });
    if (!res.ok) {
      ui.note = 'the tray was already empty';
    } else {
      const { kept } = await res.json();
      if (!kept) {
        ui.note = 'nothing rated, so the tray was emptied';
      } else {
        ui.pending = kept;
        ui.note = `sending ${kept}`;
        // Aimed at this node's outputs when they can be found. A graph that
        // routes through frontend-only nodes may hide them; the whole graph
        // is then queued, and the node still skips the sampler.
        const targets = deliveryTargets(node);
        await app.queuePrompt(-1, 1, targets.length ? targets : undefined);
      }
    }
  } catch {
    ui.note = 'could not queue the delivery; the next run will carry it';
  }
  await syncState(node, ui);
}

async function clearTray(node: NodeLike, ui: ReviewUI): Promise<void> {
  try {
    await postPw(`/pw_color/review/${node.id}/clear`, {});
    ui.note = 'tray cleared';
  } catch {
    ui.note = 'could not reach the server';
  }
  await syncState(node, ui);
}

function idleText(node: NodeLike, ui: ReviewUI): string {
  if (ui.note) return ui.note;
  return getWidget(node, 'auto_pass')?.value
    ? 'auto_pass is on: images go straight through.'
    : 'The tray is empty. Each run adds its images here.';
}

export function registerReview(): void {
  app.registerExtension({
    name: 'pw.color.review',

    async setup() {
      // The node pushes this whenever its tray changes.
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
          onWidgetChange: () => reportAutoPass(this, ui),
          draw: (ctx, rr) => {
            const L = layout(rr.w);
            // No badge. The pack's badges say whether a panel's preview is
            // exact or approximate, and this panel is not a preview of
            // anything — it is the tray itself.
            const label = ui.pending
              ? `Review — sending ${ui.pending}`
              : ui.count
                ? `Review — ${ui.count} in tray, ${keptCount(ui.ratings)} kept`
                : 'Review';
            sectionHeader(ctx, label, L.header);
            if (ui.count && !ui.pending) chips(ctx, L.header);

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
              text(ctx, ui.count ? 'loading' : idleText(this, ui), L.view.x + L.view.w / 2, L.view.y + L.view.h / 2, {
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
            if (ui.count && !ui.pending) {
              const c = chips(panel.context, L.header);
              if (hit(c.release, x, y, 3)) {
                void release(this, ui);
                return true;
              }
              if (hit(c.clear, x, y, 3)) {
                void clearTray(this, ui);
                return true;
              }
            }
            if (!ui.count) return false;
            const cells = gridCells(L.strip, ui.count);
            const onStar = ui.pending ? null : starHit(x, y, cells, STAR_H);
            if (onStar) {
              ui.ratings[onStar.index] = nextRating(ui.ratings[onStar.index] ?? 0, onStar.star);
              saveRatings(this, ui);
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

        // After a page reload the tray is still on the server; find it again.
        // Deferred because the node has no id yet, and a saved workflow has
        // not applied its widget values yet.
        setTimeout(() => void syncState(this, ui), 0);

        return created;
      };
    },
  });
}
