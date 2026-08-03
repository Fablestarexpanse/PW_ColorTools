/**
 * PW Curves — node wiring.
 *
 * Owns the layout, routes pointer events into the shared widgets, keeps the
 * `curves` widget value in sync so the graph serialises, and fetches the node's
 * own input histogram from the server route rather than waiting for a run.
 *
 * All pointer handlers go through `chainHandler`: replacing `onMouseDown`
 * outright breaks subgraph header buttons on frontend 1.4x.
 */

import { app, chainHandler, getWidget, type NodeLike } from '../comfy.ts';
import { PW } from '../theme.ts';
import { CurveEditor, identityState, type ChannelId, type CurveEditorState } from '../canvas/curve_editor.ts';
import { Segmented } from '../widgets/segmented.ts';
import { Slider } from '../widgets/slider.ts';
import { BADGE } from '../theme.ts';
import { sectionHeader, type Ctx, type Rect } from '../widgets/draw.ts';
import { fitPanel, widgetHeight } from '../widgets/layout.ts';

const M = PW.metrics;
const HEADER_H = 18;
const TABS_H = M.controlHeight;
const ROW_H = M.controlHeight;
const MIN_EDITOR_H = 160;

const CHANNEL_TABS = [
  { id: 'luma', label: 'Luma', colour: PW.channel.luma },
  { id: 'r', label: 'R', colour: PW.channel.r },
  { id: 'g', label: 'G', colour: PW.channel.g },
  { id: 'b', label: 'B', colour: PW.channel.b },
];

interface CurvesUI {
  editor: CurveEditor;
  tabs: Segmented;
  strength: Slider;
  layout: (node: NodeLike) => { tabs: Rect; editor: Rect; strength: Rect; header: Rect };
}

const uis = new WeakMap<object, CurvesUI>();

function readState(node: NodeLike): CurveEditorState {
  const w = getWidget(node, 'curves');
  try {
    const raw = JSON.parse(String(w?.value ?? ''));
    const s = identityState();
    for (const k of ['luma', 'r', 'g', 'b'] as ChannelId[]) {
      if (Array.isArray(raw?.[k]) && raw[k].length >= 2) s[k] = raw[k].map((p: number[]) => [p[0], p[1]]);
    }
    return s;
  } catch {
    return identityState();
  }
}

function writeState(node: NodeLike, ui: CurvesUI): void {
  const w = getWidget(node, 'curves');
  if (!w) return;
  // Compact, key-ordered, no whitespace: this string lands in the workflow JSON
  // and in the metadata of every saved PNG, so it stays small and it diffs.
  const s = ui.editor.state;
  w.value = JSON.stringify({ luma: s.luma, r: s.r, g: s.g, b: s.b });
  node.setDirtyCanvas?.(true, true);
}

/**
 * Fetch the node's own input histogram. This is the fix for the limitation
 * every other pack ships with — the editor can draw a real histogram the moment
 * you open it, not only after the graph has been run once.
 */
async function loadHistogram(node: NodeLike, ui: CurvesUI): Promise<void> {
  try {
    const res = await fetch(`/pw_color/histogram/${node.id}`);
    if (!res.ok) return; // 404 simply means nothing cached yet
    const data = await res.json();
    const h = data.histogram;
    ui.editor.histogram = {
      luma: Float32Array.from(h.luma),
      r: Float32Array.from(h.r),
      g: Float32Array.from(h.g),
      b: Float32Array.from(h.b),
    };
    node.setDirtyCanvas?.(true, true);
  } catch {
    // Offline or route not registered. The editor works without a histogram.
  }
}

function makeUI(node: NodeLike): CurvesUI {
  const editor = new CurveEditor();
  const tabs = new Segmented(CHANNEL_TABS);
  const strengthWidget = getWidget(node, 'strength');
  const strength = new Slider(
    { label: 'Strength', min: 0, max: 1, neutral: 1, default: 1, step: 0.01, decimals: 2 },
    typeof strengthWidget?.value === 'number' ? strengthWidget.value : 1,
  );

  const layout = (n: NodeLike) => {
    const x = M.padding;
    const w = n.size[0] - M.padding * 2;
    // Start below whatever widgets ComfyUI drew, measured rather than counted:
    // hidden widgets report a negative height, so counting rows over-estimates
    // and leaves a gap that grows every time we hide another control.
    let y = widgetHeight(n) + M.gapSection;
    const header = { x, y, w, h: HEADER_H };
    y += HEADER_H + 4;
    const tabsR = { x, y, w, h: TABS_H };
    y += TABS_H + M.gapControl;
    const editorH = Math.max(MIN_EDITOR_H, n.size[1] - y - ROW_H - M.gapSection - M.padding);
    const editorR = { x, y, w, h: editorH };
    y += editorH + M.gapControl;
    return { header, tabs: tabsR, editor: editorR, strength: { x, y, w, h: ROW_H } };
  };

  const ui: CurvesUI = { editor, tabs, strength, layout };
  editor.state = readState(node);
  editor.onChange = () => writeState(node, ui);
  return ui;
}

