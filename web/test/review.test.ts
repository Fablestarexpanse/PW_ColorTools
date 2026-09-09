/**
 * The rating gestures, as pure functions.
 *
 * The panel around them needs a browser; these do not, and they are where the
 * behaviour a user would notice lives: which star a click lands on, and what
 * clicking the star you already chose should do.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { STARS, keptCount, nextRating, starHit } from '../src/core/stars.ts';

const CELLS = [
  { x: 0, y: 0, w: 100, h: 80 },
  { x: 110, y: 0, w: 100, h: 80 },
];
const STAR = 16;

describe('starHit', () => {
  it('finds the star under the pointer', () => {
    assert.deepEqual(starHit(8, 72, CELLS, STAR), { index: 0, star: 1 });
    assert.deepEqual(starHit(40, 72, CELLS, STAR), { index: 0, star: 3 });
  });

  it('knows which cell the click was in', () => {
    assert.deepEqual(starHit(118, 72, CELLS, STAR), { index: 1, star: 1 });
  });

  it('is null above the star row, where the thumbnail is', () => {
    assert.equal(starHit(8, 10, CELLS, STAR), null);
  });

  it('is null in the gap between cells', () => {
    assert.equal(starHit(105, 72, CELLS, STAR), null);
  });

  it('is null past the last cell', () => {
    assert.equal(starHit(400, 72, CELLS, STAR), null);
  });

  it('never returns a star past the end of the scale', () => {
    // A cell wider than five stars has empty space to the right of them.
    assert.equal(starHit(95, 72, CELLS, STAR), null);
    const last = starHit(79, 72, CELLS, STAR);
    assert.ok(last && last.star === STARS);
  });
});

describe('nextRating', () => {
  it('sets the star that was clicked', () => {
    assert.equal(nextRating(0, 4), 4);
    assert.equal(nextRating(2, 5), 5);
    assert.equal(nextRating(5, 1), 1);
  });

  it('clicking the current rating clears it, which is the only way to reject', () => {
    assert.equal(nextRating(3, 3), 0);
    assert.equal(nextRating(1, 1), 0);
  });
});

describe('keptCount', () => {
  it('counts only the rated', () => {
    assert.equal(keptCount([5, 0, 3, 0]), 2);
    assert.equal(keptCount([0, 0]), 0);
    assert.equal(keptCount([]), 0);
  });
});
