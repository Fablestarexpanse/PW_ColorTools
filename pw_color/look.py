"""The PW Look grade: tone, colour, HSL mixer and gradient map.

Every function here has the lattice-safe signature — ``[M,3]`` sRGB-encoded in,
``[M,3]`` sRGB-encoded out, pure — so the whole grade bakes into one lattice and
the preview is exact. ``web/src/core/look_ops.ts`` mirrors this file and
``tests/test_parity.py`` pins them together.

Two conventions run through all of it:

* **Tone adjustments drive OKLab lightness with chroma held.** Applying a tone
  curve to R, G and B separately *is* a saturation and hue change, just an
  accidental one — it is why other tools' contrast sends skin orange. The one
  exception is exposure, which is a light operation and belongs in linear.
* **Plain language only.** exposure, contrast, highlights, shadows, whites,
  blacks, warmth, tint, vibrance, saturation. No lift/gamma/gain — our audience
  thinks in Lightroom, not in colour science.

Glow is deliberately *not* here: it reads pixel neighbourhoods, so it cannot be
a lattice op. It lives in :mod:`pw_color.glow` and the node badges it.
"""

from __future__ import annotations

import math
from typing import Any

import torch

from . import colour

__all__ = [
    "HSL_BANDS",
    "op_tone",
    "op_colour",
    "op_hsl",
    "op_gradient_map",
    "ramp_from_palette",
]

#: HSL mixer bands, as OKLab hue angles in radians.
#:
#: Computed once from the obvious reference primaries rather than hand-tuned, so
#: the band a user points at is the band they get. Hardcoded rather than derived
#: at import so the TypeScript mirror is guaranteed to hold identical values.
HSL_BANDS: tuple[tuple[str, float], ...] = (
    ("red", 0.510228),  # #FF0000
    ("orange", 0.924757),  # #FF8000
    ("yellow", 1.915835),  # #FFFF00
    ("green", 2.487012),  # #00FF00
    ("aqua", -2.883826),  # #00FFFF
    ("blue", -1.674608),  # #0000FF
    ("purple", -1.153006),  # #8000FF
    ("magenta", -0.552163),  # #FF00FF
)

#: How far a tone band reaches, in OKLab lightness.
_BAND_HALF = 0.36
#: Lightness centres for blacks / shadows / highlights / whites.
_TONE_CENTRES = (0.0, 0.33, 0.67, 1.0)


