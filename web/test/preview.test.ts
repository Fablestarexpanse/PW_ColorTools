/**
 * The preview panel's interaction guards.
 *
 * `Preview` needs a WebGL context to draw, which node cannot give it — but the
 * guards that decide whether a pointer event does anything are pure, and they
 * are where the bug was: the renderer draws `output ?? source` while the
 * handlers demanded `source`, so a node showing only its output rendered an
 * image you could not pan or zoom, with nothing on screen to say why.
 *
 * That state is reachable in normal use. The input and output caches are two
 * independent LRUs on the server, so a busy graph can evict one node's input
 * proxy while its output entry survives.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { Preview } from '../src/canvas/preview.ts';

const RECT = { x: 0, y: 0, w: 300, h: 200 };

/** Stand-in for a decoded texture; the guards only check for non-null. */
function tex(): any {
  return { width: 64, height: 48 };
}

function preview(): Preview {
  return new Preview();
}

describe('hasImage', () => {
  it('is false with nothing loaded', () => {
    assert.equal(preview().hasImage, false);
  });

  it('is true with only an input', () => {
    const p = preview();
    p.source = tex();
    assert.equal(p.hasImage, true);
  });

  it('is true with only an output', () => {
    const p = preview();
    p.output = tex();
    assert.equal(p.hasImage, true);
  });
});

describe('pointer guards', () => {
  it('ignore events when there is nothing to show', () => {
    const p = preview();
    assert.equal(p.onPointerDown(10, 10, RECT, false, false), false);
    assert.equal(p.onWheel(10, 10, RECT, -1), false);
    assert.equal(p.onPointerMove(20, 20, RECT), false);
  });

  it('accept events on an output-only preview', () => {
    // The regression: this used to return false, so a spatial node whose input
    // proxy had been evicted drew an image that could not be panned.
    const p = preview();
    p.output = tex();
    assert.equal(p.onPointerDown(10, 10, RECT, false, false), true, 'drag should start');
    assert.equal(p.onWheel(10, 10, RECT, -1), true, 'wheel should zoom');
  });

  it('accept events on an input-only preview', () => {
    const p = preview();
    p.source = tex();
    assert.equal(p.onPointerDown(10, 10, RECT, false, false), true);
  });

  it('report a drag as handled only while one is in progress', () => {
    const p = preview();
    p.output = tex();
    assert.equal(p.onPointerUp(), false, 'nothing was dragging');
    p.onPointerDown(10, 10, RECT, false, false);
    assert.equal(p.onPointerUp(), true);
  });

  it('treat a double click as reset rather than as a drag', () => {
    const p = preview();
    p.output = tex();
    assert.equal(p.onPointerDown(10, 10, RECT, false, true), true);
    assert.equal(p.onPointerUp(), false, 'a reset should not leave a drag open');
  });
});
