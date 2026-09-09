/**
 * The panel host: one DOM widget per node, drawing with the pack's own canvas
 * code.
 *
 * Until 2.1 every panel was painted from `onDrawForeground` on the LiteGraph
 * canvas. ComfyUI's Modern Node Design (frontend 1.49+, opt-in) renders nodes
 * as DOM and never calls that hook, so the pack went blank under it. A DOM
 * widget is the one surface both renderers show, so the panel lives on one:
 * a `<canvas>` inside `node.addDOMWidget`, and the existing drawing classes
 * draw into it in local coordinates. One code path, both designs.
 *
 * Measured on frontend 1.49.6 (see the spec in `docs/superpowers/specs`):
 *
 * - In Modern, every DOM widget with a `computeLayoutSize` function gets a grid
 *   row of `auto`, and all such rows share the node's leftover height. So there
 *   is exactly one container per node, appended last, and it takes the space.
 * - Wheel is forwarded to the graph unless the target sits inside
 *   `[data-capture-wheel="true"]` that contains the focused element. The
 *   canvas is focusable and takes focus on enter and press.
 * - `{serialize:false}` in the options does not stop serialisation; the
 *   property on the widget does.
 *
 * `Panel` itself is host-free so node can test the parts that have to be
 * right for every node at once; only `attachPanel` touches the DOM.
 */

import { collapseInternalPreview, fitPanel } from './layout.ts';
import type { Ctx, Rect } from './draw.ts';
import type { NodeLike } from '../comfy.ts';
import { PW } from '../theme.ts';

export interface PointerMods {
  shift: boolean;
  /** Second press of a double click. */
  double: boolean;
  /** Event timestamp, for the curve editor's own double-click detection. */
  time: number;
  /** The DOM event, for the one caller that needs it: LiteGraph's context menu. */
  event?: Event;
}

export interface PanelSpec {
  /** Minimum node width. */
  minWidth: number;
  /** Height the panel wants for a given content width. */
  height: (width: number) => number;
  /** Paint. `r` is `{0, 0, width, height}`; everything is local. */
  draw: (ctx: Ctx, r: Rect) => void;
  /** Return true to consume the event and schedule a repaint. */
  onPointerDown?: (x: number, y: number, m: PointerMods) => boolean;
  onPointerMove?: (x: number, y: number, m: PointerMods) => boolean;
  onPointerUp?: (x: number, y: number, m: PointerMods) => boolean;
  onWheel?: (x: number, y: number, delta: number) => boolean;
}

/** What `Panel` needs of a canvas: enough for a stand-in under node. */
export interface CanvasLike {
  width: number;
  height: number;
  getContext(kind: '2d'): Ctx | null;
  getBoundingClientRect(): { left: number; top: number; width: number; height: number };
}

export interface PanelEnv {
  dpr: () => number;
  /** Run `fn` before the next paint. `requestAnimationFrame` in the browser. */
  schedule: (fn: () => void) => void;
}

export class Panel {
  readonly spec: PanelSpec;
  /**
   * Graph zoom. The element's CSS box is scaled by the host's transform, so
   * client coordinates come in scaled; dividing puts them back in node units,
   * which is what every layout function in the pack works in.
   */
  scale = 1;
  /**
   * Height this panel has added to its node beyond what the node had, and
   * may therefore take back when the panel turns out taller than it asked
   * for. A user's own resize never counts, so it is never removed.
   */
  grown = 0;

  private readonly canvas: CanvasLike;
  private readonly env: PanelEnv;
  private readonly ctx: Ctx;
  private w = 0;
  private h = 0;
  /** Backing pixels per node unit, fixed at the last resize. */
  private dpr = 1;
  private pending = false;
  private disposed = false;

