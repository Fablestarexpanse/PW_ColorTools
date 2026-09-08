# Panels on DOM widgets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every PW Color panel renders and responds in both Classic and Modern Node Design, with saved workflows untouched.

**Architecture:** One `addDOMWidget` container per node hosts a `<canvas>`; the existing drawing classes draw into it in local coordinates. Node files swap their LiteGraph hook plumbing for one `attachPanel` call. Serialisation-only widgets stay in place and are hidden by the measured recipe.

**Tech Stack:** TypeScript (strict, esbuild bundle, node `--experimental-strip-types` tests), ComfyUI frontend 1.47.11 and 1.49.6, Python test suite driving `npm test` and the bundle check.

**Spec:** `docs/superpowers/specs/2026-09-08-modern-nodes-design.md`

## Global Constraints

- Node ≥ 22.6 for the frontend tests; esbuild pinned 0.25.0.
- `web/src/widgets/panel.ts` must not import `./comfy.ts` or `/scripts/*` at module level (node must load it).
- `widgets_values` of every PW node must serialise byte-identically to 2.0.1: same count, same order, same values.
- No colour, radius or spacing literal outside `theme.ts`.
- Never mention the pack the design style came from anywhere shipped.
- Frontend tested minimum stays 1.47; verified on 1.49.6.
- Test command for the Python suite: `PYTHONPATH=<scratchpad>/pylibs COMFYUI_PATH="F:/Stability Matrix/Packages/ComfyUI" "F:/Stability Matrix/Packages/ComfyUI/venv/Scripts/python.exe" -m pytest tests/ -q`. Expect 1 skip only.

---

### Task 1: `Panel` core, host-free

**Files:**
- Create: `web/src/widgets/panel.ts`
- Test: `web/test/panel.test.ts`

**Interfaces:**
- Produces:
  ```ts
  export interface PointerMods { shift: boolean; double: boolean; time: number }
  export interface PanelSpec {
    minWidth: number;
    height: (width: number) => number;           // panel height wanted for a width
    draw: (ctx: Ctx, r: Rect) => void;            // r = {0,0,width,height}
    onPointerDown?: (x: number, y: number, m: PointerMods) => boolean;
    onPointerMove?: (x: number, y: number, m: PointerMods) => boolean;
    onPointerUp?: (x: number, y: number, m: PointerMods) => boolean;
    onWheel?: (x: number, y: number, delta: number) => boolean;
  }
  export interface CanvasLike { width: number; height: number; getContext(k: '2d'): Ctx | null; getBoundingClientRect(): { left: number; top: number; width: number; height: number } }
  export interface PanelEnv { dpr: () => number; schedule: (fn: () => void) => void }
  export class Panel {
    constructor(canvas: CanvasLike, spec: PanelSpec, env: PanelEnv)
    readonly width: number; readonly height: number;   // CSS px
    resize(width: number, height: number): void;      // sets backing store, redraws now
    invalidate(): void;                                // one scheduled draw per burst
    draw(): void;                                      // immediate
    pointerDown(clientX, clientY, m): boolean; pointerMove(...): boolean; pointerUp(...): boolean;
    wheel(clientX, clientY, delta): boolean;
    dispose(): void;                                   // no further draws
  }
  ```

- [ ] **Step 1: Write the failing tests**

