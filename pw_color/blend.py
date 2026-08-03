"""Layer compositing for nodes that blend a result over their input.

Separate from the grain blender, which takes a *signed deviation* field and has
to decide each mode's neutral. Here both sides are ordinary images, so the modes
are the textbook ones and there is nothing clever to get wrong — which is
exactly why they belong in one place rather than being retyped per node.
"""

from __future__ import annotations

import torch

from .types import BLEND_MODES

__all__ = ["composite", "BLEND_MODES"]


def composite(base: torch.Tensor, layer: torch.Tensor, mode: str = "normal", opacity: float = 1.0) -> torch.Tensor:
    """Composite ``layer`` over ``base``. Both are sRGB-encoded in ``[0,1]``."""
    if mode == "normal":
        out = layer
    elif mode == "multiply":
        out = base * layer
    elif mode == "screen":
        out = 1.0 - (1.0 - base) * (1.0 - layer)
    elif mode == "overlay":
        out = torch.where(base <= 0.5, 2.0 * base * layer, 1.0 - 2.0 * (1.0 - base) * (1.0 - layer))
    elif mode == "soft light":
        # W3C / Photoshop. The piecewise d() keeps the midtone slope continuous;
        # the naive 2*b*l formula kinks at 0.5.
        d = torch.where(base <= 0.25, ((16.0 * base - 12.0) * base + 4.0) * base, base.clamp(min=0.0).sqrt())
        out = torch.where(
            layer <= 0.5,
            base - (1.0 - 2.0 * layer) * base * (1.0 - base),
            base + (2.0 * layer - 1.0) * (d - base),
        )
    elif mode == "add":
        out = base + layer
    else:
        raise ValueError(f"unknown blend mode {mode!r}, expected one of {BLEND_MODES}")

    if opacity < 1.0:
        out = torch.lerp(base, out, float(opacity))
    return out.clamp(0.0, 1.0)
