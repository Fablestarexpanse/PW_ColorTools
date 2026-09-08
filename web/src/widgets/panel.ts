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

  private readonly canvas: CanvasLike;
  private readonly env: PanelEnv;
  private readonly ctx: Ctx;
  private w = 0;
  private h = 0;
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

  /** Set the drawing size in node units and repaint now. */
  resize(width: number, height: number): void {
    this.w = Math.max(0, Math.floor(width));
    this.h = Math.max(0, Math.floor(height));
    const dpr = this.env.dpr();
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.draw();
  }

  draw(): void {
    if (this.disposed || this.w === 0 || this.h === 0) return;
    const dpr = this.env.dpr();
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
  fitPanel(node, wanted, panel.spec.minWidth);
  // Modern lays out from `setSize`; Classic reads `size` directly. Do both.
  (node as any).setSize?.([node.size[0], node.size[1]]);
  node.setDirtyCanvas?.(true, true);
}

function graphScale(): number {
  const s = (globalThis as any).app?.canvas?.ds?.scale;
  return typeof s === 'number' && s > 0 ? s : 1;
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

  const panel = new Panel(canvas, spec, {
    dpr: () => globalThis.devicePixelRatio || 1,
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
    panel.scale = graphScale();
    if (panel.pointerDown(e.clientX, e.clientY, mods(e))) canvas.setPointerCapture?.(e.pointerId);
    // A press on the panel is never the start of a node drag.
    e.stopPropagation();
  });
  canvas.addEventListener('pointermove', (e) => {
    panel.scale = graphScale();
    if (panel.pointerMove(e.clientX, e.clientY, mods(e))) e.stopPropagation();
  });
  canvas.addEventListener('pointerup', (e) => {
    panel.pointerUp(e.clientX, e.clientY, mods(e));
    if (canvas.hasPointerCapture?.(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
  });
  canvas.addEventListener(
    'wheel',
    (e) => {
      panel.scale = graphScale();
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

  const observer = new ResizeObserver(() => {
    const r = canvas.getBoundingClientRect();
    const scale = graphScale();
    const w = r.width / scale;
    const h = r.height / scale;
    if (w <= 0 || h <= 0) return;
    panel.scale = scale;
    panel.resize(w, h);
    // Growing the node from inside the observer changes layout in the same
    // pass, which the browser reports as an observer loop. Grow next frame.
    if (h < spec.height(w) - 0.5) requestAnimationFrame(() => fitNode(node, panel));
  });
  observer.observe(canvas);

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
