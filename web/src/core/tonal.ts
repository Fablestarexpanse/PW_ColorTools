/**
 * Grain's tonal response, in the host-free core.
 *
 * The curve drawn on the node panel and the weights torch applies must be the
 * same function. It lives here, next to the lattice ops, for the same reason
 * they do: `web/src/core/` imports nothing from ComfyUI, so the parity harness
 * can load it under plain node and compare it against Python.
 *
 * It used to live in `nodes/grain.ts`, which imports the host `app` module —
 * unloadable outside the browser. So `tools/tonal.ts` kept its own copy of the
 * formula, and the test that exists to catch drift was comparing Python
 * against a second transcription rather than against the shipped code.
 */

/** How far grain fades at the extremes. Mirrors EDGE_FALLOFF in pw_color/grain.py. */
export const EDGE_FALLOFF = 0.04;

export function smoothstep(e0: number, e1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - e0) / (e1 - e0)));
  return t * t * (3 - 2 * t);
}

/**
 * The tonal weight at a given perceptual position. Line-for-line the same as
 * `TonalResponse.weight` in Python, so the curve drawn on the node is the curve
 * the renderer applies — not an impression of it.
 */
export function tonalWeight(t: number, shadows: number, midtones: number, highlights: number): number {
  const shadow = 1 - smoothstep(0, 0.5, t);
  const highlight = smoothstep(0.5, 1, t);
  const mid = Math.max(0, 1 - shadow - highlight);
  const w = shadow * shadows + mid * midtones + highlight * highlights;
  return w * smoothstep(0, EDGE_FALLOFF, t) * smoothstep(0, EDGE_FALLOFF, 1 - t);
}
