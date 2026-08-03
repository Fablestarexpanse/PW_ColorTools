"""Separable Gaussian blur, shared by every spatial operation in the pack.

There were three copies of this — grain, glow and optics — which is two too
many, and all three carried the same bug: ``reflect`` padding raises if the pad
is larger than the dimension being padded, so any blur whose kernel outgrew the
image crashed rather than blurring. A 200px halation radius on a 128px frame is
an entirely reasonable thing for a user to ask for.

The fix is to clamp the kernel to what the image can support, per axis. Once the
kernel is as wide as the image the result is already indistinguishable from the
image's mean, so nothing is lost visually.
"""

from __future__ import annotations

import math

import torch

__all__ = ["gaussian_blur", "sigma_for_size"]


def sigma_for_size(size: float) -> float:
    """Convert a diameter in pixels to a Gaussian sigma.

    ``size`` is treated as full width at half maximum, which is the definition
    that makes "1.4px grain" and "a 28px halation radius" mean what a user
    expects when they look at the result.
    """
    return max(0.0, float(size)) / 2.355


def _kernel(sigma: float, radius: int, device, dtype) -> torch.Tensor:
    x = torch.arange(-radius, radius + 1, device=device, dtype=dtype)
    k = torch.exp(-(x * x) / (2.0 * sigma * sigma))
    return k / k.sum()


def _blur_axis(x: torch.Tensor, sigma: float, horizontal: bool) -> torch.Tensor:
    """One pass of a separable Gaussian over ``[B,C,H,W]``."""
    dim = x.shape[3] if horizontal else x.shape[2]
    # Reflect padding requires pad < dim. Clamping the kernel rather than the
    # padding keeps the convolution normalised and symmetric.
    radius = min(max(1, int(math.ceil(sigma * 3.0))), max(0, dim - 1))
    if radius < 1:
        return x  # a 1px axis has nothing to blur along

    k = _kernel(sigma, radius, x.device, x.dtype)
    c = x.shape[1]
    if horizontal:
        kk = k.view(1, 1, 1, -1).expand(c, 1, 1, -1)
        x = torch.nn.functional.pad(x, (radius, radius, 0, 0), mode="reflect")
    else:
        kk = k.view(1, 1, -1, 1).expand(c, 1, -1, 1)
        x = torch.nn.functional.pad(x, (0, 0, radius, radius), mode="reflect")
    return torch.nn.functional.conv2d(x, kk, groups=c)


def gaussian_blur(image: torch.Tensor, sigma: float) -> torch.Tensor:
    """Separable Gaussian on a ComfyUI-layout ``[B,H,W,C]`` tensor.

    Reflect-padded rather than zero-padded: zero padding darkens the frame edge,
    which on a glow or halation pass reads as an unintended vignette.
    """
    if sigma <= 0.05:
        return image
    x = image.permute(0, 3, 1, 2).contiguous()
    x = _blur_axis(x, sigma, horizontal=True)
    x = _blur_axis(x, sigma, horizontal=False)
    return x.permute(0, 2, 3, 1)