```ts
// web/test/panel.test.ts
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { Panel, type PanelSpec } from '../src/widgets/panel.ts';

function fakeCanvas(left = 10, top = 20) {
  const calls: string[] = [];
  const ctx: any = {
    calls, setTransform: (...a: number[]) => calls.push('setTransform ' + a.join(',')),
    clearRect: (...a: number[]) => calls.push('clearRect ' + a.join(',')),
  };
  const c: any = { width: 0, height: 0, getContext: () => ctx, getBoundingClientRect: () => ({ left, top, width: c.width, height: c.height }) };
  return { c, ctx, calls };
}
function spec(over: Partial<PanelSpec> = {}): PanelSpec {
  return { minWidth: 100, height: () => 50, draw: () => {}, ...over };
}
const env = (dpr = 2) => { const q: (() => void)[] = []; return { env: { dpr: () => dpr, schedule: (f: () => void) => q.push(f) }, q }; };

describe('Panel sizing', () => {
  it('scales the backing store by device pixel ratio and draws in CSS pixels', () => {
    const { c, calls } = fakeCanvas();
    const drawn: any[] = [];
    const { env: e } = env(2);
    const p = new Panel(c, spec({ draw: (_ctx, r) => drawn.push({ ...r }) }), e);
    p.resize(300, 150);
    assert.equal(c.width, 600); assert.equal(c.height, 300);
    assert.ok(calls.includes('setTransform 2,0,0,2,0,0'));
    assert.deepEqual(drawn.at(-1), { x: 0, y: 0, w: 300, h: 150 });
  });
});

describe('Panel invalidate', () => {
  it('coalesces a burst into one scheduled draw', () => {
    const { c } = fakeCanvas(); let n = 0;
    const { env: e, q } = env();
    const p = new Panel(c, spec({ draw: () => n++ }), e);
    p.resize(100, 50); n = 0;
    p.invalidate(); p.invalidate(); p.invalidate();
    assert.equal(q.length, 1);
    q[0](); assert.equal(n, 1);
    p.invalidate(); assert.equal(q.length, 2);
  });
  it('does nothing after dispose', () => {
    const { c } = fakeCanvas(); let n = 0;
    const { env: e, q } = env();
    const p = new Panel(c, spec({ draw: () => n++ }), e);
    p.resize(100, 50); n = 0; p.dispose(); p.invalidate();
    for (const f of q) f();
    assert.equal(n, 0);
  });
});

describe('Panel pointer routing', () => {
  it('translates client coordinates to local ones', () => {
    const { c } = fakeCanvas(10, 20); const seen: number[][] = [];
    const { env: e } = env();
    const p = new Panel(c, spec({ onPointerDown: (x, y) => { seen.push([x, y]); return true; } }), e);
    p.resize(100, 50);
    assert.equal(p.pointerDown(15, 27, { shift: false, double: false, time: 0 }), true);
    assert.deepEqual(seen, [[5, 7]]);
  });
  it('reports unhandled when the spec has no handler', () => {
    const { c } = fakeCanvas(); const { env: e } = env();
    const p = new Panel(c, spec(), e); p.resize(100, 50);
    assert.equal(p.pointerDown(1, 1, { shift: false, double: false, time: 0 }), false);
    assert.equal(p.wheel(1, 1, 3), false);
  });
  it('redraws after a handled event', () => {
    const { c } = fakeCanvas(); const { env: e, q } = env();
    const p = new Panel(c, spec({ onWheel: () => true }), e); p.resize(100, 50);
    p.wheel(5, 5, -100);
    assert.equal(q.length, 1);
  });
});
```

- [ ] **Step 2: Run to see it fail**

Run (in `web/`): `node --experimental-strip-types --no-warnings --test test/panel.test.ts`
Expected: fails, cannot find `../src/widgets/panel.ts`.

- [ ] **Step 3: Implement `Panel`**

```ts
// web/src/widgets/panel.ts (core part; DOM attach comes in Task 2)
import type { Ctx, Rect } from './draw.ts';

export interface PointerMods { shift: boolean; double: boolean; time: number }
export interface PanelSpec { /* as in Interfaces */ }
export interface CanvasLike { /* as in Interfaces */ }
export interface PanelEnv { dpr: () => number; schedule: (fn: () => void) => void }

export class Panel {
  private w = 0; private h = 0; private pending = false; private disposed = false;
  private readonly ctx: Ctx;
  constructor(private readonly canvas: CanvasLike, readonly spec: PanelSpec, private readonly env: PanelEnv) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('PW Color panel: no 2D context');
    this.ctx = ctx;
  }
  get width(): number { return this.w; }
  get height(): number { return this.h; }
  resize(width: number, height: number): void {
    this.w = Math.max(0, Math.floor(width)); this.h = Math.max(0, Math.floor(height));
    const dpr = this.env.dpr();
    this.canvas.width = Math.round(this.w * dpr); this.canvas.height = Math.round(this.h * dpr);
    this.draw();
  }
  draw(): void {
    if (this.disposed || this.w === 0 || this.h === 0) return;
    const dpr = this.env.dpr();
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.ctx.clearRect(0, 0, this.w, this.h);
    this.spec.draw(this.ctx, { x: 0, y: 0, w: this.w, h: this.h });
  }
  invalidate(): void {
    if (this.pending || this.disposed) return;
    this.pending = true;
    this.env.schedule(() => { this.pending = false; this.draw(); });
  }
  dispose(): void { this.disposed = true; }
  private local(cx: number, cy: number): [number, number] {
    const r = this.canvas.getBoundingClientRect();
    return [cx - r.left, cy - r.top];
  }
  private route(handled: boolean): boolean { if (handled) this.invalidate(); return handled; }
  pointerDown(cx: number, cy: number, m: PointerMods): boolean {
    const [x, y] = this.local(cx, cy); return this.route(this.spec.onPointerDown?.(x, y, m) ?? false);
  }
  pointerMove(cx: number, cy: number, m: PointerMods): boolean {
    const [x, y] = this.local(cx, cy); return this.route(this.spec.onPointerMove?.(x, y, m) ?? false);
  }
  pointerUp(cx: number, cy: number, m: PointerMods): boolean {
    const [x, y] = this.local(cx, cy); return this.route(this.spec.onPointerUp?.(x, y, m) ?? false);
  }
  wheel(cx: number, cy: number, delta: number): boolean {
    const [x, y] = this.local(cx, cy); return this.route(this.spec.onWheel?.(x, y, delta) ?? false);
  }
}
```

