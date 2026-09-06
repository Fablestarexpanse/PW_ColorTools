/**
 * The curve editor's interaction logic.
 *
 * ~3300 lines of TypeScript UI had no test runner at all — only `tsc` and the
 * bundle-freshness check stood behind it, and neither of those can tell you
 * that shift-click removes the wrong point. This file is where that starts.
 *
 * Only the host-free modules can be tested this way: anything importing
 * `comfy.ts` pulls in `/scripts/app.js`, which exists only in the browser.
 * `canvas/curve_editor.ts` is the largest module that does not, and it holds
 * the logic most likely to be wrong — hit-testing, insertion, the minimum-point
 * rule, and the clamping that keeps a curve monotone in x.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { CurveEditor, identityState } from '../src/canvas/curve_editor.ts';

const RECT = { x: 0, y: 0, w: 200, h: 200 };

/** Canvas coordinates for a curve-space point, mirroring `toCanvas`. */
function at(x: number, y: number): [number, number] {
  return [RECT.x + x * RECT.w, RECT.y + (1 - y) * RECT.h];
}

function editor(): CurveEditor {
  const e = new CurveEditor();
  e.state = identityState();
  return e;
}

describe('identity', () => {
  it('starts as two corner points on every channel', () => {
    const s = identityState();
    for (const ch of ['luma', 'r', 'g', 'b'] as const) {
      assert.deepEqual(s[ch], [[0, 0], [1, 1]]);
    }
  });

  it('gives each channel its own array', () => {
    const s = identityState();
    s.luma.push([0.5, 0.5]);
    assert.equal(s.r.length, 2, 'channels must not share one array');
  });
});

describe('adding points', () => {
  it('inserts a point where the canvas was clicked', () => {
    const e = editor();
    e.onPointerDown(...at(0.5, 0.75), RECT, false, 1000);
    assert.equal(e.points.length, 3);
    const [x, y] = e.points[1];
    assert.ok(Math.abs(x - 0.5) < 1e-6, `x was ${x}`);
    assert.ok(Math.abs(y - 0.75) < 1e-6, `y was ${y}`);
  });

  it('keeps points sorted by x', () => {
    const e = editor();
    for (const x of [0.8, 0.2, 0.5]) e.onPointerDown(...at(x, 0.5), RECT, false, 1000);
    const xs = e.points.map((p) => p[0]);
    assert.deepEqual(xs, [...xs].sort((a, b) => a - b));
  });

  it('refuses a point too close in x to an existing one', () => {
    const e = editor();
    e.onPointerDown(...at(0.5, 0.6), RECT, false, 1000);
    const before = e.points.length;
    // Far enough in y to miss the grab radius, but the same x.
    e.onPointerDown(...at(0.5, 0.05), RECT, false, 3000);
    assert.equal(e.points.length, before, 'two points at one x would not be a function');
  });

  it('notifies the node so it can re-bake', () => {
    const e = editor();
    let calls = 0;
    e.onChange = () => { calls += 1; };
    e.onPointerDown(...at(0.4, 0.6), RECT, false, 1000);
    assert.equal(calls, 1);
  });
});

describe('removing points', () => {
  it('shift-click removes the point under the cursor', () => {
    const e = editor();
    e.onPointerDown(...at(0.5, 0.6), RECT, false, 1000);
    assert.equal(e.points.length, 3);
    e.onPointerUp();
    e.onPointerDown(...at(0.5, 0.6), RECT, true, 2000);
    assert.equal(e.points.length, 2);
  });

  it('refuses to go below two points', () => {
    const e = editor();
    e.onPointerDown(...at(0, 0), RECT, true, 1000);
    assert.equal(e.points.length, 2, 'a curve with one point is not a curve');
    e.onPointerDown(...at(1, 1), RECT, true, 2000);
    assert.equal(e.points.length, 2);
  });
});

describe('dragging', () => {
  it('moves the grabbed point and clamps it to the panel', () => {
    const e = editor();
    e.onPointerDown(...at(0.5, 0.5), RECT, false, 1000);
    e.onPointerMove(RECT.x + 0.5 * RECT.w, RECT.y - 500, RECT, false);
    e.onPointerUp();
    assert.ok(e.points.every((p) => p[1] >= 0 && p[1] <= 1), 'y left [0,1]');
    assert.ok(e.points.some((p) => Math.abs(p[1] - 1) < 1e-6), 'drag above the panel should pin to 1');
  });

  it('does nothing on move when no point is grabbed', () => {
    const e = editor();
    const before = JSON.stringify(e.points);
    e.onPointerMove(10, 10, RECT, false);
    assert.equal(JSON.stringify(e.points), before);
  });
});

describe('channels', () => {
  it('edits only the selected channel', () => {
    const e = editor();
    e.channel = 'r';
    e.onPointerDown(...at(0.5, 0.9), RECT, false, 1000);
    assert.equal(e.state.r.length, 3);
    assert.equal(e.state.luma.length, 2, 'editing r must not touch luma');
    assert.equal(e.state.g.length, 2);
  });
});
