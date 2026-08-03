"""Film grain: procedural and plate-based.

Render-only. Grain reads pixel neighbourhoods (it has a spatial correlation
length — that is what "grain size" *means*) so it cannot be baked into a
lattice, and the node badges itself accordingly.

Four things here are what separate this from adding noise to an image:

* **Tonal response.** Real grain lives in the emulsion's density variation. It
  is strongest in the midtones, weak in the shadows and nearly absent in blown
  highlights. Uniform noise across the frame is the single biggest tell of fake
  grain, so the tonal weighting is not optional.
* **Absolute size.** 1.4px grain is 1.4px at 1024 and at 4096. The grain field
  is generated at output resolution and never scaled with the image, which is
  the trap: a fixed-size noise texture stretched to fit gets softer as the
  image gets bigger, so a look stops matching itself across resolutions.
* **Unit variance after filtering.** Blurring noise to make it coarser also
  makes it quieter. We renormalise, so ``amount`` means the same thing at any
  grain size — otherwise every size change needs an amount change to compensate.
* **Mean-centred plates.** A scanned grain plate carries its own exposure. Used
  raw it shifts the image. We subtract the plate's mean to get a signed
  deviation field, so a plate contributes texture and nothing else.

Determinism: every random draw goes through an explicit seeded CPU generator.
Same seed, same pixels, on any device.
"""

from __future__ import annotations

import math

import torch

from .colour import luma_bt709, srgb_to_linear

__all__ = [
    "GRAIN_BLEND_MODES",
    "TonalResponse",
    "procedural_field",
    "plate_field",
    "apply_grain",
    "dither",
]

#: Blend modes offered for grain. The Photoshop set our audience already knows.
GRAIN_BLEND_MODES = ("overlay", "soft light", "add", "screen")

#: Below this fraction of the range, grain fades out entirely.
#:
#: Pure black and pure white must stay clean. Two reasons: real film has no
#: density variation at Dmin or Dmax, and adding signed noise to 0.0 then
#: clamping keeps only the positive half, which lifts the black instead of
#: texturing it. A short smooth ramp fixes both without gutting the shadows
#: control, which still governs everything above it.
EDGE_FALLOFF = 0.04


