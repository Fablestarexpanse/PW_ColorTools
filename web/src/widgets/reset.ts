/**
 * "Put this node back to doing nothing."
 *
 * Every node in the pack has enough controls that finding your way back to
 * neutral by hand is real work, and "is this node still affecting my image?"
 * is a question you should never have to reverse-engineer from a dozen
 * sliders. So: one action, and the image passes through untouched.
 *
 * Values come from the node definition ComfyUI already holds, not from a
 * hardcoded table here — a table would drift from the schema the first time a
 * default changed, and drift silently.
 *
 * The exception is the handful of controls whose *default* is deliberately not
 * neutral, listed in `PASS_THROUGH` below.
 */

import type { NodeLike } from '../comfy.ts';

/**
 * Controls whose schema default does something, and what "untouched" is instead.
 *
 * Some defaults are opinionated on purpose: halation is what makes PW Optics
 * worth reaching for, the dither floor exists to be always on, and a Match
 * Source at full strength is the point of the node. Those are good defaults and
 * bad reset targets — reset has to mean the image comes out as it went in, or
 * it cannot answer "is this node still affecting my image?".
 *
 * Keyed by node type; anything not listed resets to its schema default, which
 * for every other control is already neutral.
 */
const PASS_THROUGH: Record<string, Record<string, number>> = {
  // Vignette and aberration already default to zero; only halation is on.
  PW_Optics: { halation: 0 },
  PW_Grain: { amount: 0, dither: 0 },
  PW_MatchSource: { strength: 0 },
};

/** The default for a widget, straight from the node definition. */
function defaultFor(node: NodeLike, name: string): unknown {
  const defs = (node as any).constructor?.nodeData?.input ?? {};
  for (const section of ['required', 'optional']) {
    const entry = defs[section]?.[name];
    if (!entry) continue;
    const spec = entry[1];
    if (spec && typeof spec === 'object' && 'default' in spec) return spec.default;
    // Combos are declared as a bare list of options; the first is the default.
    if (Array.isArray(entry[0]) && entry[0].length) return entry[0][0];
  }
  return undefined;
}

export interface ResetOptions {
  /** Widgets to leave alone — seeds, filenames, anything not a grade control. */
  keep?: string[];
  /** Called after the widgets are restored, to reset any custom canvas state. */
  after?: () => void;
}

/**
 * Return the node to a pass-through state: the image comes out as it went in.
 *
 * `seed` is kept: resetting it would change the grain pattern, and someone
 * reaching for reset wants the controls neutral, not a different random field.
 */
export function resetNode(node: NodeLike, opts: ResetOptions = {}): void {
  const keep = new Set(opts.keep ?? ['seed', 'control_after_generate']);
  const neutral = PASS_THROUGH[String(node.type)] ?? {};
  for (const w of node.widgets ?? []) {
    if (keep.has(w.name)) continue;
    const value = w.name in neutral ? neutral[w.name] : defaultFor(node, w.name);
    if (value === undefined) continue;
    if (w.value !== value) {
      w.value = value;
      w.callback?.(w.value);
    }
  }
  opts.after?.();
  node.setDirtyCanvas?.(true, true);
}

/**
 * Add the reset action to a node's right-click menu.
 *
 * Chains the existing `getExtraMenuOptions` rather than replacing it, so the
 * entries ComfyUI and other packs add stay put.
 */
export function addResetMenu(nodeType: any, opts: (node: NodeLike) => ResetOptions = () => ({})): void {
  const prior = nodeType.prototype.getExtraMenuOptions;
  nodeType.prototype.getExtraMenuOptions = function (this: NodeLike, canvas: unknown, options: any[]) {
    const result = prior?.apply(this, arguments as any);
    options.push(null, {
      content: 'Reset — pass image through unchanged',
      callback: () => resetNode(this, opts(this)),
    });
    return result;
  };
}
