"""Lens and film artefacts: halation, vignette, chromatic aberration.

All spatial, all render-only, none of them expressible in a lattice. They live
together because they are the same *kind* of thing — what the optics and the
emulsion did to the light before anything graded it.

Halation first and by default: it is the single biggest jump toward a film look.
It is the red-orange bleed you see around a bright window in a film frame,
caused by light passing through the emulsion, reflecting off the backing and
re-exposing the red-sensitive layer from underneath. That is why it is red, why
it only appears around highlights, and why it is soft.
"""

from __future__ import annotations

import torch

from .blur import gaussian_blur, sigma_for_size
from .colour import linear_to_srgb, luma_bt709, srgb_to_linear

__all__ = ["apply_halation", "apply_vignette", "apply_chromatic_aberration", "gaussian_blur"]


def apply_halation(
    image: torch.Tensor,
    amount: float = 0.35,
    radius: float = 28.0,
    threshold: float = 0.70,
    tint: tuple[float, float, float] = (1.0, 0.35, 0.12),
) -> torch.Tensor:
    """Bleed a warm glow out of the highlights.

    Done in **linear light**, because this is light physically spreading through
    an emulsion; summing it in the sRGB encoding gives the grey fog that makes
    cheap bloom look cheap.

    The threshold has a soft knee for the same reason grain has an edge falloff:
    a hard cutoff makes the halation boundary trace a visible contour through
    smooth gradients like a sky.

    The default tint is strongly red because that is what halation *is* — the
    red-sensitive layer being re-exposed from behind. A neutral-tinted version
    of this effect is just bloom, which is what PW Look's glow already does.
    """
    if amount <= 0.0:
        return image

    rgb = image[..., :3]
    lin = srgb_to_linear(rgb.clamp(0.0, 1.0))
    lum = luma_bt709(lin).unsqueeze(-1)

    knee = max(1e-4, (1.0 - threshold) * 0.5)
    t = ((lum - threshold) / knee).clamp(0.0, 1.0)
    bright = lin * (t * t * (3.0 - 2.0 * t))

    # Radius in output pixels, matching the absolute-size contract PW Grain
    # sets, so a look keeps matching itself across resolutions.
    blurred = gaussian_blur(bright, sigma_for_size(max(0.5, float(radius))))
    tintv = torch.tensor(tint, dtype=blurred.dtype, device=blurred.device)

    out = linear_to_srgb(lin + blurred * tintv * float(amount)).clamp(0.0, 1.0)
    if image.shape[-1] == 4:
        return torch.cat((out, image[..., 3:]), dim=-1)
    return out


def apply_vignette(
    image: torch.Tensor,
    amount: float = 0.3,
    midpoint: float = 0.5,
    roundness: float = 1.0,
    feather: float = 0.6,
) -> torch.Tensor:
    """Darken (or brighten) the frame corners.

    Applied in linear light as an exposure change rather than as a multiply in
    the sRGB encoding: a multiply crushes shadow detail and shifts saturation,
    which is why so many vignettes look like a dirty filter rather than like
    falloff.

    ``roundness`` 1.0 is an ellipse fitted to the frame; lower values push it
    toward a rectangle, which suits wide crops where a true ellipse clips the
    short edges too hard.
    """
    if amount == 0.0:
        return image

    rgb = image[..., :3]
    b, h, w = rgb.shape[0], rgb.shape[1], rgb.shape[2]
    dev, dt = rgb.device, rgb.dtype

    ys = torch.linspace(-1.0, 1.0, h, device=dev, dtype=dt).view(h, 1)
    xs = torch.linspace(-1.0, 1.0, w, device=dev, dtype=dt).view(1, w)

    # Aspect-correct so a vignette on a 16:9 frame is not an oval surprise.
    aspect = w / max(1, h)
    if aspect >= 1.0:
        xs = xs * aspect
    else:
        ys = ys / aspect

    p = 2.0 / max(0.05, float(roundness))
    r = (xs.abs().pow(p) + ys.abs().pow(p)).pow(1.0 / p)
    r = r / max(1e-6, r.max().item())

    edge0 = float(midpoint) * (1.0 - float(feather))
    edge1 = float(midpoint) + (1.0 - float(midpoint)) * float(feather) + 1e-6
    t = ((r - edge0) / (edge1 - edge0)).clamp(0.0, 1.0)
    mask = (t * t * (3.0 - 2.0 * t)).unsqueeze(0).unsqueeze(-1).expand(b, h, w, 1)

    lin = srgb_to_linear(rgb.clamp(0.0, 1.0))
    out = linear_to_srgb(lin * (2.0 ** (-float(amount) * 2.0 * mask))).clamp(0.0, 1.0)
    if image.shape[-1] == 4:
        return torch.cat((out, image[..., 3:]), dim=-1)
    return out


def apply_chromatic_aberration(image: torch.Tensor, amount: float = 0.3) -> torch.Tensor:
    """Lateral chromatic aberration: scale red and blue oppositely about the centre.

    Real lateral CA grows with distance from the optical axis, so this is a
    radial *scale* rather than a uniform shift — a constant offset would put
    fringing in the middle of the frame, where a lens has none.

    Uses ``grid_sample`` because it is a resample rather than a colour
    operation, so the parity concerns that rule it out in ``lattice.py`` do not
    apply here: nothing on the browser side reproduces this.
    """
    if amount == 0.0:
        return image

    rgb = image[..., :3]
    b, h, w = rgb.shape[0], rgb.shape[1], rgb.shape[2]
    x = rgb.permute(0, 3, 1, 2)

    ys = torch.linspace(-1.0, 1.0, h, device=rgb.device, dtype=rgb.dtype).view(1, h, 1, 1)
    xs = torch.linspace(-1.0, 1.0, w, device=rgb.device, dtype=rgb.dtype).view(1, 1, w, 1)
    base = torch.cat((xs.expand(1, h, w, 1), ys.expand(1, h, w, 1)), dim=-1)

    # 0.004 keeps the maximum useful setting at a couple of pixels at 1080p,
    # which is where the effect stops reading as a lens and starts reading as a
    # mistake.
    k = float(amount) * 0.004
    out_channels = []
    for i, scale in enumerate((1.0 + k, 1.0, 1.0 - k)):
        grid = (base / scale).expand(b, h, w, 2)
        sampled = torch.nn.functional.grid_sample(
            x[:, i : i + 1], grid, mode="bilinear", padding_mode="border", align_corners=True
        )
        out_channels.append(sampled)
    out = torch.cat(out_channels, dim=1).permute(0, 2, 3, 1).clamp(0.0, 1.0)

    if image.shape[-1] == 4:
        return torch.cat((out, image[..., 3:]), dim=-1)
    return out
