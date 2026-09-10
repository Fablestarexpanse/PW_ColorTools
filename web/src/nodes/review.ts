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
 * The R after a frame's stars re-runs that frame: the server hands back the
 * prompt that made it with new seeds, and this queues it at the front. The
 * result lands in the tray right after the frame it re-ran.
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
import { STARS, clampView, keptCount, nextRating, rerunHit, starHit, viewHeight, type Cell } from '../core/stars.ts';
import { fillPanel, headerChip, hit, sectionHeader, text, type Ctx, type Rect } from '../widgets/draw.ts';
import { attachPanel, fitNode, panelOf, type Panel } from '../widgets/panel.ts';
import { addResetMenu } from '../widgets/reset.ts';

const M = PW.metrics;
const HEADER_H = 18;
const TOOLBAR_H = 20;
const VIEW_DEFAULT = 220;
/** The drag handle under the focus view. */
const HANDLE_H = 12;
const THUMB_H = 76;
const STAR_H = 16;
const CELL_H = THUMB_H + STAR_H;
const CELL_W = 96;
const CELL_GAP = 8;
const MIN_WIDTH = 420;
/** Where the handle's height is saved, so it comes back with the workflow. */
const VIEW_PROP = 'pw_view_h';

interface ReviewUI {
  count: number;
  ratings: number[];
  /** Which frames are re-runs of another, and which can be re-run. */
  reruns: boolean[];
  rerunnable: boolean[];
  /** Keepers chosen and waiting for the run that delivers them. */
  pending: number;
  /** Changes whenever frames are renumbered. */
  epoch: number;
  focus: number;
  views: Map<number, HTMLImageElement>;
  thumbs: Map<number, HTMLImageElement>;
  /** What happened last, shown while the tray is empty. */
  note: string;
  /** A short message in the header, cleared after a moment. */
  flash: string;
  flashTimer: ReturnType<typeof setTimeout> | null;
  /** The auto_pass value the server last heard from this panel. */
  sentAutoPass: boolean | null;
  /** A drag of the view handle in progress: the pointer, view and node at its start. */
  drag: { y: number; view: number; nodeH: number } | null;
}

const uis = new WeakMap<object, ReviewUI>();

function blank(): ReviewUI {
  return {
    count: 0,
    ratings: [],
    reruns: [],
    rerunnable: [],
    pending: 0,
    epoch: -1,
    focus: 0,
    views: new Map(),
    thumbs: new Map(),
    note: '',
    flash: '',
    flashTimer: null,
    sentAutoPass: null,
    drag: null,
  };
}

function baseView(node: NodeLike): number {
  const saved = Number((node as any).properties?.[VIEW_PROP]);
  return Number.isFinite(saved) && saved > 0 ? clampView(saved) : VIEW_DEFAULT;
}

function setBaseView(node: NodeLike, h: number): void {
  const n = node as any;
  n.properties ??= {};
  n.properties[VIEW_PROP] = clampView(h);
}

function columns(width: number): number {
  return Math.max(1, Math.floor((width + CELL_GAP) / (CELL_W + CELL_GAP)));
}

function rowCount(width: number, count: number): number {
  return Math.max(1, Math.ceil(Math.max(1, count) / columns(width)));
}

