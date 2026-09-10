# Changelog

## 2.4.0

### Added

- **Re-run a frame from PW Review.** An R after each frame's stars queues the
  prompt that made it again, at the front of the queue, with every seed drawn
  afresh and everything else unchanged. The new image lands in the tray right
  after the frame it re-ran, marked R. The node now keeps each run's prompt and
  workflow alongside its images for this.
- **rate all 5** and **send all** in PW Review's new toolbar. send all sends
  the whole tray, rated frames first by rating, then the unrated in the order
  they arrived.
- **A resizable preview in PW Review.** A grip under the focus view drags it
  taller or shorter, and the height is saved with the workflow. Making the
  node itself taller now gives the room to the view, where before it only
  added empty space under the thumbnails.

## 2.3.0

### Changed

- **PW Review collects instead of stopping.** It used to halt the run and wait
  at the node, one run at a time. With Run set to four, that meant rating one
  image at a time while the other runs sat queued behind it, and several runs
  appearing to pile up without generating. Now each run drops its images into
  the node's tray and finishes, so the queue keeps generating while the tray
  fills. Rate them when you are ready, then **release**: the keepers go out as
  one batch, best first, on a single run that PW Review queues at the front
  and aims only at the nodes after it, so the sampler does not run again. A
  new **clear** button empties the tray.

### Fixed

- **auto_pass did nothing to runs already in the queue.** ComfyUI copies every
  widget into a run when it is queued, so turning auto_pass on still left the
  queued runs holding for a rating, and turning it off still let them through.
  The switch now reaches the server the moment it changes and wins over the
  value the run was queued with.

## 2.2.0

### Added

- **PW Review.** A place to stop and look, put wherever you want to judge what
  came out. The run halts at the node with the whole batch in front of you:
  click a frame to see it large, rate each one to five stars, then release.
  The rated images carry on best first and the unrated are dropped, which is
  how you reject one. Rating nothing stops the run there quietly.
  `auto_pass` sends everything through untouched for unattended runs, and is
  what the node's reset turns on. The batch stays in memory while it waits and
  the run keeps its place in the queue. It is not in the drop-in workflow, on
  purpose: a template that stops on first run is the wrong first impression.

## 2.1.2

### Fixed

- **In Modern Node Design, the previews stopped following the sliders.** Move
  a control after the graph had run and nothing on the panel changed. The two
  renderers announce a widget edit differently: the Classic one calls the
  node's `onWidgetChanged`, which is what the pack listened to, while the
  Modern one calls only the widget's own callback. Panels now listen to both,
  coalesced to one rebake per frame, so a drag stays cheap.

  PW Optics is unaffected and unchanged: its preview is the node's real
  output, badged `render only`, and it updates when the graph next runs.

## 2.1.1

### Fixed

- **Nodes grew every time the graph ran, in Modern Node Design.** The renderer
  changes a node's layout while it executes, the panel shrank for a frame, the
  pack grew the node to compensate and never gave it back. Height the pack
  adds is now remembered and returned once the panel is taller than it needs;
  a user's own resize is left alone.
- README screenshots retaken in Modern Node Design; `tools/capture_readme.py`
  reproduces them.

## 2.1.0

### Added

- **Modern Node Design (Nodes 2.0) support.** Every panel — curve editor,
  previews, preset strip, colour mixer, grain response, palette — is now
  hosted on a DOM widget and draws in both node designs. Saved workflows are
  unchanged: the widgets that carry state keep their place and their values.

### Fixed

- In Modern Node Design the 2.0.1 notice widget appended an empty entry to
  each PW node's saved widget values. The notice is gone with the limitation.

## 2.0.1

### Fixed

- **On Linux, a backslash in a save name reached the filename.** The name
  sanitiser took the last path segment using the host's own separator, so on
  Linux `..\..\x` was one segment and its `..` survived into the file. Never
  a traversal (the result stayed inside the looks directory) but not the name
  the docstring promised. Both separators are now cut on every platform. Found
  by the first CI run on ubuntu; Windows could not reproduce it.
- CI could not have passed: the dependency step pointed pip at the PyTorch
  index alone, which has no `pytest`, and installed a fraction of what
  importing ComfyUI needs. Failed tests now surface as run annotations.

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
