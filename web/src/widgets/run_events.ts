/**
 * "The graph just finished" — one subscription, shared by every node.
 *
 * Nodes cache their decoded input server-side during execution, and the UI
 * fetches that proxy to draw previews, histograms and preset thumbnails. The
 * question is *when* to fetch.
 *
 * `onExecuted` is the obvious hook and it is the wrong one: ComfyUI only emits
 * an `executed` message for nodes that return `ui` data. PW Palette does; PW
 * Look and PW Curves do not, so their `onExecuted` never fires and the fetch
 * that lived there never ran. The result was a preview that stayed on "run the
 * graph once" forever, because the only other fetch happened at node creation,
 * before anything had been cached.
 *
 * So: listen to the prompt-level events instead. Those fire regardless of what
 * any individual node returns.
 */

import { api } from '../comfy.ts';

const listeners = new Set<() => void>();
let installed = false;
let pending: ReturnType<typeof setTimeout> | null = null;

function fire(): void {
  // Debounced: `executed` arrives once per UI-producing node, and a graph with
  // several of them would otherwise trigger a burst of identical fetches.
  if (pending) clearTimeout(pending);
  pending = setTimeout(() => {
    pending = null;
    for (const fn of listeners) {
      try {
        fn();
      } catch (err) {
        console.warn('[PW Color] run listener failed', err);
      }
    }
  }, 80);
}

function install(): void {
  if (installed) return;
  installed = true;
  // execution_success covers the normal path. execution_error still leaves
  // proxies cached for whichever nodes ran before the failure, and those are
  // exactly the ones a user is about to look at to work out what went wrong.
  for (const name of ['execution_success', 'execution_error', 'executed']) {
    api.addEventListener(name, fire);
  }
}

/**
 * Run `fn` shortly after the graph finishes. Returns an unsubscribe function,
 * which nodes must call from `onRemoved` or a deleted node keeps fetching.
 */
export function onRunComplete(fn: () => void): () => void {
  install();
  listeners.add(fn);
  return () => listeners.delete(fn);
}
