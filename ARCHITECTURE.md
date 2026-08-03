# Architecture

Findings and decisions that are not obvious from the code. Read this before
changing anything in `pw_color/lattice.py` or `web/src/core/`.

## The parity contract

Per-pixel colour operations are evaluated into a 3D LUT lattice. Both the WebGL
preview and the torch renderer sample that same lattice, so they cannot drift.
Spatial operations (grain, halation, vignette, chromatic aberration, mask blur)
read pixel neighbourhoods, cannot be expressed as a lattice, and live in
separate render-only nodes with a preview clearly badged as approximate.

**What travels in the workflow is the LOOK parameters, not the lattice.** Both
sides bake locally from the same small JSON. That keeps workflow files (and the
metadata embedded in every saved PNG) tiny and human-readable, and it keeps the
preview free of a server round trip while the user drags a control. The obvious
objection — two implementations of the same maths will drift — is answered by
the byte-identical build test below, which fails the moment they do. A 33³ u16
lattice would otherwise be ~287 KB of base64 per node.

Four properties make the contract hold, and all four are enforced by
`tests/test_parity.py`:

1. **Both sides bake in float64.** JavaScript has no float32 arithmetic; torch
   defaults to it. Baking in float32 on the Python side put the two sides ~1e-7
   apart, which sounds harmless and is not: wherever an op runs off the edge of
   the sRGB gamut the local gain is around 100x, and that was enough to flip u16
   codes and put a visible code value between preview and render.

2. **The lattice is quantised on construction.** `Lattice.from_fn` returns an
   already-quantised lattice. There is no way to hold an unquantised one by
   accident, so the preview cannot be sampling something the renderer isn't.
   `encoding=None` opts out, for `.cube` authoring only.

3. **The trilinear sampler is hand-written on both sides**, not `grid_sample`
   and not hardware texture filtering. `align_corners` semantics and GPU
   filtering precision are not things we can pin across backends.

4. **The clamp to [0,1] happens after sampling, not in the lattice.** The
   lattice stores the unclamped op stack over a fixed −0.5…2.0 range. See the
   bake cost section — this is worth 1.8 code values on a bright exposure. A
   clamp is trivially identical in torch and in a fragment shader, so it costs
   nothing in parity. The range is a constant rather than fitted to the data,
   because a fitted range would make the quantisation grid depend on floats
   that agree across the two languages only to ~1e-6.

### What is guaranteed

| | Guarantee |
|---|---|
| Lattice build (TS vs torch, pre-quantisation) | agree to < 1e-6 |
| Lattice bytes after u16 transport | **byte-identical** |
| Trilinear sampler (same lattice in) | agree to ~6e-8 |
| Preview vs render, 8-bit output | **identical** |
| Preview vs render, 16-bit output | within 1 code |

The 16-bit code is the sampler running float64 in the browser and float32 in
torch. A float64 render path would double every intermediate on a 4K image for
no visible benefit, so we take the code and state it rather than claiming
something we do not deliver.

## Bake cost: what a lattice costs, and when it stops being free

Baking is only free if the operation is smooth. Max error against direct
per-pixel evaluation, in 8-bit code values, with the extended-range storage in
place:

| Operation | 33³ | 65³ |
|---|---|---|
| Per-channel RGB curves | 0.021 | 0.007 |
| Exposure +0.4 (clips highlights) | 0.121 | 0.089 |
| Per-channel luma curve | 0.167 | 0.044 |
| Darkening exposure (−0.3 stops) | 0.173 | 0.050 |
| Saturation pull (0.85) | 0.267 | 0.077 |
| Contrast +0.3 (clips highlights) | 0.333 | 0.090 |
| Preserve-hue luma curve | 0.744 | 0.250 |
| **Warmth −0.4 (leaves gamut)** | **4.023** | **1.415** |
| **Saturation push 1.18 (leaves gamut)** | **6.815** | **2.396** |