def _smoothstep(edge0: float, edge1: float, x: torch.Tensor) -> torch.Tensor:
    t = ((x - edge0) / (edge1 - edge0)).clamp(0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class TonalResponse:
    """Shadow / midtone / highlight weighting, as a partition of unity.

    The three windows sum to 1 at every luminance, so setting all three to the
    same value gives genuinely uniform grain and the sliders read as a balance
    rather than as three independent gains that interact confusingly.
    """

    __slots__ = ("shadows", "mids", "highlights")

    def __init__(self, shadows: float = 0.20, mids: float = 1.00, highlights: float = 0.10) -> None:
        self.shadows = float(shadows)
        self.mids = float(mids)
        self.highlights = float(highlights)

    def weight(self, image: torch.Tensor) -> torch.Tensor:
        """Per-pixel grain weight ``[B,H,W,1]`` from an sRGB-encoded image.

        Luminance is measured in linear light, not on the sRGB encoding: grain
        sits in the emulsion and responds to light, not to the display curve.
        """
        lum = luma_bt709(srgb_to_linear(image[..., :3].clamp(0.0, 1.0)))
        # Perceptual position, so "midtones" means what a user points at.
        t = lum.clamp(0.0, 1.0).pow(1.0 / 2.2)

        shadow = 1.0 - _smoothstep(0.0, 0.5, t)
        highlight = _smoothstep(0.5, 1.0, t)
        mid = (1.0 - shadow - highlight).clamp(min=0.0)

        w = shadow * self.shadows + mid * self.mids + highlight * self.highlights
        falloff = _smoothstep(0.0, EDGE_FALLOFF, t) * _smoothstep(0.0, EDGE_FALLOFF, 1.0 - t)
        return (w * falloff).unsqueeze(-1)


def _gaussian_kernel(sigma: float, device, dtype) -> torch.Tensor:
    radius = max(1, int(math.ceil(sigma * 3.0)))
    x = torch.arange(-radius, radius + 1, device=device, dtype=dtype)
    k = torch.exp(-(x * x) / (2.0 * sigma * sigma))
    return k / k.sum()


def _blur_separable(x: torch.Tensor, sigma: float) -> torch.Tensor:
    """Separable Gaussian on ``[B,C,H,W]``. Reflect padding so grain does not
    fade at the frame edge, which reads as a vignette."""
    if sigma <= 0.05:
        return x
    k = _gaussian_kernel(sigma, x.device, x.dtype)
    r = (k.numel() - 1) // 2
    c = x.shape[1]
    kh = k.view(1, 1, 1, -1).expand(c, 1, 1, -1)
    kv = k.view(1, 1, -1, 1).expand(c, 1, -1, 1)
    x = torch.nn.functional.pad(x, (r, r, 0, 0), mode="reflect")
    x = torch.nn.functional.conv2d(x, kh, groups=c)
    x = torch.nn.functional.pad(x, (0, 0, r, r), mode="reflect")
    return torch.nn.functional.conv2d(x, kv, groups=c)


def _normalise(field: torch.Tensor) -> torch.Tensor:
    """Rescale to unit standard deviation, per batch item.

    Without this, coarser grain is quieter grain, and ``amount`` stops being a
    stable unit across the size slider.
    """
    std = field.flatten(1).std(dim=1, unbiased=False).clamp(min=1e-8)
    return field / std.view(-1, *([1] * (field.ndim - 1)))


def procedural_field(
    batch: int,
    height: int,
    width: int,
    size: float,
    seed: int,
    vary_per_frame: bool = False,
    device: torch.device | str = "cpu",
) -> torch.Tensor:
    """A signed, unit-variance grain field ``[B,H,W,3]``.

    ``size`` is the full width at half maximum of a grain cluster, in **output
    pixels**, independent of image resolution.

    Generated on the CPU regardless of the target device: CUDA's RNG does not
    produce the same stream as the CPU's, and a grain look that changes when you
    move machines is not a look.
    """
    sigma = max(0.0, float(size)) / 2.355
    out = []
    for b in range(batch):
        g = torch.Generator(device="cpu").manual_seed(int(seed) + (b if vary_per_frame else 0))
        # Three independent channels. Real stock has partially decorrelated
        # layers; per-channel amounts are how the user dials that in.
        noise = torch.randn(1, 3, height, width, generator=g, dtype=torch.float32)
        out.append(_normalise(_blur_separable(noise, sigma)))
    return torch.cat(out, dim=0).permute(0, 2, 3, 1).contiguous().to(device)


def plate_field(
    plate: torch.Tensor,
    batch: int,
    height: int,
    width: int,
    seed: int,
    vary_per_frame: bool = False,
) -> torch.Tensor:
    """Turn a grain plate into a signed, unit-variance field ``[B,H,W,3]``.

    The plate is **cropped**, never resized: resizing would scale the grain with
    the image and break the absolute-size guarantee. If the plate is smaller
    than the frame it tiles, mirrored, so the seam is not a hard line.

    A plate batch maps to output batch index, so an image-sequence loader wired
    into the plate input gives per-frame plates without this module needing to
    know anything about video.
    """
    if plate.ndim != 4 or plate.shape[-1] < 3:
        raise ValueError(f"grain plate must be [B,H,W,C>=3], got {tuple(plate.shape)}")

    ph, pw = plate.shape[1], plate.shape[2]
    fields = []
    for b in range(batch):
        src = plate[min(b, plate.shape[0] - 1) if plate.shape[0] > 1 else 0, ..., :3]

        # Mean-centre first: the plate's own exposure must not reach the image.
        dev = src - src.mean()

        # Mirror-tile up to at least the frame size, then random-crop.
        reps_y = max(1, -(-height // ph))
        reps_x = max(1, -(-width // pw))
        if reps_y > 1 or reps_x > 1:
            dev = _mirror_tile(dev, reps_y, reps_x)
        th, tw = dev.shape[0], dev.shape[1]

        g = torch.Generator(device="cpu").manual_seed(int(seed) + (b if vary_per_frame else 0))
        oy = int(torch.randint(0, max(1, th - height + 1), (1,), generator=g).item())
        ox = int(torch.randint(0, max(1, tw - width + 1), (1,), generator=g).item())
        crop = dev[oy : oy + height, ox : ox + width]
        fields.append(crop.unsqueeze(0))

    field = torch.cat(fields, dim=0)
    return _normalise(field).to(plate.device)


def _mirror_tile(x: torch.Tensor, reps_y: int, reps_x: int) -> torch.Tensor:
    """Tile ``[H,W,C]`` with alternating flips, so tile seams are continuous."""
    rows = []
    for j in range(reps_y):
        cols = []
        for i in range(reps_x):
            t = x
            if i % 2:
                t = torch.flip(t, dims=[1])
            if j % 2:
                t = torch.flip(t, dims=[0])
            cols.append(t)
        rows.append(torch.cat(cols, dim=1))
    return torch.cat(rows, dim=0)


def _blend(base: torch.Tensor, signed: torch.Tensor, mode: str) -> torch.Tensor:
    """Composite a signed grain deviation over the base.

    Each mode maps the signed field to its own neutral. This matters: the
    neutral value of overlay and soft light is 0.5 grey, but the neutral of
    screen is *black*. Feeding screen a 0.5-centred layer lifts a mid grey to
    0.75 — an enormous exposure shift dressed up as a blend mode. So screen
    takes only the positive lobe and lightens, which is what screen means and
    what a grain plate composited in screen actually does.
    """
    if mode == "add":
        return base + signed

    if mode == "screen":
        # Black-based layer: screen(base, 0) == base.
        layer = signed.clamp(min=0.0, max=1.0)
        return 1.0 - (1.0 - base) * (1.0 - layer)

    # Grey-based layer for the contrast modes.
    layer = (0.5 + signed * 0.5).clamp(0.0, 1.0)
    if mode == "overlay":
        return torch.where(base <= 0.5, 2.0 * base * layer, 1.0 - 2.0 * (1.0 - base) * (1.0 - layer))
    if mode == "soft light":
        # W3C / Photoshop soft light. The piecewise d() is what keeps the
        # midtone slope continuous; the naive 2*b*l formula kinks at 0.5.
        d = torch.where(base <= 0.25, ((16.0 * base - 12.0) * base + 4.0) * base, base.clamp(min=0.0).sqrt())
        return torch.where(
            layer <= 0.5,
            base - (1.0 - 2.0 * layer) * base * (1.0 - base),
            base + (2.0 * layer - 1.0) * (d - base),
        )
    raise ValueError(f"unknown grain blend mode {mode!r}")


def apply_grain(
    image: torch.Tensor,
    field: torch.Tensor,
    tonal: TonalResponse,
    amount: float = 0.05,
    channel_amounts: tuple[float, float, float] = (1.0, 1.0, 1.15),
    blend: str = "overlay",
    opacity: float = 1.0,
) -> torch.Tensor:
    """Composite a signed grain field onto an image.

    ``channel_amounts`` defaults the blue channel hotter, which matches real
    stock: the blue-sensitive layer sits on top and has the largest crystals.
    """
    rgb = image[..., :3]
    weight = tonal.weight(image)
    ch = torch.tensor(channel_amounts, dtype=rgb.dtype, device=rgb.device)

    field_rgb = field[..., :3].to(rgb.device)
    if field_rgb.shape[1:3] != rgb.shape[1:3]:
        raise ValueError(
            f"grain field is {tuple(field_rgb.shape[1:3])} but the image is {tuple(rgb.shape[1:3])} — "
            "the field must be generated at output resolution, or grain size stops being absolute"
        )

    scaled = field_rgb * float(amount) * weight * ch
    out = _blend(rgb, scaled, blend)
    if opacity < 1.0:
        out = torch.lerp(rgb, out, float(opacity))
    out = out.clamp(0.0, 1.0)

    if image.shape[-1] == 4:
        return torch.cat((out, image[..., 3:]), dim=-1)
    return out


def dither(image: torch.Tensor, seed: int, levels: int = 255, strength: float = 1.0) -> torch.Tensor:
    """Add a triangular-PDF dither floor before 8-bit quantisation.

    Always on, even at zero grain. A smooth sky or a soft gradient quantised to
    8 bits bands visibly; one LSB of TPDF dither decorrelates the quantisation
    error and the banding becomes texture the eye reads as nothing at all. This
    costs a fraction of a code value and is the cheapest quality win in the pack.

    TPDF rather than uniform: the sum of two uniforms is the noise distribution
    that makes the quantisation error independent of the signal, which is the
    entire point. Uniform dither leaves a residual correlated with the signal.
    """
    if strength <= 0.0:
        return image
    rgb = image[..., :3]
    g = torch.Generator(device="cpu").manual_seed(int(seed) ^ 0x51712)
    shape = rgb.shape
    a = torch.rand(shape, generator=g, dtype=torch.float32)
    b = torch.rand(shape, generator=g, dtype=torch.float32)
    tpdf = (a - b).to(rgb.device) * (float(strength) / float(levels))
    out = (rgb + tpdf).clamp(0.0, 1.0)
    if image.shape[-1] == 4:
        return torch.cat((out, image[..., 3:]), dim=-1)
    return out
