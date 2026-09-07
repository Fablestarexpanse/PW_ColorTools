"""The bright-pass bloom shared by PW Look's glow and PW Optics' halation.

Both are the same operation with a different tint and a different story about
why. Light above a threshold is blurred and added back, in linear light —
summing it in the sRGB encoding is what makes cheap bloom look like grey fog
instead of light — with a soft knee on the threshold so the boundary does not
trace a visible contour through a smooth gradient like a sky.

The two were written out separately, which meant the knee, the linear round
trip and the absolute-pixel radius contract each existed twice. What actually
differs is only the tint, so that is the only thing the callers pass.
"""

from __future__ import annotations

import torch

from .blur import gaussian_blur, sigma_for_size
from .colour import linear_to_srgb, luma_bt709, srgb_to_linear, with_alpha_of

__all__ = ["bright_pass_bloom"]


def bright_pass_bloom(
    image: torch.Tensor,
    amount: float,
    radius: float,
    threshold: float,
    tint: torch.Tensor | tuple[float, float, float] | None = None,
) -> torch.Tensor:
    """Blur what is above ``threshold`` and add it back, tinted.

    ``radius`` is absolute in output pixels, matching the size contract PW Grain
    sets, so a look keeps matching itself across resolutions. It is a *full
    width at half maximum* rather than a true radius — `sigma_for_size`
    divides by 2.355 — which is what makes "a 28px halation" mean the size a
    user reads off the result. The name is the one on the node input and in
    every saved LOOK, so it stays.
    """
    if amount <= 0.0:
        return image

    rgb = image[..., :3]
    lin = srgb_to_linear(rgb.clamp(0.0, 1.0))
    lum = luma_bt709(lin).unsqueeze(-1)

    # Soft knee over the top of the threshold rather than a hard cut.
    knee = max(1e-4, (1.0 - threshold) * 0.5)
    t = ((lum - threshold) / knee).clamp(0.0, 1.0)
    bright = lin * (t * t * (3.0 - 2.0 * t))

    blurred = gaussian_blur(bright, sigma_for_size(max(0.5, float(radius))))
    if tint is not None:
        if not isinstance(tint, torch.Tensor):
            tint = torch.tensor(tint, dtype=blurred.dtype, device=blurred.device)
        blurred = blurred * tint

    out = linear_to_srgb(lin + blurred * float(amount)).clamp(0.0, 1.0)
    return with_alpha_of(out, image)