Note: TS strip-only mode rejects constructor parameter properties. Declare the fields and assign them in the body instead (see `TexSource` in `canvas/preview.ts`).

- [ ] **Step 4: Run tests, then `npx tsc --noEmit`**

Expected: all `panel.test.ts` tests pass, typecheck clean.

- [ ] **Step 5: Commit** — `Panel: a device-pixel canvas that draws in local CSS coordinates`

---

### Task 2: `attachPanel` and `hideSerialisationWidget` (DOM wiring)

**Files:**
- Modify: `web/src/widgets/panel.ts` (append)
- Modify: `web/src/widgets/layout.ts` (keep `widgetHeight`, `collapseInternalPreview`, `fitPanel`; delete `ensureHeight`)

**Interfaces:**
- Consumes: `Panel`, `widgetHeight(node)`, `collapseInternalPreview(node)`, `fitPanel(node, panelHeight, minWidth)`.
- Produces:
  ```ts
  export function attachPanel(node: NodeLike, spec: PanelSpec): Panel;
  export function hideSerialisationWidget(node: NodeLike, name: string): void;
  export function fitNode(node: NodeLike, panel: Panel): void;   // node height = widgets + spec.height(width)
  ```

- [ ] **Step 1: Implement**

```ts
import { collapseInternalPreview, fitPanel, widgetHeight } from './layout.ts';
import type { NodeLike } from '../comfy.ts';   // type-only import: erased, so node can still load this module

const WIDGET_NAME = 'pw_panel';

export function fitNode(node: NodeLike, panel: Panel): void {
  const width = Math.max(panel.spec.minWidth, node.size[0]);
  fitPanel(node, panel.spec.height(width - 2 * 12 /* PW.metrics.padding: import PW */) , panel.spec.minWidth);
  // Modern re-lays out from setSize; Classic reads size directly. Do both.
  (node as any).setSize?.([node.size[0], node.size[1]]);
  node.setDirtyCanvas?.(true, true);
}

export function attachPanel(node: NodeLike, spec: PanelSpec): Panel {
  const box = document.createElement('div');
  box.dataset.captureWheel = 'true';
  box.style.cssText = 'display:flex;flex-direction:column;width:100%;height:100%;';
  const canvas = document.createElement('canvas');
  canvas.tabIndex = -1;
  canvas.style.cssText = 'flex:1 1 auto;display:block;width:100%;min-height:0;outline:none;touch-action:none;';
  box.appendChild(canvas);

  const panel = new Panel(canvas, spec, {
    dpr: () => globalThis.devicePixelRatio || 1,
    schedule: (fn) => requestAnimationFrame(fn),
  });

  const mods = (e: PointerEvent | MouseEvent): PointerMods =>
    ({ shift: e.shiftKey, double: (e as any).detail === 2, time: e.timeStamp });
  canvas.addEventListener('pointerenter', () => canvas.focus({ preventScroll: true }));
  canvas.addEventListener('pointerdown', (e) => {
    canvas.focus({ preventScroll: true });
    if (panel.pointerDown(e.clientX, e.clientY, mods(e))) { canvas.setPointerCapture?.(e.pointerId); }
    e.stopPropagation();   // a press on the panel must never start a node drag
  });
  canvas.addEventListener('pointermove', (e) => { if (panel.pointerMove(e.clientX, e.clientY, mods(e))) e.stopPropagation(); });
  canvas.addEventListener('pointerup', (e) => { panel.pointerUp(e.clientX, e.clientY, mods(e)); canvas.releasePointerCapture?.(e.pointerId); });
  canvas.addEventListener('wheel', (e) => {
    if (panel.wheel(e.clientX, e.clientY, e.deltaY)) { e.preventDefault(); e.stopPropagation(); }
  }, { passive: false });
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());

  const widget = (node as any).addDOMWidget(WIDGET_NAME, 'pwpanel', box, { serialize: false, hideOnZoom: false });
  widget.serialize = false;   // the option alone does not stop serialisation on 1.49.6

  collapseInternalPreview(node);
  fitNode(node, panel);

  const ro = new ResizeObserver(() => {
    const r = canvas.getBoundingClientRect();
    // Zoomed canvas: the element's CSS box scales with the graph. Draw at the unscaled size.
    const scale = (globalThis as any).app?.canvas?.ds?.scale ?? 1;
    const w = r.width / scale, h = r.height / scale;
    if (w <= 0 || h <= 0) return;
    panel.resize(w, h);
    // A saved workflow applies its stored size after creation; if the panel came
    // back shorter than it needs, grow the node. Converges in one pass.
    if (h < spec.height(w) - 0.5) fitNode(node, panel);
  });
  ro.observe(canvas);

  const priorRemoved = node.onRemoved;
  node.onRemoved = function (this: NodeLike) { ro.disconnect(); panel.dispose(); priorRemoved?.call(this); };
  return panel;
}

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
  w.options.hidden = true;   // mutate in place: the host holds this object
}
```

