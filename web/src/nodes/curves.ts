/**
 * PW Curves — node wiring.
 *
 * Owns the layout, routes pointer events into the shared widgets, keeps the
 * `curves` widget value in sync so the graph serialises, and fetches the node's
 * own input histogram from the server route rather than waiting for a run.
 *
 * The panel is hosted on a DOM widget (see `widgets/panel.ts`), so it renders
 * in both the Classic and the Modern node design. Everything below is in
 * panel-local coordinates.
 */

import { fetchPw } from '../fetch.ts';
import { type NodeLike, app, getWidget } from '../comfy.ts';
import { PW } from '../theme.ts';
import { CurveEditor, identityState, type ChannelId, type CurveEditorState } from '../canvas/curve_editor.ts';
import { Preview } from '../canvas/preview.ts';
import { Lattice, DEFAULT_SIZE } from '../core/lattice.ts';
import { buildSampleFn } from '../core/ops.ts';
import { isComparing, onCompareChange } from '../widgets/compare.ts';
import { onRunComplete } from '../widgets/run_events.ts';
import { addResetMenu, resetNode } from '../widgets/reset.ts';
import { Segmented } from '../widgets/segmented.ts';
import { BADGE } from '../theme.ts';
import { headerChip, hit, sectionHeader, type Rect } from '../widgets/draw.ts';
import { attachPanel, fitNode, hideSerialisationWidget, panelOf, type Panel } from '../widgets/panel.ts';

const M = PW.metrics;
const HEADER_H = 18;
const TABS_H = M.controlHeight;
const MIN_EDITOR_H = 160;
const PREVIEW_H = 140;
const MIN_WIDTH = 360;
/** The panel's floor: everything fixed plus the smallest usable editor. */
const PANEL_MIN_H = HEADER_H + 4 + PREVIEW_H + M.gapControl + TABS_H + M.gapControl + MIN_EDITOR_H + M.padding;

const CHANNEL_TABS = [
  { id: 'luma', label: 'Luma', colour: PW.channel.luma },
  { id: 'r', label: 'R', colour: PW.channel.r },
  { id: 'g', label: 'G', colour: PW.channel.g },
  { id: 'b', label: 'B', colour: PW.channel.b },
];

interface CurvesUI {
  editor: CurveEditor;
  tabs: Segmented;
  preview: Preview;
  /** Re-bake the lattice the preview samples. Cheap enough to run per edit. */
  rebake: (node: NodeLike) => void;
}

const uis = new WeakMap<object, CurvesUI>();

/** The editor takes whatever height is left below the fixed rows. */
function layout(w: number, h: number): { header: Rect; preview: Rect; tabs: Rect; editor: Rect } {
  let y = 0;
  const header = { x: 0, y, w, h: HEADER_H };
  y += HEADER_H + 4;
  const preview = { x: 0, y, w, h: PREVIEW_H };
  y += PREVIEW_H + M.gapControl;
  const tabs = { x: 0, y, w, h: TABS_H };
  y += TABS_H + M.gapControl;
  const editor = { x: 0, y, w, h: Math.max(MIN_EDITOR_H, h - y - M.padding) };
  return { header, preview, tabs, editor };
}

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
  panelOf(node)?.invalidate();
}

/**
 * Fetch the node's own input histogram. This is the fix for the limitation
 * every other pack ships with — the editor can draw a real histogram the moment
 * you open it, not only after the graph has been run once.
 */
async function loadHistogram(node: NodeLike, ui: CurvesUI): Promise<void> {
  try {
    const res = await fetchPw(`/pw_color/histogram/${node.id}`);
    if (!res.ok) return; // 404 simply means nothing cached yet
    const data = await res.json();
    const h = data.histogram;
    ui.editor.histogram = {
      luma: Float32Array.from(h.luma),
      r: Float32Array.from(h.r),
      g: Float32Array.from(h.g),
      b: Float32Array.from(h.b),
    };
    panelOf(node)?.invalidate();
  } catch {
    // Offline or route not registered. The editor works without a histogram.
  }
}

function makeUI(node: NodeLike): CurvesUI {
  const editor = new CurveEditor();
  const tabs = new Segmented(CHANNEL_TABS);
  const preview = new Preview();

  const rebake = (n: NodeLike) => {
    // The preview samples the same lattice the renderer will build from the
    // same control points, so what is on screen is what will be rendered.
    const op = {
      type: 'curves',
      params: {
        ...editor.state,
        preserve_hue: getWidget(n, 'preserve_hue')?.value !== false,
      },
      strength: typeof getWidget(n, 'strength')?.value === 'number' ? getWidget(n, 'strength')!.value : 1,
    };
    preview.lattice = Lattice.fromFn(buildSampleFn([op]) as any, DEFAULT_SIZE);
    preview.digest = JSON.stringify([op.params, op.strength]);
  };

  const ui: CurvesUI = { editor, tabs, preview, rebake };
  editor.state = readState(node);
  editor.onChange = () => {
    writeState(node, ui);
    rebake(node);
  };
  rebake(node);
  return ui;
}

