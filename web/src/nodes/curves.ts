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
import { Preview } from '../canvas/preview.ts';
import { Lattice, DEFAULT_SIZE } from '../core/lattice.ts';
import { buildSampleFn } from '../core/ops.ts';
import { isComparing, onCompareChange } from '../widgets/compare.ts';
import { onRunComplete } from '../widgets/run_events.ts';
import { addResetMenu, resetNode } from '../widgets/reset.ts';
import { Segmented } from '../widgets/segmented.ts';
import { BADGE } from '../theme.ts';
import { headerChip, hit, sectionHeader, type Ctx, type Rect } from '../widgets/draw.ts';
import { ensureHeight, fitPanel, widgetHeight } from '../widgets/layout.ts';

/** The live 2D context, for measuring chips during hit tests. */
const ctx0 = (): Ctx | null => (globalThis as any).app?.canvas?.ctx ?? null;

const M = PW.metrics;
const HEADER_H = 18;
const TABS_H = M.controlHeight;
const ROW_H = M.controlHeight;
const MIN_EDITOR_H = 160;
const PREVIEW_H = 140;

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
  layout: (node: NodeLike) => {
    header: Rect;
    preview: Rect;
    tabs: Rect;
    editor: Rect;
  };
  /** Re-bake the lattice the preview samples. Cheap enough to run per edit. */
  rebake: (node: NodeLike) => void;
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
  const layout = (n: NodeLike) => {
    const x = M.padding;
    const w = n.size[0] - M.padding * 2;
    // Start below whatever widgets ComfyUI drew, measured rather than counted:
    // hidden widgets report a negative height, so counting rows over-estimates
    // and leaves a gap that grows every time we hide another control.
    let y = widgetHeight(n) + M.gapSection;
    const header = { x, y, w, h: HEADER_H };
    y += HEADER_H + 4;
    const previewR = { x, y, w, h: PREVIEW_H };
    y += PREVIEW_H + M.gapControl;
    const tabsR = { x, y, w, h: TABS_H };
    y += TABS_H + M.gapControl;
    const editorH = Math.max(MIN_EDITOR_H, n.size[1] - y - M.gapSection - M.padding);
    return { header, preview: previewR, tabs: tabsR, editor: { x, y, w, h: editorH } };
  };

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

  const ui: CurvesUI = { editor, tabs, preview, layout, rebake };
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
        for (const name of ['curves']) {
          const w = getWidget(this, name);
          if (!w) continue;
          w.type = 'hidden';
          w.computeSize = () => [0, -4];
        }

        // Widget height from LiteGraph, editor height from us. The editor gets
        // a generous default because a curve you cannot see is not editable.
        fitPanel(
          this,
          HEADER_H + PREVIEW_H + TABS_H + MIN_EDITOR_H + ROW_H + M.gapSection * 2 + M.gapControl * 3 + M.padding,
          360,
        );

        const refresh = () => {
          void ui.preview.load(this.id, () => this.setDirtyCanvas?.(true, true));
          void loadHistogram(this, ui);
        };
        refresh();

        // A global key listener, so holding the compare key redraws every
        // PW node at once rather than only the one under the cursor.
        const stopCompare = onCompareChange(() => this.setDirtyCanvas?.(true, true));
        // PW Curves returns no `ui` data, so `onExecuted` never fires for it.
        // The prompt-level events are what tell us a proxy is now cached.
        const stopRun = onRunComplete(refresh);
        const priorRemoved = this.onRemoved;
        this.onRemoved = function (this: NodeLike) {
          stopCompare();
          stopRun();
          priorRemoved?.call(this);
        };

        chainHandler(this, 'onDrawForeground', function (this: NodeLike, ctx: Ctx) {
          if ((this as any).flags?.collapsed) return;
          if (ensureHeight(this, HEADER_H + PREVIEW_H + TABS_H + MIN_EDITOR_H + ROW_H + M.gapSection * 2 + M.gapControl * 3 + M.padding, 360)) this.setDirtyCanvas?.(true, true);
          const L = ui.layout(this);
          sectionHeader(ctx, 'Curves', L.header, BADGE.lut);
          headerChip(ctx, L.header, 'reset', BADGE.lut.label);
          ui.preview.comparing = isComparing();
          ui.preview.draw(ctx, L.preview);
          ui.tabs.draw(ctx, L.tabs);
          ui.editor.draw(ctx, L.editor);
        });

        chainHandler(this, 'onMouseDown', function (this: NodeLike, e: any, pos: [number, number]) {
          const L = ui.layout(this);
          const [x, y] = pos;
          const now = e?.timeStamp ?? 0;
          const shift = !!e?.shiftKey;

          if (hit(headerChip(ctx0(), L.header, 'reset', BADGE.lut.label), x, y, 3)) {
            resetNode(this, {
              after: () => {
                ui.editor.resetAll();
                ui.rebake(this);
              },
            });
            return true;
          }

          const tab = ui.tabs.onPointerDown(x, y, L.tabs);
          if (tab) {
            ui.editor.channel = tab as ChannelId;
            this.setDirtyCanvas?.(true, true);
            return true;
          }
          if (hit(L.preview, x, y)) {
            ui.preview.onPointerDown(x, y, L.preview, shift, e?.detail === 2);
            this.setDirtyCanvas?.(true, true);
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

        chainHandler(this, 'onMouseWheel', function (this: NodeLike, e: any, pos: [number, number]) {
          const L = ui.layout(this);
          if (!hit(L.preview, pos[0], pos[1])) return false;
          const delta = e?.deltaY ?? -(e?.wheelDelta ?? 0);
          if (ui.preview.onWheel(pos[0], pos[1], L.preview, delta)) {
            e?.preventDefault?.();
            e?.stopPropagation?.();
            this.setDirtyCanvas?.(true, true);
            return true;
          }
          return false;
        });

        chainHandler(this, 'onMouseMove', function (this: NodeLike, e: any, pos: [number, number]) {
          const L = ui.layout(this);
          const shift = !!e?.shiftKey;
          if (ui.preview.onPointerMove(pos[0], pos[1], L.preview)) {
            this.setDirtyCanvas?.(true, true);
            return true;
          }
          if (ui.editor.onPointerMove(pos[0], pos[1], L.editor, shift)) {
            this.setDirtyCanvas?.(true, true);
            return ui.editor.isDragging;
          }
          return false;
        });

        chainHandler(this, 'onMouseUp', function (this: NodeLike) {
          const a = false;
          const b = ui.editor.onPointerUp();
          const c = ui.preview.onPointerUp();
          if (a || b || c) this.setDirtyCanvas?.(true, true);
          return a || b || c;
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
          // A workflow saved before the preview existed carries a size that is
          // now too short, and it is applied after creation — so re-fit here
          // or the panel is clipped.
          fitPanel(
            this,
            HEADER_H + PREVIEW_H + TABS_H + MIN_EDITOR_H + ROW_H + M.gapSection * 2 + M.gapControl * 3 + M.padding,
            360,
          );
          ui.editor.state = readState(this);
          ui.rebake(this);
          void loadHistogram(this, ui);
          void ui.preview.load(this.id, () => this.setDirtyCanvas?.(true, true));
        }
        return r;
      };
    },
  });
}