function stripHeight(w: number, count: number): number {
  const rows = rowCount(w, count);
  return rows * CELL_H + (rows - 1) * CELL_GAP;
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

/** Everything above and below the focus view, which does not stretch. */
function fixedHeight(w: number, ui: ReviewUI): number {
  return HEADER_H + 4 + TOOLBAR_H + 6 + HANDLE_H + stripHeight(w, ui.count) + M.padding;
}

function panelHeight(w: number, node: NodeLike, ui: ReviewUI): number {
  return fixedHeight(w, ui) + baseView(node);
}

interface Layout {
  header: Rect;
  toolbar: Rect;
  view: Rect;
  handle: Rect;
  strip: Rect;
}

/** `h` is the panel's real height: room beyond what it asked for goes to the view. */
function layout(w: number, h: number, node: NodeLike, ui: ReviewUI): Layout {
  const viewH = viewHeight(baseView(node), h - panelHeight(w, node, ui));
  let y = 0;
  const header = { x: 0, y, w, h: HEADER_H };
  y += HEADER_H + 4;
  const toolbar = { x: 0, y, w, h: TOOLBAR_H };
  y += TOOLBAR_H + 6;
  const view = { x: 0, y, w, h: viewH };
  y += viewH;
  const handle = { x: 0, y, w, h: HANDLE_H };
  y += HANDLE_H;
  return { header, toolbar, view, handle, strip: { x: 0, y, w, h: 0 } };
}

interface Tools {
  release: Rect;
  clear: Rect;
  sendAll: Rect;
  rateAll: Rect;
}

/** The toolbar's buttons: bulk actions on the left, release and clear on the right. */
function tools(ctx: Ctx | null, bar: Rect): Tools {
  const release = headerChip(ctx, bar, 'release');
  const clear = headerChip(ctx, { ...bar, w: release.x - bar.x - 6 }, 'clear');
  const measure = (s: string) => (ctx ? ((ctx.font = PW.font.body), ctx.measureText(s).width) : s.length * 6.2);
  const chipAt = (x: number, label: string): Rect => {
    const r = { x, y: bar.y + (bar.h - 16) / 2, w: measure(label) + 14, h: 16 };
    if (ctx) {
      fillPanel(ctx, r, PW.color.chip, M.radiusControl, PW.color.borderSoft);
      text(ctx, label, r.x + r.w / 2, bar.y + bar.h / 2, { colour: PW.color.textMute, align: 'center' });
    }
    return r;
  };
  const rateAll = chipAt(bar.x, 'rate all 5');
  const sendAll = chipAt(rateAll.x + rateAll.w + 6, 'send all');
  return { release, clear, sendAll, rateAll };
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

/** A small lettered square: the R button, and the badge on a re-run's thumbnail. */
function letter(ctx: Ctx, x: number, y: number, size: number, label: string, strong: boolean): void {
  const r = { x, y, w: size, h: size };
  fillPanel(ctx, r, strong ? PW.color.accent : PW.color.chip, M.radiusControl, PW.color.borderSoft);
  text(ctx, label, x + size / 2, y + size / 2, { colour: strong ? PW.color.well : PW.color.textMute, align: 'center' });
}

function flash(node: NodeLike, ui: ReviewUI, message: string): void {
  ui.flash = message;
  if (ui.flashTimer) clearTimeout(ui.flashTimer);
  ui.flashTimer = setTimeout(() => {
    ui.flash = '';
    panelOf(node)?.invalidate();
  }, 2500);
  panelOf(node)?.invalidate();
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
    // Frames were renumbered while this was in flight, so index names another.
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
      // Frames were renumbered, so every cached index may name another frame.
      ui.views.clear();
      ui.thumbs.clear();
      ui.focus = 0;
      ui.epoch = epoch;
    }
    const hadPending = ui.pending;
    ui.count = state.count ?? 0;
    ui.ratings = Array.isArray(state.ratings) ? state.ratings.slice() : [];
    ui.reruns = Array.isArray(state.reruns) ? state.reruns.slice() : [];
    ui.rerunnable = Array.isArray(state.rerunnable) ? state.rerunnable.slice() : [];
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

async function release(node: NodeLike, ui: ReviewUI, everything = false): Promise<void> {
  if (!ui.count || ui.pending) return;
  try {
    const res = await postPw(`/pw_color/review/${node.id}/release`, { ratings: ui.ratings, everything });
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

/** Queue the prompt that made frame `index` again, with new seeds, at the front. */
async function rerun(node: NodeLike, ui: ReviewUI, index: number): Promise<void> {
  try {
    const res = await postPw(`/pw_color/review/${node.id}/rerun/${index}`, {});
    if (!res.ok) {
      flash(node, ui, 'that frame cannot be re-run');
      return;
    }
    const job = await res.json();
    await api.queuePrompt(-1, { output: job.prompt, workflow: job.workflow });
    flash(node, ui, `re-run of #${index + 1} queued`);
  } catch {
    flash(node, ui, 'could not queue the re-run');
  }
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

/** Set the node's height, so dragging the handle moves what is below it. */
function setNodeHeight(node: NodeLike, h: number): void {
  node.size[1] = h;
  (node as any).setSize?.([node.size[0], h]);
  node.setDirtyCanvas?.(true, true);
}

function draw(node: NodeLike, ui: ReviewUI, ctx: Ctx, rr: Rect): void {
  const L = layout(rr.w, rr.h, node, ui);
  // No badge. The pack's badges say whether a panel's preview is exact or
  // approximate, and this panel is not a preview of anything — it is the tray.
  const label = ui.flash
    ? `Review — ${ui.flash}`
    : ui.pending
      ? `Review — sending ${ui.pending}`
      : ui.count
        ? `Review — ${ui.count} in tray, ${keptCount(ui.ratings)} kept`
        : 'Review';
  sectionHeader(ctx, label, L.header);
  if (ui.count && !ui.pending) tools(ctx, L.toolbar);

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
    text(ctx, ui.count ? 'loading' : idleText(node, ui), L.view.x + L.view.w / 2, L.view.y + L.view.h / 2, {
      colour: PW.color.textMute,
      align: 'center',
    });
  }

  // The handle: a short grip centred under the view.
  const gy = L.handle.y + L.handle.h / 2;
  ctx.strokeStyle = ui.drag ? PW.color.accent : PW.color.textMute;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (const dy of [-2, 2]) {
    ctx.moveTo(L.handle.w / 2 - 18, gy + dy);
    ctx.lineTo(L.handle.w / 2 + 18, gy + dy);
  }
  ctx.stroke();

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
    if (ui.reruns[i]) letter(ctx, c.x + 3, c.y + 3, 14, 'R', true);

    const rating = ui.ratings[i] ?? 0;
    for (let s = 1; s <= STARS; s++) {
      star(ctx, c.x + (s - 0.5) * STAR_H, c.y + THUMB_H + STAR_H / 2, STAR_H * 0.4, s <= rating);
    }
    if (ui.rerunnable[i]) letter(ctx, c.x + STARS * STAR_H + 1, c.y + THUMB_H + 1, STAR_H - 2, 'R', false);
  });
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
          height: (w) => panelHeight(w, this, ui),
          onWidgetChange: () => reportAutoPass(this, ui),
          draw: (ctx, rr) => draw(this, ui, ctx, rr),
          onPointerDown: (x, y) => {
            const L = layout(panel.width, panel.height, this, ui);
            if (hit(L.handle, x, y, 2)) {
              // Start from the height as drawn, so a node already dragged tall
              // does not jump when the handle is first grabbed.
              ui.drag = { y, view: L.view.h, nodeH: this.size[1] };
              return true;
            }
            if (ui.count && !ui.pending) {
              const t = tools(panel.context, L.toolbar);
              if (hit(t.release, x, y, 3)) {
                void release(this, ui);
                return true;
              }
              if (hit(t.clear, x, y, 3)) {
                void clearTray(this, ui);
                return true;
              }
              if (hit(t.sendAll, x, y, 3)) {
                void release(this, ui, true);
                return true;
              }
              if (hit(t.rateAll, x, y, 3)) {
                ui.ratings = Array.from({ length: ui.count }, () => STARS);
                saveRatings(this, ui);
                return true;
              }
            }
            if (!ui.count) return false;
            const cells = gridCells(L.strip, ui.count);
            if (!ui.pending) {
              const again = rerunHit(x, y, cells, STAR_H);
              if (again != null && ui.rerunnable[again]) {
                void rerun(this, ui, again);
                return true;
              }
              const onStar = starHit(x, y, cells, STAR_H);
              if (onStar) {
                ui.ratings[onStar.index] = nextRating(ui.ratings[onStar.index] ?? 0, onStar.star);
                saveRatings(this, ui);
                return true;
              }
            }
            const picked = cells.findIndex((c) => hit({ x: c.x, y: c.y, w: c.w, h: THUMB_H }, x, y));
            if (picked >= 0) {
              ui.focus = picked;
              void loadFrame(this, ui, 'image', picked);
              return true;
            }
            return false;
          },
          onPointerMove: (_x, y) => {
            if (!ui.drag) return false;
            setBaseView(this, ui.drag.view + (y - ui.drag.y));
            // The node changes by exactly what the view did, measured from the
            // start of the drag rather than from the panel's size, which the
            // host reports a frame late.
            setNodeHeight(this, ui.drag.nodeH + baseView(this) - ui.drag.view);
            return true;
          },
          onPointerUp: () => {
            if (!ui.drag) return false;
            ui.drag = null;
            return true;
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
