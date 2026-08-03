/**
 * Hold-to-compare, as one global key listener rather than one per node.
 *
 * Held, not toggled: comparison is a glance, and a toggle leaves you unsure
 * which state you are looking at. Alt is the key, matching Lightroom's backslash
 * in spirit if not in letter — backslash is taken by ComfyUI.
 *
 * A single listener on window, with nodes registering a redraw callback, keeps
 * this from becoming a dozen listeners that each have to be torn down when a
 * node is deleted.
 */

const listeners = new Set<(held: boolean) => void>();
let held = false;
let installed = false;

function set(next: boolean): void {
  if (next === held) return;
  held = next;
  for (const fn of listeners) fn(held);
}

function install(): void {
  if (installed) return;
  installed = true;
  window.addEventListener('keydown', (e) => {
    if (e.key === 'Alt') set(true);
  });
  window.addEventListener('keyup', (e) => {
    if (e.key === 'Alt') set(false);
  });
  // Alt-tabbing away leaves the key stuck down otherwise, and the user comes
  // back to a node showing "before" with no way to tell why.
  window.addEventListener('blur', () => set(false));
}

/** True while the compare key is down. */
export function isComparing(): boolean {
  return held;
}

/** Register a redraw callback. Returns an unsubscribe function. */
export function onCompareChange(fn: (held: boolean) => void): () => void {
  install();
  listeners.add(fn);
  return () => listeners.delete(fn);
}