**Everything expensive is a clip**, because a clip is a kink and no lattice
represents a kink. Moving the clamp out of the lattice removed the highlight
kink entirely — exposure went 1.94 → 0.12, contrast 1.39 → 0.33, and the
preserve-hue curve 3.85 → 0.74. What remains is **gamut clipping**: chroma
pushed past the sRGB boundary. That one is genuine, and no lattice size fixes
it — 33³ → 65³ roughly thirds the error rather than quartering it.

Attempted and rejected: **OKLab chroma compression** toward the gamut boundary
instead of hard per-channel clipping. It made the bake *worse* (27 → 37 codes)
and desaturated legal primaries by up to 49 codes. The reason is that the sRGB
gamut boundary in OKLab has cusps at the six primaries, so a gamut-aware op
inherits those cusps — it trades one kink for six.

This cost is a **quality budget, not a parity one**: preview and render sample
the same lattice, so both show the same deviation and the user never sees a
discrepancy between them. What they could see is banding on a heavily pushed
saturated highlight.

One knock-on: an exported `.cube` is limited to `[0,1]` by the format, so it
carries the clamped lattice and is slightly worse than our internal render on
looks that clip. That is inherent to the format, not to us.

## Maths that lives in two languages

Every duplicated formula gets a harness and a test, because the whole
architecture rests on the two implementations not drifting. Currently:

| Maths | Python | TypeScript | Test |
|---|---|---|---|
| Colour, curves, ops, lattice | `pw_color/` | `web/src/core/` | `test_parity.py` |
| Grain tonal response | `pw_color/grain.py` | `web/src/nodes/grain.ts` | `test_grain.py` |
| Design system palette | `pw_color/theme.py` | `web/src/theme.ts` | `test_theme.py` |

`theme.ts` is the source of truth for colour; the Python mirror exists only
because the palette swatch strip is rendered server-side and has to match the
node chrome. `test_theme.py` parses the TS and asserts they agree.

Palette clustering deliberately has **no** TypeScript twin. The node's swatch
strip is drawn from the execution result, because unlike the lattice there is
no interactive reason to want k-means client-side, and a third duplicated
implementation would be a third thing to keep in sync.

Both harnesses (`web/tools/parity.ts`, `web/tools/tonal.ts`) run under bare
`node --experimental-strip-types` with no build step, deliberately: a parity
test that needs a bundler is a parity test people stop running.

If you add a third, add its row here and its test at the same time.

## Grain

Render-only — grain has a spatial correlation length, which is exactly what a
colour lattice cannot express. Four things are load-bearing:

* **Tonal response** weights grain by linear luminance through three windows
  that form a partition of unity, so equal sliders give genuinely uniform grain.
  A short smooth ramp (`EDGE_FALLOFF`) takes it to zero at pure black and pure
  white — real film has no density variation at Dmin or Dmax, and adding signed
  noise to 0.0 then clamping keeps only the positive half, which *lifts* the
  black instead of texturing it.
* **Absolute size.** The field is generated at output resolution and never
  scaled. The trap is a fixed-size noise texture stretched to fit, which gets
  softer as the image grows, so a look stops matching itself across resolutions.
  `test_grain_size_is_absolute_not_relative` measures spatial autocorrelation at
  two resolutions to catch that.
* **Unit variance after filtering.** Blurring noise to coarsen it also quietens
  it. We renormalise, so `amount` means the same thing at every size.
* **Mean-centred plates.** A scanned plate carries its own exposure; used raw it
  shifts the image.

One thing the blend modes get wrong elsewhere: **screen's neutral is black, not
grey.** Feeding screen a 0.5-centred grain layer lifts mid grey to 0.75 — a
large exposure shift dressed up as a blend mode. Our screen takes only the
positive lobe and lightens, which is what screen means and what a grain plate
composited in screen actually does. Every mode is tested for neutrality at zero
grain across five input values.

## Palette

K-means in OKLab, not RGB. K-means minimises Euclidean distance, and Euclidean
distance in sRGB does not mean "looks different" — cluster in RGB and a night
scene returns five near-identical browns while the one saturated accent gets
absorbed.

