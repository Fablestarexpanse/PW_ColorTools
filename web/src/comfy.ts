/**
 * The ComfyUI frontend surface we depend on, in one file.
 *
 * Two reasons this exists rather than importing `/scripts/app.js` everywhere:
 *
 * 1. The frontend moves fast. Confining every touch point to one module means
 *    a breaking change is a handful of lines here rather than a hunt through
 *    every node file.
 * 2. `app` and `api` are provided by the host page, not bundled. Declaring the
 *    shape we rely on keeps the rest of the codebase type-checked against a
 *    contract we control.
 *
 * Verified against ComfyUI 0.29.2 / comfyui-frontend-package 1.47.11.
 */

// The host resolves these at runtime; esbuild leaves them external.
// @ts-ignore - provided by ComfyUI, no types published
import { app } from '/scripts/app.js';
// @ts-ignore
import { api } from '/scripts/api.js';

export { app, api };

/** Lowest frontend we have actually tested against. */
export const MIN_FRONTEND = [1, 40, 0] as const;

export interface WidgetLike {
  name: string;
  type: string;
  value: any;
  options?: Record<string, any>;
  callback?: (value: any) => void;
  computeSize?: (width: number) => [number, number];
  serialize?: boolean;
}

export interface NodeLike {
  id: number | string;
  type: string;
  size: [number, number];
  widgets?: WidgetLike[];
  graph?: unknown;
  setDirtyCanvas?: (fg: boolean, bg?: boolean) => void;
  onRemoved?: () => void;
  onMouseDown?: (...args: any[]) => any;
  onMouseMove?: (...args: any[]) => any;
  onMouseUp?: (...args: any[]) => any;
  /** Wheel over the node body. Present on LiteGraph nodes in frontend 1.4x. */
  onMouseWheel?: (...args: any[]) => any;
  onResize?: (...args: any[]) => any;
  onDrawForeground?: (...args: any[]) => any;
}

export function getWidget(node: NodeLike, name: string): WidgetLike | undefined {
  return node.widgets?.find((w) => w.name === name);
}

/**
 * Install a handler that runs *before* the existing one and does not replace it.
 *
 * ComfyUI 0.27+ / frontend 1.4x: subgraph header buttons are implemented with
 * the node's own `onMouseDown`. Replacing the handler outright silently breaks
 * entering and leaving subgraphs — the bug the Olm node pack had to patch. Any
 * pointer handler we install must go through here.
 */
export function chainHandler<K extends keyof NodeLike>(node: NodeLike, key: K, handler: Function): void {
  const original = node[key] as unknown as Function | undefined;
  (node as any)[key] = function (this: unknown, ...args: any[]) {
    const ours = handler.apply(this, args);
    const theirs = original ? original.apply(this, args) : undefined;
    // Either handler consuming the event is enough to consume it.
    return ours || theirs;
  };
}

/** Parse the frontend version the host reports, or null if it will not say. */
export function frontendVersion(): number[] | null {
  const raw =
    (globalThis as any).__COMFYUI_FRONTEND_VERSION__ ??
    (app as any)?.frontendVersion ??
    (app as any)?.extensionManager?.version;
  if (typeof raw !== 'string') return null;
  const m = raw.match(/(\d+)\.(\d+)\.(\d+)/);
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}

/** Warn once if the host is older than we have tested. Never throw: a warning
 *  the user can act on beats a pack that refuses to load. */
export function warnIfUnsupported(): void {
  const v = frontendVersion();
  if (!v) return;
  const [a, b] = v;
  const [minA, minB] = MIN_FRONTEND;
  if (a < minA || (a === minA && b < minB)) {
    console.warn(
      `[PW Color] ComfyUI frontend ${v.join('.')} is older than the tested minimum ` +
        `${MIN_FRONTEND.join('.')}. Nodes may render incorrectly.`,
    );
  }
}
