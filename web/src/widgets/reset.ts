/**
 * "Put this node back to doing nothing."
 *
 * Every node in the pack has enough controls that finding your way back to
 * neutral by hand is real work, and "is this node still affecting my image?"
 * is a question you should never have to reverse-engineer from a dozen
 * sliders. So: one menu entry, and the node is a pass-through again.
 *
 * Defaults come from the node definition ComfyUI already holds, not from a
 * hardcoded table here — a table would drift from the schema the first time a
 * default changed, and drift silently.
 */

import type { NodeLike } from '../comfy.ts';

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
 * Restore every widget to its schema default.
 *
 * `seed` is kept by default: resetting it would change the grain pattern, and
 * someone reaching for "reset" wants the controls neutral, not a different
 * random field.
 */
export function resetNode(node: NodeLike, opts: ResetOptions = {}): void {
  const keep = new Set(opts.keep ?? ['seed', 'control_after_generate']);
  for (const w of node.widgets ?? []) {
    if (keep.has(w.name)) continue;
    const value = defaultFor(node, w.name);
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
 * Add "Reset to defaults" to a node's right-click menu.
 *
 * Chains the existing `getExtraMenuOptions` rather than replacing it, so the
 * entries ComfyUI and other packs add stay put.
 */
export function addResetMenu(nodeType: any, opts: (node: NodeLike) => ResetOptions = () => ({})): void {
  const prior = nodeType.prototype.getExtraMenuOptions;
  nodeType.prototype.getExtraMenuOptions = function (this: NodeLike, canvas: unknown, options: any[]) {
    const result = prior?.apply(this, arguments as any);
    options.push(null, {
      content: 'Reset to defaults',
      callback: () => resetNode(this, opts(this)),
    });
    return result;
  };
}
