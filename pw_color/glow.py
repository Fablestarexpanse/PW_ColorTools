"""Glow — a spatial op, and the one exception on PW Look.

Every other PW Look control bakes into a lattice, so the preview is exact.
Glow cannot: it blurs, which reads pixel neighbourhoods. It lives on PW Look
anyway because "glow" is part of how our audience describes a look, and putting
it on a separate node would mean wiring two nodes to express one idea.

The cost is stated rather than hidden: the moment glow is non-zero, the node's
LOOK is no longer LUT-exportable and the UI badges the section `render only`.
PW Optics will grow the full halation treatment later; this is the cheap,
always-useful half.
"""

from __future__ import annotations

import math

import torch

from .colour import luma_bt709, srgb_to_linear, linear_to_srgb

__all__ = ["apply_glow", "gaussian_blur"]


def _kernel(sigma: float, device, dtype) -> torch.Tensor:
    radius = max(1, int(math.ceil(sigma * 3.0)))
    x = torch.arange(-radius, radius + 1, device=device, dtype=dtype)
    k = torch.exp(-(x * x) / (2.0 * sigma * sigma))
    return k / k.sum()


def gaussian_blur(image: torch.Tensor, sigma: float) -> torch.Tensor:
    """Separable Gaussian on ``[B,H,W,C]``, reflect-padded.

    Reflect rather than zero padding: zero padding darkens the frame edge, which
    on a glow pass reads as an unintended vignette.
    """
    if sigma <= 0.05:
        return image
    x = image.permute(0, 3, 1, 2)
    k = _kernel(sigma, x.device, x.dtype)
    r = (k.numel() - 1) // 2
    c = x.shape[1]
    kh = k.view(1, 1, 1, -1).expand(c, 1, 1, -1)
    kv = k.view(1, 1, -1, 1).expand(c, 1, -1, 1)
    x = torch.nn.functional.pad(x, (r, r, 0, 0), mode="reflect")
    x = torch.nn.functional.conv2d(x, kh, groups=c)
    x = torch.nn.functional.pad(x, (0, 0, r, r), mode="reflect")
    x = torch.nn.functional.conv2d(x, kv, groups=c)
    return x.permute(0, 2, 3, 1)


def apply_glow(
    image: torch.Tensor,
    amount: float,
    radius: float = 24.0,
    threshold: float = 0.65,
    warmth: float = 0.35,
) -> torch.Tensor:
    """Bloom the highlights back over the image.

    Done in **linear light**: glow is light spilling across the frame, and
    summing it in the sRGB encoding is what makes cheap bloom look like grey
    fog instead of light. The threshold has a soft knee for the same reason a
    grain falloff does — a hard cutoff makes the glow boundary trace a visible
    contour through smooth gradients.

    ``warmth`` biases the glow toward amber, which is what a real lens does and
    what stops the effect reading as digital haze.
    """
    if amount <= 0.0:
        return image

    rgb = image[..., :3]
    lin = srgb_to_linear(rgb.clamp(0.0, 1.0))
    lum = luma_bt709(lin).unsqueeze(-1)

    # Soft knee over the top of the threshold rather than a hard cut.
    knee = max(1e-4, (1.0 - threshold) * 0.5)
    t = ((lum - threshold) / knee).clamp(0.0, 1.0)
    weight = t * t * (3.0 - 2.0 * t)
    bright = lin * weight

    # Radius is absolute in pixels, matching PW Grain's size contract, so a
    # look keeps matching itself across resolutions.
    blurred = gaussian_blur(bright, max(0.5, float(radius)) / 2.355)

    if warmth != 0.0:
        tintv = torch.tensor(
            [1.0 + 0.35 * warmth, 1.0, 1.0 - 0.45 * warmth],
            dtype=blurred.dtype,
            device=blurred.device,
        )
        blurred = blurred * tintv

    out = linear_to_srgb(lin + blurred * float(amount)).clamp(0.0, 1.0)
    if image.shape[-1] == 4:
        return torch.cat((out, image[..., 3:]), dim=-1)
    return out