  constructor(canvas: CanvasLike, spec: PanelSpec, env: PanelEnv) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('PW Color panel: no 2D context');
    this.canvas = canvas;
    this.spec = spec;
    this.env = env;
    this.ctx = ctx;
  }

  /** Content width in node units. */
  get width(): number {
    return this.w;
  }

  get height(): number {
    return this.h;
  }

  /** The live context, so hit tests can measure text exactly as drawing did. */
  get context(): Ctx {
    return this.ctx;
  }

  /** Backing pixels per node unit in use. */
  get density(): number {
    return this.dpr;
  }

  /** Set the drawing size in node units and repaint now. */
  resize(width: number, height: number): void {
    this.w = Math.max(0, Math.floor(width));
    this.h = Math.max(0, Math.floor(height));
    this.dpr = this.env.dpr();
    this.canvas.width = Math.round(this.w * this.dpr);
    this.canvas.height = Math.round(this.h * this.dpr);
    this.draw();
  }

  draw(): void {
    if (this.disposed || this.w === 0 || this.h === 0) return;
    // The transform must match the backing store set at resize, not whatever
    // the density is now; a zoom between the two would otherwise skew it.
    const dpr = this.dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.w, this.h);
    this.spec.draw(this.ctx, { x: 0, y: 0, w: this.w, h: this.h });
  }

  /** Repaint before the next frame. A burst of calls costs one draw. */
  invalidate(): void {
    if (this.pending || this.disposed) return;
    this.pending = true;
    this.env.schedule(() => {
      this.pending = false;
      this.draw();
    });
  }

  dispose(): void {
    this.disposed = true;
  }

  private local(cx: number, cy: number): [number, number] {
    const r = this.canvas.getBoundingClientRect();
    return [(cx - r.left) / this.scale, (cy - r.top) / this.scale];
  }

  private route(handled: boolean): boolean {
    if (handled) this.invalidate();
    return handled;
  }

  pointerDown(cx: number, cy: number, m: PointerMods): boolean {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onPointerDown?.(x, y, m) ?? false);
  }

  pointerMove(cx: number, cy: number, m: PointerMods): boolean {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onPointerMove?.(x, y, m) ?? false);
  }

  pointerUp(cx: number, cy: number, m: PointerMods): boolean {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onPointerUp?.(x, y, m) ?? false);
  }

  wheel(cx: number, cy: number, delta: number): boolean {
    const [x, y] = this.local(cx, cy);
    return this.route(this.spec.onWheel?.(x, y, delta) ?? false);
  }
}

// -- DOM wiring ---------------------------------------------------------------

const WIDGET_NAME = 'pw_panel';
const panels = new WeakMap<object, Panel>();

/** The panel attached to a node, for callers that only have the node. */
export function panelOf(node: object): Panel | undefined {
  return panels.get(node);
}

/** Content width for a node width: the panel sits inside the node padding. */
function contentWidth(nodeWidth: number, minWidth: number): number {
  return Math.max(nodeWidth, minWidth) - 2 * PW.metrics.padding;
}

/**
 * Size the node to its widgets plus the panel's wanted height.
 *
 * Both renderers give the DOM widget whatever is left below the widgets, so
 * this is the one place height is decided. Never shrinks a manual resize.
 */
export function fitNode(node: NodeLike, panel: Panel): void {
  const wanted = panel.spec.height(contentWidth(node.size[0], panel.spec.minWidth));
  const before = node.size[1];
  fitPanel(node, wanted, panel.spec.minWidth);
  panel.grown += node.size[1] - before;
  // Modern lays out from `setSize`; Classic reads `size` directly. Do both.
  (node as any).setSize?.([node.size[0], node.size[1]]);
  node.setDirtyCanvas?.(true, true);
}

/** Change the node height by `by` pixels, in either direction. */
function resizeNodeBy(node: NodeLike, by: number): void {
  const target = node.size[1] + by;
  node.size[1] = target;
  (node as any).setSize?.([node.size[0], target]);
  node.setDirtyCanvas?.(true, true);
}

/**
 * The zoom the host applied to this element, from the element itself.
 *
 * Both renderers lay the node out in node units and then scale it with a CSS
 * transform, so `offsetWidth` is node units and the bounding box is screen
 * pixels. Reading `app.canvas.ds.scale` instead was wrong mid-animation: on
 * load the view zooms to fit over several frames, and a transform one frame
 * behind the number made every panel look too short and grow without end.
 */
function elementScale(el: HTMLElement): number {
  const w = el.offsetWidth;
  return w > 0 ? el.getBoundingClientRect().width / w : 1;
}

/**
 * Host a panel on `node` as its last widget.
 *
 * Call from `onNodeCreated`, after the serialisation widgets are hidden. The
 * node's height is fitted here and again whenever the element comes back too
 * short — a saved workflow applies its stored size after creation, which is how
 * a node saved before a panel existed used to come back clipped.
 */
