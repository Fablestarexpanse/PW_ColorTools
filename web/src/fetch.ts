/**
 * Fetching the pack's own routes, without dragging in the host surface.
 *
 * `api.fetchApi` is what applies ComfyUI's base path and whatever headers the
 * deployment needs — a bare `fetch('/pw_color/...')` skips both, which is why
 * the pack used to 404 behind a reverse proxy serving ComfyUI under a subpath.
 *
 * Separate from `comfy.ts` because that module imports `/scripts/app.js` at the
 * top level, which exists only in the browser. Anything importing it cannot be
 * loaded by node — and `canvas/preview.ts` needs to be, so its interaction
 * guards can be tested. The host module is imported here only when a request is
 * actually made, which never happens in a test.
 */

/** Fetch one of the pack's routes through the host's own fetch. */
export async function fetchPw(path: string): Promise<Response> {
  // @ts-ignore - provided by ComfyUI at runtime, no types published
  const { api } = await import('/scripts/api.js');
  return api.fetchApi(path, { cache: 'no-store' });
}