export function registerCurves(): void {
  app.registerExtension({
    name: 'pw.color.curves',
    async beforeRegisterNodeDef(nodeType: any, nodeData: any) {
      if (nodeData?.name !== 'PW_Curves') return;

      // The curve editor holds state the widgets do not, so a plain widget
      // reset would leave the curve drawn but the node claiming defaults.
      addResetMenu(nodeType, (node) => ({
        after: () => {
          const ui = uis.get(node);
          if (!ui) return;
          ui.editor.resetAll();
          ui.rebake(node);
          panelOf(node)?.invalidate();
        },
      }));

      const onCreated = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (this: NodeLike) {
        const r = onCreated?.apply(this, arguments as any);
        const ui = makeUI(this);
        uis.set(this, ui);

        // `curves` is the serialisation channel, not a control — the editor
        // below is how you set it. Every other control is a native ComfyUI
        // widget, so it looks and behaves like the rest of the app.
        hideSerialisationWidget(this, 'curves');

        const reset = () =>
          resetNode(this, {
            after: () => {
              ui.editor.resetAll();
              ui.rebake(this);
            },
          });

        const panel: Panel = attachPanel(this, {
          minWidth: MIN_WIDTH,
          height: () => PANEL_MIN_H,
          draw: (ctx, rr) => {
            const L = layout(rr.w, rr.h);
            sectionHeader(ctx, 'Curves', L.header, BADGE.lut);
            headerChip(ctx, L.header, 'reset', BADGE.lut.label);
            ui.preview.comparing = isComparing();
            ui.preview.draw(ctx, L.preview);
            ui.tabs.draw(ctx, L.tabs);
            ui.editor.draw(ctx, L.editor);
          },
          onPointerDown: (x, y, m) => {
            const L = layout(panel.width, panel.height);
            if (hit(headerChip(panel.context, L.header, 'reset', BADGE.lut.label), x, y, 3)) {
              reset();
              return true;
            }
            const tab = ui.tabs.onPointerDown(x, y, L.tabs);
            if (tab) {
              ui.editor.channel = tab as ChannelId;
              return true;
            }
            if (hit(L.preview, x, y)) {
              ui.preview.onPointerDown(x, y, L.preview, m.shift, m.double);
              return true;
            }
            if (hit(L.editor, x, y)) {
              ui.editor.onPointerDown(x, y, L.editor, m.shift, m.time);
              return true;
            }
            return false;
          },
          onPointerMove: (x, y, m) => {
            const L = layout(panel.width, panel.height);
            if (ui.preview.onPointerMove(x, y, L.preview)) return true;
            return ui.editor.onPointerMove(x, y, L.editor, m.shift);
          },
          onPointerUp: () => {
            // Both must run, so they are called before the OR rather than in it.
            const editor = ui.editor.onPointerUp();
            const preview = ui.preview.onPointerUp();
            return editor || preview;
          },
          onWheel: (x, y, delta) => {
            const L = layout(panel.width, panel.height);
            return hit(L.preview, x, y) && ui.preview.onWheel(x, y, L.preview, delta);
          },
        });
        const repaint = () => panel.invalidate();

        const refresh = () => {
          void ui.preview.load(this.id, repaint);
          void loadHistogram(this, ui);
        };
        // Not yet: onNodeCreated fires from the constructor, before the node
        // has its id, so a fetch now asks for the wrong proxy — and its
        // in-flight flag then blocks the right one from onConfigure. By the
        // next tick a loaded node is configured and a new one is in the graph.
        setTimeout(refresh, 0);

        // A global key listener, so holding the compare key redraws every
        // PW node at once rather than only the one under the cursor.
        const stopCompare = onCompareChange(repaint);
        // PW Curves returns no `ui` data, so `onExecuted` never fires for it.
        // The prompt-level events are what tell us a proxy is now cached.
        const stopRun = onRunComplete(refresh);
        const priorRemoved = this.onRemoved;
        this.onRemoved = function (this: NodeLike) {
          stopCompare();
          stopRun();
          priorRemoved?.call(this);
        };

        return r;
      };

      // Reloading a saved workflow replaces widget values after creation, so
      // the editor has to re-read them rather than trusting what it built with.
      const onConfigure = nodeType.prototype.onConfigure;
      nodeType.prototype.onConfigure = function (this: NodeLike, info: any) {
        const r = onConfigure?.apply(this, arguments as any);
        const ui = uis.get(this);
        const panel = panelOf(this);
        if (ui && panel) {
          // The stored size is applied after creation; re-fit or a node saved
          // before the panel existed comes back clipped.
          fitNode(this, panel);
          ui.editor.state = readState(this);
          ui.rebake(this);
          void loadHistogram(this, ui);
          void ui.preview.load(this.id, () => panel.invalidate());
          panel.invalidate();
        }
        return r;
      };

      // The preview follows the strength and preserve-hue widgets too.
      const onWidgetChanged = nodeType.prototype.onWidgetChanged;
      nodeType.prototype.onWidgetChanged = function (this: NodeLike) {
        const res = onWidgetChanged?.apply(this, arguments as any);
        const ui = uis.get(this);
        if (ui) {
          ui.rebake(this);
          panelOf(this)?.invalidate();
        }
        return res;
      };
    },
  });
}
