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

import torch

from .bloom import bright_pass_bloom

__all__ = ["apply_glow"]


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
    # Only the tint is specific to glow: warmth biases it toward amber, which
    # is what a real lens does and what stops the effect reading as digital haze.
    tint = (1.0 + 0.35 * warmth, 1.0, 1.0 - 0.45 * warmth) if warmth != 0.0 else None
    return bright_pass_bloom(image, amount, radius, threshold, tint)
