# Grain plates

Three plates ship here. **They are generated, not scanned** — we are not going
to pass off synthetic noise as a drum scan, and you should know what you are
using.

| Plate | Character |
|---|---|
| `plate-fine.png` | Tight, low-contrast grain. Digital captures, clean stock. |
| `plate-medium.png` | The general-purpose one. |
| `plate-coarse.png` | Heavy clumping. Pushed stock, high ISO looks. |

What they add over the procedural mode is **clumping**. Real grain clusters;
the procedural path generates an even field by construction. These are built
from multi-octave noise with a contrast curve and slight vertical anisotropy,
which gets the clustered character that reads as emulsion.

They are 640×640 and mirror-tile to fill any frame. That is a deliberate size
compromise — noise does not compress, and a 1024px set was 7 MB in the
repository for something the procedural mode approximates well.

## Adding your own

Drop scanned plates in this folder as PNG, JPEG, TIFF or WebP. They appear in
the `plate` dropdown on PW Grain after a ComfyUI restart, the same contract as
adding a checkpoint.

What makes a good plate:

* **Flat and evenly lit.** The plate's own exposure is subtracted (we
  mean-centre it into a signed deviation field), but a *gradient* across the
  plate survives that and will show up as a soft vignette on your image.
* **At the resolution you work at.** Plates are cropped, never resized —
  resizing would scale the grain with the image and break the guarantee that
  1.4px grain is 1.4px at any resolution. A plate smaller than your frame
  mirror-tiles, which is fine for fine grain and obvious for coarse.
* **Unclipped.** Anything crushed to 0 or blown to 1 contributes no deviation,
  so clipped plates grain unevenly.

Stills only. There is deliberately no video decoding here — that would mean a
dependency on opencv or ffmpeg, which this pack does not take. For sequences,
wire an image-sequence loader into the `plate_image` input instead: a batch
there maps to output batch index, so you get per-frame plates without us owning
a decoder.