Zoom note: in Classic the DOM widget's CSS box is scaled by the graph transform, so `getBoundingClientRect` reports scaled sizes. Dividing by `ds.scale` keeps the drawing in node units, and the browser scales the bitmap. Check in Modern whether the same holds (spike: rows were in node units at scale 1; verify at 0.5 in Task 8).

- [ ] **Step 2: Delete `ensureHeight` from `layout.ts`; `npx tsc --noEmit`; run `npm test`**

- [ ] **Step 3: Commit** — `attachPanel: host a panel on a DOM widget in both node designs`

---

### Task 3: PW Curves on the panel

**Files:**
- Modify: `web/src/nodes/curves.ts`

**Interfaces:**
- Consumes: `attachPanel`, `hideSerialisationWidget`, `fitNode`, `Panel`.

- [ ] **Step 1: Rewrite the node wiring**

Keep `readState`, `writeState`, `loadHistogram`, `CHANNEL_TABS`, `rebake`. Replace `makeUI`'s layout with local coordinates and the `onNodeCreated` body with:

```ts
const PANEL_MIN_H = HEADER_H + 4 + PREVIEW_H + M.gapControl + TABS_H + M.gapControl + MIN_EDITOR_H + M.padding;

function layout(w: number, h: number) {
  let y = 0;
  const header = { x: 0, y, w, h: HEADER_H }; y += HEADER_H + 4;
  const preview = { x: 0, y, w, h: PREVIEW_H }; y += PREVIEW_H + M.gapControl;
  const tabs = { x: 0, y, w, h: TABS_H }; y += TABS_H + M.gapControl;
  const editor = { x: 0, y, w, h: Math.max(MIN_EDITOR_H, h - y - M.padding) };
  return { header, preview, tabs, editor };
}
```

In `onNodeCreated`:

```ts
hideSerialisationWidget(this, 'curves');
const ui = makeUI(this);
uis.set(this, ui);
let panel: Panel;
const repaint = () => panel?.invalidate();
panel = attachPanel(this, {
  minWidth: 360,
  height: () => PANEL_MIN_H,
  draw: (ctx, r) => {
    const L = layout(r.w, r.h);
    sectionHeader(ctx, 'Curves', L.header, BADGE.lut);
    headerChip(ctx, L.header, 'reset', BADGE.lut.label);
    ui.preview.comparing = isComparing();
    ui.preview.draw(ctx, L.preview);
    ui.tabs.draw(ctx, L.tabs);
    ui.editor.draw(ctx, L.editor);
  },
  onPointerDown: (x, y, m) => {
    const L = layout(panel.width, panel.height);
    if (hit(headerChip(null, L.header, 'reset', BADGE.lut.label), x, y, 3)) {
      resetNode(this, { after: () => { ui.editor.resetAll(); ui.rebake(this); } });
      return true;
    }
    const tab = ui.tabs.onPointerDown(x, y, L.tabs);
    if (tab) { ui.editor.channel = tab as ChannelId; return true; }
    if (hit(L.preview, x, y)) { ui.preview.onPointerDown(x, y, L.preview, m.shift, m.double); return true; }
    if (hit(L.editor, x, y)) { ui.editor.onPointerDown(x, y, L.editor, m.shift, m.time); return true; }
    return false;
  },
  onPointerMove: (x, y, m) => {
    const L = layout(panel.width, panel.height);
    if (ui.preview.onPointerMove(x, y, L.preview)) return true;
    return ui.editor.onPointerMove(x, y, L.editor, m.shift);
  },
  onPointerUp: () => { const a = ui.editor.onPointerUp(); const b = ui.preview.onPointerUp(); return a || b; },
  onWheel: (x, y, d) => { const L = layout(panel.width, panel.height); return hit(L.preview, x, y) && ui.preview.onWheel(x, y, L.preview, d); },
});
```

`headerChip(null, ...)` measures with the estimate; pass `panel`'s context instead: add `ctx` getter to `Panel` (`get context(): Ctx`) and use `headerChip(panel.context, ...)` so hit tests and drawing measure identically.

Replace every `this.setDirtyCanvas?.(true, true)` that meant "repaint" with `repaint()`: in `refresh`, `onCompareChange`, `editor.onChange`, `writeState` (pass `repaint` into `makeUI`), `loadHistogram`, `preview.load` callbacks. In `onConfigure`: `fitNode(this, panel)` instead of `fitPanel(...)`, then `repaint()`.

Delete all `chainHandler` calls, `collapseInternalPreview`, `ensureHeight`, `ctx0`.

- [ ] **Step 2: `npx tsc --noEmit && npm run build && npm test`**

- [ ] **Step 3: Live check in Classic on 8199**: load template, PW Curves shows header, preview, tabs, editor; click adds a point; drag moves; shift-click removes; wheel over preview zooms without zooming the graph; reset chip resets. Then Modern: same. Record both in the commit message.

- [ ] **Step 4: Commit** — `PW Curves draws on a DOM panel: works in Classic and Modern Node Design`

---

### Task 4: Spatial preview (PW Grain, PW Optics)

**Files:**
- Modify: `web/src/nodes/spatial_preview.ts`, `web/src/nodes/grain.ts`, `web/src/nodes/optics.ts`

**Interfaces:**
- `attachSpatialPreview(nodeType, opts)` keeps its signature. `opts.extra` and `opts.drawExtra` now receive local coordinates: `drawExtra(ctx, node, top: number, width: number)` with `top = 0`.

- [ ] **Step 1: Rewrite `attachSpatialPreview`**

```ts
nodeType.prototype.onNodeCreated = function (this: NodeLike) {
  const r = onCreated?.apply(this, arguments as any);
  const preview = new Preview();
  let panel: Panel;
  const extraH = () => opts.extra?.(this) ?? 0;
  const headerRect = (w: number) => ({ x: 0, y: extraH(), w, h: HEADER_H });
  const previewRect = (w: number) => ({ x: 0, y: extraH() + HEADER_H + 6, w, h: opts.height });
  panel = attachPanel(this, {
    minWidth: opts.minWidth,
    height: () => extraH() + HEADER_H + 6 + opts.height + M.padding,
    draw: (ctx, rr) => {
      opts.drawExtra?.(ctx, this, 0, rr.w);
      sectionHeader(ctx, opts.label ?? 'Result', headerRect(rr.w), BADGE.render);
      headerChip(ctx, headerRect(rr.w), 'reset', BADGE.render.label);
      preview.comparing = isComparing();
      preview.draw(ctx, previewRect(rr.w));
    },
    onPointerDown: (x, y, m) => {
      const hr = headerRect(panel.width), pr = previewRect(panel.width);
      if (hit(headerChip(panel.context, hr, 'reset', BADGE.render.label), x, y, 3)) { resetNode(this); return true; }
      if (!hit(pr, x, y)) return false;
      preview.onPointerDown(x, y, pr, m.shift, m.double); return true;
    },
    onPointerMove: (x, y) => preview.onPointerMove(x, y, previewRect(panel.width)),
    onPointerUp: () => preview.onPointerUp(),
    onWheel: (x, y, d) => { const pr = previewRect(panel.width); return hit(pr, x, y) && preview.onWheel(x, y, pr, d); },
  });
  const repaint = () => panel.invalidate();
  const refresh = () => { void preview.load(this.id, repaint); void preview.loadOutput(this.id, repaint); };
  refresh();
  const stopCompare = onCompareChange(repaint);
  const stopRun = onRunComplete(refresh);
  const priorRemoved = this.onRemoved;
  this.onRemoved = function (this: NodeLike) { stopCompare(); stopRun(); priorRemoved?.call(this); };
  // Grain's response curve follows the sliders.
  (this as any).__pwRepaint = repaint;
  return r;
};
```