def _smoothstep(e0: float, e1: float, x: torch.Tensor) -> torch.Tensor:
    t = ((x - e0) / (e1 - e0)).clamp(0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _band(x: torch.Tensor, centre: float, half: float = _BAND_HALF) -> torch.Tensor:
    """A smooth bump peaking at ``centre``, reaching zero ``half`` away.

    C1 continuous on purpose: a piecewise-linear tent would put a kink in the
    transfer curve, and a kink is the one thing a lattice cannot represent.
    """
    rising = _smoothstep(centre - half, centre, x)
    falling = 1.0 - _smoothstep(centre, centre + half, x)
    return rising * falling


def op_tone(rgb: torch.Tensor, p: dict[str, Any]) -> torch.Tensor:
    """Exposure, contrast, and the four tonal bands.

    Exposure and contrast run in linear light where stops and pivots mean
    something. Blacks, shadows, highlights and whites run on OKLab lightness,
    which is where "shadows" means what a user points at.
    """
    exposure = float(p.get("exposure", 0.0))
    contrast = float(p.get("contrast", 0.0))
    out = rgb

    if exposure != 0.0 or contrast != 0.0:
        lin = colour.srgb_to_linear(out)
        if exposure != 0.0:
            lin = lin * (2.0 ** exposure)
        if contrast != 0.0:
            # Pivot on 0.18 middle grey, not 0.5: a 0.5 pivot drags midtone
            # skin darker as contrast goes up.
            lin = (lin.clamp(min=1e-6) / 0.18).pow(1.0 + contrast) * 0.18
        out = colour.linear_to_srgb(lin)

    amounts = (
        float(p.get("blacks", 0.0)),
        float(p.get("shadows", 0.0)),
        float(p.get("highlights", 0.0)),
        float(p.get("whites", 0.0)),
    )
    if any(a != 0.0 for a in amounts):
        lab = colour.srgb_to_oklab(out)
        l = lab[..., 0]
        delta = torch.zeros_like(l)
        for amount, centre in zip(amounts, _TONE_CENTRES):
            if amount != 0.0:
                delta = delta + amount * 0.25 * _band(l, centre)
        out = colour.oklab_to_srgb(torch.stack((l + delta, lab[..., 1], lab[..., 2]), dim=-1))
    return out


def op_colour(rgb: torch.Tensor, p: dict[str, Any]) -> torch.Tensor:
    """Warmth, tint, vibrance and saturation, all in OKLab.

    ``vibrance`` scales chroma more where there is little of it, so it lifts a
    muted sky without turning an already-saturated red into a flat blob.
    ``saturation`` scales everything equally.
    """
    warmth = float(p.get("warmth", 0.0))
    tint = float(p.get("tint", 0.0))
    vibrance = float(p.get("vibrance", 0.0))
    saturation = float(p.get("saturation", 1.0))
    if warmth == 0.0 and tint == 0.0 and vibrance == 0.0 and saturation == 1.0:
        return rgb

    lab = colour.srgb_to_oklab(rgb)
    l, a, b = lab[..., 0], lab[..., 1], lab[..., 2]

    if warmth != 0.0:
        # Along the blue-yellow axis, scaled by lightness so deep shadows do not
        # pick up a flat colour cast.
        b = b + warmth * 0.1 * l
    if tint != 0.0:
        # Green-magenta axis.
        a = a + tint * 0.1 * l

    if vibrance != 0.0 or saturation != 1.0:
        c = torch.sqrt(a * a + b * b)
        scale = torch.full_like(c, saturation)
        if vibrance != 0.0:
            # 0.25 is roughly where sRGB chroma saturates, so this reaches ~0
            # boost for colours already at the edge of the gamut.
            headroom = (1.0 - (c / 0.25).clamp(0.0, 1.0))
            scale = scale * (1.0 + vibrance * headroom)
        # Guard on chroma so a pure neutral (a == b == 0) is untouched rather
        # than multiplied by a scale it has no direction to apply.
        live = torch.where(c > 1e-9, scale, torch.ones_like(scale))
        a = a * live
        b = b * live

    return colour.oklab_to_srgb(torch.stack((l, a, b), dim=-1))


def _hue_distance(h: torch.Tensor, centre: float) -> torch.Tensor:
    """Signed angular distance, wrapped to [-pi, pi].

    Wrapping matters: red sits at +29 degrees and magenta at -32, so a naive
    subtraction makes them almost 360 degrees apart and the red band would stop
    affecting reds that happen to lean pink.
    """
    d = h - centre
    return d - 2.0 * math.pi * torch.round(d / (2.0 * math.pi))


def op_hsl(rgb: torch.Tensor, p: dict[str, Any]) -> torch.Tensor:
    """Eight-band hue / saturation / lightness mixer.

    Bands overlap smoothly and are weighted by chroma, so the mixer does not
    tug at near-neutral pixels whose hue is numerically defined but visually
    meaningless — the classic cause of blotchy skies.
    """
    bands = p.get("bands") or {}
    active = {k: v for k, v in bands.items() if v and any(float(x) != 0.0 for x in (v.get("hue", 0), v.get("sat", 0), v.get("lum", 0)))}
    if not active:
        return rgb

    lab = colour.srgb_to_oklab(rgb)
    l, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    c = torch.sqrt(a * a + b * b)
    h = torch.atan2(b, a)

    # Half the gap between adjacent band centres, so neighbours cross at ~0.5.
    half = math.pi / len(HSL_BANDS)
    chroma_gate = (c / 0.04).clamp(0.0, 1.0)

    d_hue = torch.zeros_like(h)
    sat_scale = torch.ones_like(c)
    d_lum = torch.zeros_like(l)

    for name, centre in HSL_BANDS:
        band = active.get(name)
        if band is None:
            continue
        dist = _hue_distance(h, centre).abs()
        w = (1.0 - _smoothstep(0.0, half * 1.6, dist)) * chroma_gate
        hue_amt = float(band.get("hue", 0.0))
        sat_amt = float(band.get("sat", 0.0))
        lum_amt = float(band.get("lum", 0.0))
        if hue_amt:
            d_hue = d_hue + w * hue_amt * (math.pi / 12.0)  # +-1 is +-15 degrees
        if sat_amt:
            sat_scale = sat_scale * (1.0 + w * sat_amt)
        if lum_amt:
            d_lum = d_lum + w * lum_amt * 0.15

    h2 = h + d_hue
    c2 = (c * sat_scale).clamp(min=0.0)
    return colour.oklab_to_srgb(torch.stack((l + d_lum, c2 * torch.cos(h2), c2 * torch.sin(h2)), dim=-1))


def ramp_from_palette(hexes: list[str]) -> list[tuple[float, list[float]]]:
    """Build gradient-map stops from palette colours, ordered dark to light.

    Sorted by OKLab lightness rather than by the palette's own order, because a
    gradient map is a lightness mapping — feeding it a coverage-sorted palette
    would produce a ramp that jumps around.
    """
    from .colour import hex_to_srgb, srgb_to_oklab

    entries = []
    for hx in hexes:
        rgb = hex_to_srgb(hx)
        lab = srgb_to_oklab(torch.tensor(rgb))
        entries.append((float(lab[0]), list(rgb)))
    entries.sort(key=lambda e: e[0])
    if len(entries) == 1:
        return [(0.0, entries[0][1]), (1.0, entries[0][1])]
    n = len(entries) - 1
    return [(i / n, rgb) for i, (_, rgb) in enumerate(entries)]


def op_gradient_map(rgb: torch.Tensor, p: dict[str, Any]) -> torch.Tensor:
    """Map lightness through a colour ramp, then blend back.

    Driven by OKLab lightness rather than by Rec.709 luma so that the mapping
    follows what the eye reads as light and dark.
    """
    stops = p.get("stops") or []
    amount = float(p.get("amount", 0.0))
    if amount <= 0.0 or len(stops) < 2:
        return rgb

    lab = colour.srgb_to_oklab(rgb)
    l = lab[..., 0].clamp(0.0, 1.0)

    pos = torch.tensor([float(s[0]) for s in stops], dtype=rgb.dtype, device=rgb.device)
    cols = torch.tensor([[float(v) for v in s[1]] for s in stops], dtype=rgb.dtype, device=rgb.device)

    idx = (torch.bucketize(l.contiguous(), pos, right=True) - 1).clamp(0, len(stops) - 2)
    p0, p1 = pos[idx], pos[idx + 1]
    c0, c1 = cols[idx], cols[idx + 1]
    t = ((l - p0) / (p1 - p0).clamp(min=1e-9)).clamp(0.0, 1.0).unsqueeze(-1)
    mapped = torch.lerp(c0, c1, t)

    mode = p.get("blend", "normal")
    if mode == "normal":
        blended = mapped
    elif mode == "soft light":
        d = torch.where(rgb <= 0.25, ((16.0 * rgb - 12.0) * rgb + 4.0) * rgb, rgb.clamp(min=0.0).sqrt())
        blended = torch.where(
            mapped <= 0.5,
            rgb - (1.0 - 2.0 * mapped) * rgb * (1.0 - rgb),
            rgb + (2.0 * mapped - 1.0) * (d - rgb),
        )
    elif mode == "overlay":
        blended = torch.where(rgb <= 0.5, 2.0 * rgb * mapped, 1.0 - 2.0 * (1.0 - rgb) * (1.0 - mapped))
    elif mode == "multiply":
        blended = rgb * mapped
    elif mode == "screen":
        blended = 1.0 - (1.0 - rgb) * (1.0 - mapped)
    elif mode == "colour":
        # Keep the image's lightness, take the ramp's hue and chroma. This is
        # what most people actually want from a gradient map and what makes it
        # a grading tool rather than a poster filter.
        ramp_lab = colour.srgb_to_oklab(mapped)
        blended = colour.oklab_to_srgb(torch.stack((lab[..., 0], ramp_lab[..., 1], ramp_lab[..., 2]), dim=-1))
    else:
        raise ValueError(f"unknown gradient map blend {mode!r}")

    return torch.lerp(rgb, blended, amount)
