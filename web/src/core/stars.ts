/**
 * Where the stars are, and what clicking one means.
 *
 * Host-free on purpose. This is the part of the review panel that decides
 * what a click did, which is worth being able to test without a browser.
 */

export const STARS = 5;

export interface Cell {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface StarHit {
  index: number;
  star: number;
}

/**
 * The star a point lands on, or null.
 *
 * The row sits along the bottom of each cell, `starSize` tall and one
 * `starSize` wide per star. A cell is usually wider than the row, so the
 * space to the right of the fifth star is deliberately not the fifth star:
 * clicking there should do nothing rather than quietly award five.
 */
export function starHit(x: number, y: number, cells: Cell[], starSize: number): StarHit | null {
  for (let index = 0; index < cells.length; index++) {
    const c = cells[index];
    const top = c.y + c.h - starSize;
    if (x < c.x || x > c.x + c.w || y < top || y > c.y + c.h) continue;
    const star = Math.floor((x - c.x) / starSize) + 1;
    if (star < 1 || star > STARS) return null;
    return { index, star };
  }
  return null;
}

/**
 * Clicking the star you already chose clears the rating.
 *
 * Without it there is no way back to unrated, and unrated is how an image is
 * rejected — so the gesture that rejects has to be reachable by the same
 * clicks that rate.
 */
export function nextRating(current: number, clicked: number): number {
  return current === clicked ? 0 : clicked;
}

/** How many images would leave the gate. */
export function keptCount(ratings: number[]): number {
  return ratings.filter((r) => r > 0).length;
}

/**
 * The re-run button: the slot just past the fifth star in a cell's star row.
 * Returns the cell's index, or null.
 */
export function rerunHit(x: number, y: number, cells: Cell[], starSize: number): number | null {
  for (let index = 0; index < cells.length; index++) {
    const c = cells[index];
    const top = c.y + c.h - starSize;
    const left = c.x + STARS * starSize;
    if (x >= left && x <= c.x + c.w && y >= top && y <= c.y + c.h) return index;
  }
  return null;
}

/** Smallest and largest focus view a drag of the handle can make. */
export const VIEW_MIN = 120;
export const VIEW_MAX = 2400;

export function clampView(h: number): number {
  return Math.round(Math.min(VIEW_MAX, Math.max(VIEW_MIN, h)));
}

/**
 * How tall the focus view draws.
 *
 * `base` is the height set with the handle. A node dragged taller than the
 * panel needs (`spare` above zero) gives the extra to the view too, so both
 * the handle and the node's own corner make the preview bigger. Space taken
 * by new rows of thumbnails comes out of that extra first, never out of
 * `base`.
 */
export function viewHeight(base: number, spare: number): number {
  return clampView(base + Math.max(0, spare));
}
