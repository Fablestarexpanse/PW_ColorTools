/**
 * Click a value to type it.
 *
 * A DOM input positioned over the canvas, rather than a canvas-drawn text field:
 * text editing is caret placement, selection, IME, clipboard and accessibility,
 * and reimplementing that on a 2D context is how node packs end up with a text
 * box that cannot be pasted into.
 *
 * The input is created on demand, positioned in page coordinates over the node,
 * and removed on commit or cancel. Enter commits, Escape cancels, blur commits —
 * which is what every other numeric field in every other tool does.
 */

import { PW } from '../theme.ts';
import type { Rect } from './draw.ts';

let active: HTMLInputElement | null = null;

/** Convert a LiteGraph node-local rect to page coordinates. */
function toPage(canvas: HTMLCanvasElement, node: any, r: Rect): Rect {
  const ds = (canvas as any).__pwds ?? null;
  const app = (globalThis as any).app;
  const scale = app?.canvas?.ds?.scale ?? 1;
  const [ox, oy] = app?.canvas?.ds?.offset ?? [0, 0];
  const box = canvas.getBoundingClientRect();
  void ds;
  return {
    x: box.left + (node.pos[0] + r.x + ox) * scale,
    y: box.top + (node.pos[1] + r.y + oy) * scale,
    w: r.w * scale,
    h: r.h * scale,
  };
}

export interface NumericEntryOptions {
  value: number;
  min?: number;
  max?: number;
  decimals?: number;
  onCommit: (value: number) => void;
}

/** Open an editor over `rect`. Any previously open editor is committed first. */
export function openNumericEntry(node: any, rect: Rect, opts: NumericEntryOptions): void {
  close();
  const app = (globalThis as any).app;
  const canvas: HTMLCanvasElement | undefined = app?.canvas?.canvas;
  if (!canvas) return;

  const page = toPage(canvas, node, rect);
  const input = document.createElement('input');
  input.type = 'text';
  input.value = opts.value.toFixed(opts.decimals ?? 2);
  Object.assign(input.style, {
    position: 'fixed',
    left: `${page.x}px`,
    top: `${page.y}px`,
    width: `${Math.max(48, page.w)}px`,
    height: `${Math.max(18, page.h)}px`,
    zIndex: '9999',
    background: PW.color.surface,
    color: PW.color.text,
    border: `1px solid ${PW.color.accent}`,
    borderRadius: `${PW.metrics.radiusControl}px`,
    font: PW.font.mono,
    textAlign: 'right',
    padding: '0 4px',
    outline: 'none',
  } as CSSStyleDeclaration);

  let done = false;
  const commit = (apply: boolean) => {
    if (done) return;
    done = true;
    const raw = input.value.trim();
    input.remove();
    if (active === input) active = null;
    if (!apply || raw === '') return;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return;
    let v = parsed;
    if (opts.min !== undefined) v = Math.max(opts.min, v);
    if (opts.max !== undefined) v = Math.min(opts.max, v);
    opts.onCommit(v);
  };

  input.addEventListener('keydown', (e) => {
    // Stop ComfyUI's global shortcuts from eating the keystrokes.
    e.stopPropagation();
    if (e.key === 'Enter') commit(true);
    else if (e.key === 'Escape') commit(false);
  });
  input.addEventListener('blur', () => commit(true));

  document.body.appendChild(input);
  input.focus();
  input.select();
  active = input;
}

/** Commit and remove any open editor. */
export function close(): void {
  active?.blur();
  active = null;
}
