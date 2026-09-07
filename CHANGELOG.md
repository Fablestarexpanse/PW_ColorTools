# Changelog

## 2.0.0

A code-health release. No node was added or removed and no saved workflow
changes meaning; every socket, widget and default is where it was. What changed
is everything underneath, and the things that were wrong.

### Fixed

- **An interrupted save destroyed the file it was replacing.** All three
  writers (`.look`, palette, `.cube`) wrote straight to the destination. A full
  disk or a crash mid-write turned the look you had into an empty file. Writes
  now go beside the target and rename.
- **A preview you could see but not touch.** A node showing only its rendered
  output would not pan, zoom or double-click, with nothing to say why. The two
  caches evict independently, so this was reachable in ordinary use.
- **The preset strip latched a failure.** One failed fetch during page load left
  it blank for the life of the page. Only successful fetches are cached now.
- **PW Match Source truncated the LOOK chain.** It emitted a LOOK but could not
  receive one, so placing it mid-chain silently dropped everything upstream. It
  takes `look_in` now, appended as a new slot so existing wires are untouched.
- **A grain plate name reached the filesystem unchecked.** The `plate` combo
  value comes from workflow JSON. It is now validated against the shipped set.
- **Reset presets could not recover.** A malformed `presets.json` was cached as
  empty forever; fixing the file now recovers without a restart.
- Corrupt `.look`, `.ase`, `.gpl` and `.cube` files all fail as a `ValueError`
  naming the file, instead of escaping as `struct.error`, `IndexError` or
  `TypeError` from inside a parser.
- Preview routes went behind a reverse-proxy subpath: the browser now fetches
  through ComfyUI's own `api`, which applies the base path.
- Glow and halation, the gradient map's blend modes, and the tonal-response
  smoothstep were each implemented more than once; the copies had drifted.
  One implementation each, verified bit-identical against the originals.

### Changed

- **Modern Node Design (frontend 1.49+, opt-in) is not yet supported.** The
  pack's panels are canvas-drawn and that renderer does not paint them. The
  pack now warns on load and explains on each node; keep the setting off.
- `grain.dither` is `grain.apply_dither`. `preview_server.store_for_node` is
  `preview_cache.store_input_for_node`. Both Python-level only.
- Loading a look or palette by a name the node does not list is refused, with
  the available names in the message. Previously any basename was tried.
- CI runs the full suite — including ComfyUI-dependent node tests and the
  frontend tests — and fails if anything skipped that was not expected to.

### Added

- `example_workflows/pw_color_basic.json`: the whole pack wired, starting at
  pass-through. Under *Workflow → Browse Templates → Extensions*.
- A frontend test runner (`npm test`) with tests for the curve editor and the
  preview's interaction guards.
- Parity tests for the palette exporters, which existed in both languages with
  nothing checking they agreed.
- 809 tests, up from 658.

## 1.0.0

Initial release: PW Look, PW Curves, PW Grain, PW Optics, PW Match Source,
PW Palette, PW Scopes, PW Look I/O.