Determinism is a stated guarantee (same image, byte-identical palette), so
everything that could introduce variance is pinned: clustering runs on the CPU
regardless of where the image is (CUDA reductions are not order-deterministic),
k-means++ seeding uses an explicit seeded generator, Lloyd's runs to a fixed cap
with an explicit epsilon, empty clusters reseed from the farthest point rather
than at random, and sorts are stable with a hex tie-break.

Two behaviours worth knowing:

* `ignore_near_black` / `ignore_near_white` default **on**. Clusters go where
  the pixels are, so without them a night scene returns five blacks. If
  filtering removes everything, extraction falls back to the unfiltered pixels
  rather than failing — an all-black image has a palette and it is one black.
* `weight_by_chroma` changes clustering pull and ordering but **not** the
  reported `coverage`, which stays a true pixel fraction. A "coverage" that
  silently meant something else depending on a toggle would be worse than
  useless.

k-means++ already rescues a small saturated accent without chroma weighting,
because seeding by squared distance naturally favours outliers. That is pinned
by a test, so a switch to plain random seeding would be caught rather than
quietly degrading every palette.

### Palette export

Palettes save to **ComfyUI's `output/palettes`**, not into the pack folder:
they are the user's work and must survive updating or reinstalling this node
pack.

Two paths, deliberately: `save_as` writes on every run (repeatable workflow),
and the node's export button downloads straight from the browser with no
execution (you liked what you just saw). The browser exporter mirrors the
Python `.ase` and `.gpl` writers rather than calling them, because requiring a
run to get a file defeats the point — that is a third small duplication, and
`test_palette_io.py` covers both directions of every format so the Python side
at least cannot drift silently.

Only `.json` round-trips losslessly. `.ase`, `.gpl` and `.txt` carry colours but
not coverage, so loading one splits coverage evenly and records
`meta["coverage"] = "even (not carried by this format)"`. Inventing
plausible-looking coverage would be worse than admitting it is unknown: a
downstream node cannot tell a guess from a measurement.

`save_as` is a free-text widget, so `safe_name()` **strips** path separators and
`..` rather than escaping them. The only correct handling of a traversal attempt
from a text field is for the result not to be a path at all; four traversal
shapes are covered by tests.

## LUT export honesty

A `.cube` can only carry per-pixel operations. Grain, glow, halation, vignette,
chromatic aberration and reference matching cannot be in one.

Rather than have the exporter guess after the fact, **every op records
`lut_safe` when it is created**, at the node that knows. `PW Look I/O` then
reports exactly what a `.cube` included and what it dropped, instead of writing
a file that quietly does less than the user's graph.

`Look.lut_exportable` ignores disabled ops, so turning grain off genuinely makes
a look exportable again. There is a test for that, because the alternative — a
warning that never goes away — trains people to ignore warnings.

## Scopes

Rendered server-side into an IMAGE, not drawn in the browser. Two reasons: a
scope of the downscaled preview proxy is not a scope of the image (resampling
fills in exactly the gaps that make posterisation and clipping visible), and a
scope that is an IMAGE can be wired into a Save Image node and kept alongside
the frame it measured.

The waveform maps **destination columns to source columns** when the scope is
wider than the image. The obvious direction — source to destination — lights
only every nth column and leaves the trace combed with vertical gaps. Caught by
a test that asserts every output column carries some trace.

Density is normalised per column and raised to ~0.45; histograms use ~0.4.
Linear makes a normal photograph one spike and a flat line, log makes three
stray pixels look like a tonal region.

## ComfyUI specifics

Verified against **ComfyUI 0.29.2 / comfyui-frontend-package 1.47.11**.

* **V3 schema only.** `nodes.py` checks for `NODE_CLASS_MAPPINGS` *before*
  `comfy_entrypoint`, so defining both silently pins the pack to V1. We define
  only the entrypoint.
* **Custom types** come from `io.Custom("LOOK")` / `io.Custom("PALETTE")`.
* **Chain node handlers, never replace them** — see `chainHandler` in
  `web/src/comfy.ts`. Subgraph header buttons are implemented through the
  node's own `onMouseDown`; replacing it breaks entering and leaving subgraphs.
* The root `__init__.py` guards its `comfy_api` import so the maths modules
  stay testable without ComfyUI installed.