export function registerCurves(): void {
  app.registerExtension({
    name: 'pw.color.curves',
    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Curves') return;

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const r = onCreated?.apply(this, arguments as any);
        const ui = makeUI(this);
        uis.set(this, ui);

        // Hide the widgets we draw ourselves. `curves` is the serialisation
        // channel, not a control; `strength` has a proper slider below, and
        // showing both meant two controls for one value — which is exactly the
        // kind of thing that makes a node pack feel bolted together.
        for (const name of ['curves', 'strength']) {
          const w = getWidget(this, name);
          if (!w) continue;
          w.type = 'hidden';
          w.computeSize = () => [0, -4];
        }

        // Widget height from LiteGraph, editor height from us. The editor gets
        // a generous default because a curve you cannot see is not editable.
        fitPanel(this, HEADER_H + TABS_H + MIN_EDITOR_H + ROW_H + M.gapSection * 2 + M.padding, 360);

        chainHandler(this, 'onDrawForeground', function (this: NodeLike, ctx: Ctx) {
          if ((this as any).flags?.collapsed) return;
          const L = ui.layout(this);
          sectionHeader(ctx, 'Curves', L.header, BADGE.lut);
          ui.tabs.draw(ctx, L.tabs);
          ui.editor.draw(ctx, L.editor);
          ui.strength.draw(ctx, L.strength);
        });

        chainHandler(this, 'onMouseDown', function (this: NodeLike, e: any, pos: [number, number]) {
          const L = ui.layout(this);
          const [x, y] = pos;
          const now = e?.timeStamp ?? 0;
          const shift = !!e?.shiftKey;

          const tab = ui.tabs.onPointerDown(x, y, L.tabs);
          if (tab) {
            ui.editor.channel = tab as ChannelId;
            this.setDirtyCanvas?.(true, true);
            return true;
          }
          if (ui.strength.onPointerDown(x, y, L.strength, now)) {
            syncStrength(this, ui);
            return true;
          }
          if (
            x >= L.editor.x &&
            x <= L.editor.x + L.editor.w &&
            y >= L.editor.y &&
            y <= L.editor.y + L.editor.h
          ) {
            ui.editor.onPointerDown(x, y, L.editor, shift, now);
            this.setDirtyCanvas?.(true, true);
            return true;
          }
          return false;
        });

        chainHandler(this, 'onMouseMove', function (this: NodeLike, e: any, pos: [number, number]) {
          const L = ui.layout(this);
          const shift = !!e?.shiftKey;
          if (ui.strength.onPointerMove(pos[0], pos[1], L.strength, shift)) {
            syncStrength(this, ui);
            return true;
          }
          if (ui.editor.onPointerMove(pos[0], pos[1], L.editor, shift)) {
            this.setDirtyCanvas?.(true, true);
            return ui.editor.isDragging;
          }
          return false;
        });

        chainHandler(this, 'onMouseUp', function (this: NodeLike) {
          const a = ui.strength.onPointerUp();
          const b = ui.editor.onPointerUp();
          if (a || b) this.setDirtyCanvas?.(true, true);
          return a || b;
        });

        void loadHistogram(this, ui);
        return r;
      };

      // Reloading a saved workflow replaces widget values after creation, so
      // the editor has to re-read them rather than trusting what it built with.
      const onConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function (this: NodeLike, info: any) {
        const r = onConfigure?.apply(this, arguments as any);
        const ui = uis.get(this);
        if (ui) {
          ui.editor.state = readState(this);
          const sw = getWidget(this, 'strength');
          if (typeof sw?.value === 'number') ui.strength.value = sw.value;
          void loadHistogram(this, ui);
        }
        return r;
      };
    },
  });
}

function syncStrength(node: NodeLike, ui: CurvesUI): void {
  const w = getWidget(node, 'strength');
  if (w && w.value !== ui.strength.value) {
    w.value = ui.strength.value;
    w.callback?.(w.value);
  }
  node.setDirtyCanvas?.(true, true);
}