`grain.ts`: `drawExtra` draws at `top = 0` (already parameterised); the `onWidgetChanged` hook calls `(this as any).__pwRepaint?.()`. Better: export `repaintOf(node)` from `panel.ts` backed by a `WeakMap<object, Panel>` filled in `attachPanel`, and use `panelOf(node)?.invalidate()` in `grain.ts`. Add to Task 2's interface: `export function panelOf(node: NodeLike): Panel | undefined`.

- [ ] **Step 2: typecheck, build, tests; live check Grain + Optics in both modes (response curve follows sliders; result preview after a run pans/zooms).**

- [ ] **Step 3: Commit** — `PW Grain and PW Optics draw on a DOM panel`

---

### Task 5: PW Look on the panel

**Files:**
- Modify: `web/src/nodes/look.ts`

- [ ] **Step 1: Rewrite layout in local coordinates**

```ts
function layout(w: number, ui: LookUI) {
  const { rows } = gridShape(w, Math.max(1, ui.presets.length));
  let y = 0;
  const previewHeader = { x: 0, y, w, h: HEADER_H }; y += HEADER_H + 6;
  const preview = { x: 0, y, w, h: PREVIEW_H }; y += PREVIEW_H + M.gapSection;
  const presetHeader = { x: 0, y, w, h: HEADER_H }; y += HEADER_H + 6;
  const strip = { x: 0, y, w, h: rows * CELL_H + (rows - 1) * 6 }; y += strip.h + M.gapSection;
  const hslHeader = { x: 0, y, w, h: HEADER_H }; y += HEADER_H + 6;
  const hslTabs = { x: 0, y, w: Math.min(w, 220), h: 22 };
  const hslRows = { x: 0, y: y + 26, w, h: HSL_BANDS.length * HSL_ROW_H };
  return { previewHeader, preview, presetHeader, strip, hslHeader, hslTabs, hslRows };
}
function panelHeight(w: number, ui: LookUI): number {
  const { rows } = gridShape(Math.max(200, w), Math.max(1, ui.presets.length));
  const strip = rows * CELL_H + (rows - 1) * 6;
  const base = HEADER_H + 6 + PREVIEW_H + M.gapSection + HEADER_H + 6 + strip + M.gapSection + HEADER_H + 6 + M.padding;
  return base + (ui.hslOpen ? 26 + HSL_BANDS.length * HSL_ROW_H + M.gapControl : 0);
}
```

`onNodeCreated`: `hideSerialisationWidget(this, 'hsl')`; `attachPanel(this, { minWidth: 420, height: (w) => panelHeight(w, ui), draw, onPointerDown, onPointerMove, onPointerUp, onWheel })` with the same branch order as today's `onMouseDown` (reset chip, preview, hsl header toggle → `fitNode(this, panel)`, strip cell → `applyPreset`, hsl tabs, hsl row drag). After presets load: `fitNode(this, panel); refresh(); panel.invalidate()`. `applyPreset`, `writeHsl`, `buildThumbnails`, `refreshPreview` take a `repaint` or call `panelOf(node)?.invalidate()`. `onWidgetChanged` → `refreshPreview` + `panelOf(this)?.invalidate()`. `onConfigure` → `fitNode` + `refreshPreview` + invalidate. Drop `onResize` chain: the panel's `ResizeObserver` re-fits when the width changes the row count (`fitNode` is called from the observer when `h < spec.height(w)`).

