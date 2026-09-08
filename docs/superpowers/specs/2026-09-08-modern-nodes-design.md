# Panels on DOM widgets: one renderer for Classic and Modern Node Design

Date: 2026-09-08. Status: approved, building. Ships as 2.1.0.

## Problem

Every panel the pack draws (curve editor, live previews, preset strip, colour
mixer, grain response, palette strip) is painted from `onDrawForeground` on
the LiteGraph canvas. ComfyUI frontend 1.49 added the opt-in "Modern Node
Design (Nodes 2.0)", which renders nodes as DOM elements and never calls that
hook. With it on, every PW node is blank below its sliders. 2.0.1 ships a
warning; this release ships the port.

## What was measured (frontend 1.49.6, both modes, 2026-09-08)

- `node.addDOMWidget(name, type, element, opts)` renders in both modes.
- In Modern, every DOM widget whose object has a `computeLayoutSize`
  function gets a CSS grid row of `auto`, and all such rows share the node's
  leftover height equally. Explicit CSS heights on the element are ignored.
  Therefore: one container widget per node, appended last, absorbing leftover.
- `node.size[1]` still sets the node height in both modes.
- Pointer events reach a `<canvas>` inside the widget in both modes;
  `stopPropagation` on pointerdown is required or the node drags.
- Wheel is forwarded to the graph unless the target is inside an element with
  `data-capture-wheel="true"` that contains `document.activeElement`. A
  focusable canvas (`tabIndex=-1`) focused on pointerenter/pointerdown
  receives wheel; graph zoom is unaffected.
- `{serialize:false}` in the options does not stop serialisation; the
  `serialize` property on the returned widget must be set. The 2.0.1 notice
  widget missed this and appends an empty string to `widgets_values` in
  Modern.
- Hiding a schema STRING widget that exists only to serialise (`curves`,
  `hsl`, `locked`): keep the original widget object and index, set
  `type='hidden'`, `computeSize=()=>[0,-4]`, swap `element` for a
  `display:none` div, set `computeLayoutSize=undefined`, and mutate
  `options.hidden=true` in place. Splicing or `removeWidget` leaves the old
  textarea mounted.
- `getExtraMenuOptions` and `LiteGraph.ContextMenu` work in Modern.
- `nodeCreated` fires before `node.type` is set; the class is on
  `node.constructor.comfyClass`.

## Design

### `web/src/widgets/panel.ts` (new)

`attachPanel(node, spec): Panel`. Creates the container `<div>` with
`data-capture-wheel`, a `<canvas>` child with `tabIndex=-1`, and registers it
with `addDOMWidget` as the node's last widget, `serialize=false`.

The panel owns:

- A 2D context sized to the element's CSS box times `devicePixelRatio`. The
  context is scaled so drawing code works in CSS pixels, in local coordinates
  with origin at the panel's top-left.
- `invalidate()`: schedules one `requestAnimationFrame` redraw, coalescing
  bursts. `draw(ctx, rect)` from the spec is called with `rect = {0, 0, w, h}`.
- Pointer translation: `pointerdown/move/up`, `dblclick`, `wheel` are turned
  into `spec.onPointerDown(x, y, mods)` etc. with local coordinates. Pointer
  capture is set on pointerdown so drags continue outside the element. The
  handler's return value of `true` stops propagation.
- `ResizeObserver` on the element: re-size the backing store and redraw.
- `setHeight(px)`: the panel's requested height. Node height is set to
  `widgetsBottom + px`; both modes then give the container that space.
- Teardown on `node.onRemoved`: observers, listeners, `Preview` textures.

The panel exposes `width` and `height` in CSS pixels for layout functions.

### Hidden serialisation widgets

`hideSerialisationWidget(node, name)` in `panel.ts` applies the measured
recipe. Index and value are untouched, so `widgets_values` is byte-identical
to 2.0.x and saved workflows load as before.

### Node files

`look.ts`, `curves.ts`, `spatial_preview.ts` (Grain and Optics), `palette.ts`
each replace their `chainHandler` mouse/draw plumbing and the
`fitPanel`/`widgetHeight`/`ensureHeight` layout arithmetic with one
`attachPanel` call. Their layout functions keep their structure, but start at
`y = 0` and use `panel.width` instead of `node.size[0]`. Every
`node.setDirtyCanvas(true, true)` that meant "repaint my panel" becomes
`panel.invalidate()`.

Header chips (reset, lock, export) and section badges stay canvas-drawn
inside the panel; hit testing is the same `headerChip` geometry as today,
now with a real context available at all times.

`Preview` and `CurveEditor` are unchanged: they already take a `Rect`.

### Removed

`warnIfModernNodes`, `MODERN_NODES_NOTICE`, the per-node notice in
`index.ts`, `collapseInternalPreview` and the height arithmetic in
`layout.ts`, all `onDrawForeground`/`onMouse*` chaining in node files.
`chainHandler` stays for `onResize` only if still needed; otherwise it goes.
`modernNodesActive()` stays for one thing: nothing. It goes too.

### Kept

The `$$canvas-image-preview` collapse: the host still attaches its own
preview widget to nodes with an IMAGE output. In Classic that widget takes
node space; in Modern it is a DOM row that would share leftover. It is
collapsed the same way as today, in `attachPanel`.

### README and CHANGELOG

README "Node design" paragraph becomes: works in both node designs, tested on
1.47.11 and 1.49.6. CHANGELOG 2.1.0 entry. Version bump in the three places.

## Testing

- `web/test/panel.test.ts` under node with a fake element: DPR scaling,
  local coordinate translation, `invalidate` coalescing, wheel and pointer
  routing, teardown. The panel module must not import the host surface at
  module level (same rule as `preview.ts`).
- Existing `curve_editor.test.ts` and `preview.test.ts` unchanged.
- `tests/test_build.py` proves the committed bundle matches the sources.
- Live, on the 8199 instance, in Classic and in Modern: the template loads
  with 0 console errors; every panel draws; curve points add/drag/remove;
  preview pans, zooms and double-click resets; preset strip applies; palette
  export menu opens; reset chip works; saved workflow `widgets_values` are
  byte-identical before and after a load/save round trip; the V19 production
  workflow loads with 0 missing links.

## Out of scope

Redesigning any panel. Re-implementing panels as HTML controls. Supporting
frontends older than the tested minimum.