export function attachPanel(node: NodeLike, spec: PanelSpec): Panel {
  const box = document.createElement('div');
  box.dataset.captureWheel = 'true';
  box.style.cssText = 'display:flex;flex-direction:column;width:100%;height:100%;';
  const canvas = document.createElement('canvas');
  canvas.tabIndex = -1;
  canvas.style.cssText =
    'flex:1 1 auto;display:block;width:100%;min-height:0;outline:none;touch-action:none;';
  box.appendChild(canvas);

  // Backing density follows the graph zoom as well as the screen, so a panel
  // zoomed to 200% is drawn at 200% rather than upscaled from node units.
  // Capped: a 16x zoom on a 4K screen must not allocate a 60-megapixel canvas.
  const density = () => Math.min(3, Math.max(1, (globalThis.devicePixelRatio || 1) * elementScale(canvas)));
  const panel = new Panel(canvas, spec, {
    dpr: density,
    schedule: (fn) => requestAnimationFrame(fn),
  });
  panels.set(node, panel);

  const mods = (e: PointerEvent | MouseEvent): PointerMods => ({
    shift: e.shiftKey,
    double: e.detail === 2,
    time: e.timeStamp,
    event: e,
  });
  canvas.addEventListener('pointerenter', () => canvas.focus({ preventScroll: true }));
  canvas.addEventListener('pointerdown', (e) => {
    canvas.focus({ preventScroll: true });
    panel.scale = elementScale(canvas);
    if (panel.pointerDown(e.clientX, e.clientY, mods(e))) canvas.setPointerCapture?.(e.pointerId);
    // A press on the panel is never the start of a node drag.
    e.stopPropagation();
  });
  canvas.addEventListener('pointermove', (e) => {
    panel.scale = elementScale(canvas);
    if (panel.pointerMove(e.clientX, e.clientY, mods(e))) e.stopPropagation();
  });
  canvas.addEventListener('pointerup', (e) => {
    panel.pointerUp(e.clientX, e.clientY, mods(e));
    if (canvas.hasPointerCapture?.(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
  });
  canvas.addEventListener(
    'wheel',
    (e) => {
      panel.scale = elementScale(canvas);
      if (panel.wheel(e.clientX, e.clientY, e.deltaY)) {
        e.preventDefault();
        e.stopPropagation();
      }
    },
    { passive: false },
  );
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());

  const widget = (node as any).addDOMWidget(WIDGET_NAME, 'pwpanel', box, {
    serialize: false,
    hideOnZoom: false,
  });
  // The option alone does not stop serialisation on 1.49.6; the property does.
  widget.serialize = false;

  collapseInternalPreview(node);
  fitNode(node, panel);

  const measure = () => {
    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;
    // No width means not laid out at all (collapsed node, hidden at zoom).
    // No *height* with a width is the case that matters: the node gave the
    // widget nothing, and the deficit below is how it gets its space.
    if (w <= 0) return;
    panel.scale = elementScale(canvas);
    // Re-allocate on a size change, and on a zoom change: the observer does
    // not see transforms, so zoom is caught here via pointerenter and the
    // timer, which is why a panel sharpens as the pointer reaches it.
    const stale = w !== panel.width || h !== panel.height || Math.abs(density() - panel.density) > 0.01;
    if (h > 0 && stale) panel.resize(w, h);
    // The panel came back shorter than it asked for. `fitNode` guessed the
    // widget block from LiteGraph's layout, and the Modern renderer's rows
    // are taller than that, so grow by the measured deficit instead: both
    // renderers hand the extra straight to this widget. Done next frame,
    // because changing layout from inside the observer is reported as an
    // observer loop.
    //
    // And the reverse: the Modern renderer changes a node's layout while it
    // executes, which shrinks the panel for a frame and would grow the node
    // for good. Height this code added is remembered and handed back once
    // the panel is taller than it asked for; a user's own resize is not ours
    // to take, so only what was added is ever removed.
    const deficit = spec.height(w) - h;
    if (deficit > 0.5) {
      panel.grown += deficit;
      requestAnimationFrame(() => resizeNodeBy(node, deficit));
    } else if (deficit < -0.5 && panel.grown > 0) {
      const back = Math.min(-deficit, panel.grown);
      panel.grown -= back;
      requestAnimationFrame(() => resizeNodeBy(node, -back));
    }
  };
  const observer = new ResizeObserver(measure);
  observer.observe(canvas);
  // Observer callbacks ride the rendering pipeline, which a background tab
  // does not run; a timer still does, so the first measurement never waits
  // for a paint. Harmless when the observer got there first.
  setTimeout(measure, 0);
  canvas.addEventListener('pointerenter', measure);

  const priorRemoved = node.onRemoved;
  node.onRemoved = function (this: NodeLike) {
    observer.disconnect();
    panel.dispose();
    panels.delete(node);
    priorRemoved?.call(this);
  };
  return panel;
}

/**
 * Hide a schema STRING widget that exists only to carry serialised state.
 *
 * The widget object and its index are kept, so `widgets_values` is the same
 * as it ever was and saved workflows load unchanged. Each line below is
 * needed by one renderer or the other: `type` and `computeSize` for Classic,
 * the stub element and `options.hidden` for Modern. `options` is mutated
 * rather than replaced because the host keeps a reference to the object;
 * removing the widget instead leaves its textarea mounted.
 */
export function hideSerialisationWidget(node: NodeLike, name: string): void {
  const w: any = node.widgets?.find((x) => x.name === name);
  if (!w) return;
  w.type = 'hidden';
  w.computeSize = () => [0, -4];
  w.computeLayoutSize = undefined;
  const stub = document.createElement('div');
  stub.style.cssText = 'display:none;height:0;';
  w.element = stub;
  w.options ??= {};
  w.options.hidden = true;
}