The HSL row hit test today hardcodes `trackX = r.x + 108` while drawing uses `swatchW + 62 = 108` and `trackW = rowW - 148` vs draw's `rowW - 108 - 40`. They agree; keep them but define `HSL_TRACK_X = 108` and `HSL_TRACK_PAD = 148` once and use in both places.

- [ ] **Step 2: typecheck, build, tests; live check both modes: preview live as sliders move; preset click applies and sliders follow; colour mixer opens, drag sets a band, double-click zeroes it; reset chip.**

- [ ] **Step 3: Commit** — `PW Look draws on a DOM panel`

---

### Task 6: PW Palette on the panel

**Files:**
- Modify: `web/src/nodes/palette.ts`

- [ ] **Step 1: Rewrite**

`hideSerialisationWidget(this, 'locked')`. Layout: `header = {0,0,w,18}`, `strip = {0,22,w,STRIP_H}`; panel height `STRIP_H + 22 + M.gapSection + M.padding` (unchanged `PANEL_BLOCK`). `draw` = header, palette, saved hint, toast. `onPointerDown`: export chip → `new LiteGraph.ContextMenu(..., { event: e })` needs the DOM event; add `event?: Event` to `PointerMods` in Task 1 (`{ shift, double, time, event }`) and pass it from `attachPanel`. Lock chip and swatch copy as today. The `executed` listener calls `panelOf(node)?.invalidate()`; `toast` schedules a second invalidate after 1400 ms (`setTimeout(() => panelOf(node)?.invalidate(), 1450)`) so the toast disappears without a mouse move.

- [ ] **Step 2: typecheck, build, tests; live check: run graph, swatches appear; click copies (toast); export menu opens in both modes; lock toggles.**

- [ ] **Step 3: Commit** — `PW Palette draws on a DOM panel`

---

### Task 7: Remove the Modern-mode warning and the dead layout code

**Files:**
- Modify: `web/src/index.ts`, `web/src/comfy.ts`, `web/src/widgets/layout.ts`
- Modify: `README.md:433-439`, `CHANGELOG.md`, `pyproject.toml`, `pw_color/__init__.py`, `web/package.json`

- [ ] **Step 1:** delete `modernNodesActive`, `MODERN_NODES_NOTICE`, `warnIfModernNodes`, the `nodeCreated` hook in `index.ts`, `ensureHeight`; `chainHandler` stays only if a caller remains (`grep -rn chainHandler web/src`), else delete it and its `NodeLike` mouse fields. `NodeLike` keeps `onRemoved`, `onConfigure`, `size`, `widgets`, `setDirtyCanvas`.
- [ ] **Step 2:** README paragraph:

```
**Node design.** Panels are hosted on DOM widgets, so the pack renders the
same in the Classic node design and in *Modern Node Design (Nodes 2.0)*.
Tested on frontend 1.47.11 (Classic) and 1.49.6 (both).
```

CHANGELOG `## 2.1.0` → Added: "Modern Node Design (Nodes 2.0) support: every panel is hosted on a DOM widget and draws in both node designs. Saved workflows are unchanged." Fixed: "In Modern Node Design the 2.0.1 notice widget appended an empty entry to each PW node's saved widget values." Version 2.1.0 in three places.

- [ ] **Step 3:** `npx tsc --noEmit && npm run build`; full Python suite (expect 1 skip); commit — `2.1.0: panels render in both node designs; warning removed`

---

### Task 8: Live verification and workflow round trip

- [ ] **Step 1:** On 8199 (clone on this branch): Classic and Modern, load template: 0 console errors, every panel draws (screenshot each mode). Zoom to 50% and 200%: panels stay crisp and aligned.
- [ ] **Step 2:** Round trip: `app.graph.serialize()` before and after a load; compare every PW node's `widgets_values` with JSON.stringify — must be equal, and equal to the template's values.
- [ ] **Step 3:** Load the V19 production workflow (`F:/Stability Matrix/Packages/ComfyUI/user/default/workflows/*V19*`), 0 missing nodes, 0 dangling links, panels draw.
- [ ] **Step 4:** Run the template once (`--cpu`), confirm previews fill after the run in both modes.
- [ ] **Step 5:** Restore the user's setting to Modern on. Merge to `main`, tag `v2.1.0`, push, watch CI green.
