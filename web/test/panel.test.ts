/**
 * The panel host: a device-pixel canvas that draws in local CSS coordinates.
 *
 * Everything that touches the DOM lives in `attachPanel`, which node cannot
 * load. What it *can* test is the part that has to be right for every node in
 * the pack at once: backing-store scaling, the local coordinate translation
 * every hit test depends on, and redraw coalescing — a panel that repainted on
 * every pointermove during a drag would be the first thing a user noticed.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { Panel, type PanelSpec } from '../src/widgets/panel.ts';

function fakeCanvas(left = 10, top = 20) {
  const calls: string[] = [];
  const ctx: any = {
    setTransform: (...a: number[]) => calls.push('setTransform ' + a.join(',')),
    clearRect: (...a: number[]) => calls.push('clearRect ' + a.join(',')),
  };
  const c: any = {
    width: 0,
    height: 0,
    getContext: () => ctx,
    getBoundingClientRect: () => ({ left, top, width: c.width, height: c.height }),
  };
  return { c, ctx, calls };
}

function spec(over: Partial<PanelSpec> = {}): PanelSpec {
  return { minWidth: 100, height: () => 50, draw: () => {}, ...over };
}

function env(dpr = 2) {
  const q: (() => void)[] = [];
  return { env: { dpr: () => dpr, schedule: (f: () => void) => q.push(f) }, q };
}

const MODS = { shift: false, double: false, time: 0 };

describe('Panel sizing', () => {
  it('scales the backing store by device pixel ratio and draws in CSS pixels', () => {
    const { c, calls } = fakeCanvas();
    const drawn: any[] = [];
    const p = new Panel(c, spec({ draw: (_ctx, r) => drawn.push({ ...r }) }), env(2).env);
    p.resize(300, 150);
    assert.equal(c.width, 600);
    assert.equal(c.height, 300);
    assert.ok(calls.includes('setTransform 2,0,0,2,0,0'));
    assert.deepEqual(drawn.at(-1), { x: 0, y: 0, w: 300, h: 150 });
    assert.equal(p.width, 300);
    assert.equal(p.height, 150);
  });

  it('does not draw into a zero-sized canvas', () => {
    const { c } = fakeCanvas();
    let n = 0;
    const p = new Panel(c, spec({ draw: () => n++ }), env().env);
    p.resize(0, 0);
    assert.equal(n, 0);
  });
});

describe('Panel invalidate', () => {
  it('coalesces a burst into one scheduled draw', () => {
    const { c } = fakeCanvas();
    let n = 0;
    const { env: e, q } = env();
    const p = new Panel(c, spec({ draw: () => n++ }), e);
    p.resize(100, 50);
    n = 0;
    p.invalidate();
    p.invalidate();
    p.invalidate();
    assert.equal(q.length, 1);
    q[0]();
    assert.equal(n, 1);
    p.invalidate();
    assert.equal(q.length, 2, 'a new burst schedules again once the last one drew');
  });

  it('does nothing after dispose', () => {
    const { c } = fakeCanvas();
    let n = 0;
    const { env: e, q } = env();
    const p = new Panel(c, spec({ draw: () => n++ }), e);
    p.resize(100, 50);
    n = 0;
    p.dispose();
    p.invalidate();
    for (const f of q) f();
    assert.equal(n, 0);
  });
});

describe('Panel pointer routing', () => {
  it('translates client coordinates to local ones', () => {
    const { c } = fakeCanvas(10, 20);
    const seen: number[][] = [];
    const p = new Panel(c, spec({ onPointerDown: (x, y) => (seen.push([x, y]), true) }), env().env);
    p.resize(100, 50);
    assert.equal(p.pointerDown(15, 27, MODS), true);
    assert.deepEqual(seen, [[5, 7]]);
  });

  it('divides by the graph scale, so a zoomed-out node still hits in node units', () => {
    const { c } = fakeCanvas(10, 20);
    const seen: number[][] = [];
    const p = new Panel(c, spec({ onPointerMove: (x, y) => (seen.push([x, y]), true) }), env().env);
    p.resize(100, 50);
    p.scale = 0.5;
    p.pointerMove(20, 30, MODS);
    assert.deepEqual(seen, [[20, 20]]);
  });

  it('reports unhandled when the spec has no handler', () => {
    const { c } = fakeCanvas();
    const p = new Panel(c, spec(), env().env);
    p.resize(100, 50);
    assert.equal(p.pointerDown(1, 1, MODS), false);
    assert.equal(p.pointerUp(1, 1, MODS), false);
    assert.equal(p.wheel(1, 1, 3), false);
  });

  it('redraws after a handled event and not after an unhandled one', () => {
    const { c } = fakeCanvas();
    const { env: e, q } = env();
    const p = new Panel(c, spec({ onWheel: (_x, _y, d) => d < 0 }), e);
    p.resize(100, 50);
    p.wheel(5, 5, 100);
    assert.equal(q.length, 0);
    p.wheel(5, 5, -100);
    assert.equal(q.length, 1);
  });
});
