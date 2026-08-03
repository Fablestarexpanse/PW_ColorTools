"""Scopes: histogram, waveform and RGB parade.

Rendered server-side into an IMAGE rather than drawn in the browser, for two
reasons: a scope of a downscaled proxy is not a scope of the image (resampling
fills in exactly the gaps that make posterisation or clipping visible), and a
scope you can wire into a Save Image node is a scope you can put in a
comparison sheet.

Everything is drawn straight into a torch tensor. No text, so no font
dependency, and the graticule carries the reading instead.
"""

from __future__ import annotations

import torch

from .colour import luma_bt709, srgb_to_linear
from .theme import CHANNEL, THEME

__all__ = ["SCOPE_MODES", "render_scope"]

SCOPE_MODES = ("histogram", "waveform", "parade", "all")


def _hex(value: str, device, dtype) -> torch.Tensor:
    v = value.lstrip("#")
    return torch.tensor([int(v[i : i + 2], 16) / 255.0 for i in (0, 2, 4)], device=device, dtype=dtype)


def _panel(h: int, w: int, device, dtype) -> torch.Tensor:
    return _hex(THEME["well"], device, dtype).view(1, 1, 3).expand(h, w, 3).clone()


def _graticule(canvas: torch.Tensor, divisions: int = 4) -> None:
    """Draw the reference grid in place.

    Four divisions, not ten: this is for artists reading shape, not engineers
    reading IRE, and a dense grid makes a small scope unreadable.
    """
    h, w, _ = canvas.shape
    grid = _hex(THEME["grid"], canvas.device, canvas.dtype)
    for i in range(1, divisions):
        y = int(h * i / divisions)
        x = int(w * i / divisions)
        canvas[y : y + 1, :, :] = grid
        canvas[:, x : x + 1, :] = grid


def _histogram(image: torch.Tensor, h: int, w: int, bins: int = 256) -> torch.Tensor:
    """Overlaid per-channel histogram.

    Plotted on a mild power scale: a linear histogram of a normal photograph is
    one spike and a flat line, while a log one makes three stray pixels look
    like a tonal region.
    """
    dev, dt = image.device, image.dtype
    canvas = _panel(h, w, dev, dt)
    _graticule(canvas)

    px = image[0, ..., :3].reshape(-1, 3).clamp(0, 1)
    xs = torch.linspace(0, 1, w, device=dev, dtype=dt)
    src = torch.linspace(0, 1, bins, device=dev, dtype=dt)

    for i, key in enumerate(("r", "g", "b")):
        idx = (px[:, i] * (bins - 1)).round().to(torch.int64)
        counts = torch.bincount(idx, minlength=bins).to(dt)
        peak = counts.max().clamp(min=1.0)
        norm = (counts / peak).pow(0.4)
        # Resample the bins across the canvas width so the scope is not tied to
        # a 256px output.
        col = torch.searchsorted(src.contiguous(), xs.contiguous()).clamp(0, bins - 1)
        heights = (norm[col] * (h - 2)).round().to(torch.int64)
        rows = torch.arange(h, device=dev).view(h, 1)
        mask = rows >= (h - heights).view(1, w)
        colour = _hex(CHANNEL[key], dev, dt).view(1, 1, 3)
        # Additive so overlaps read as the mixed colour, which is the whole
        # point of an overlaid histogram.
        canvas = torch.where(mask.unsqueeze(-1), (canvas + colour * 0.75).clamp(max=1.0), canvas)
    return canvas


def _waveform_channel(values: torch.Tensor, h: int, w: int, bins: int) -> torch.Tensor:
    """Column-wise intensity distribution, as a ``[h, w]`` density map.

    ``values`` is ``[H, W]`` in ``[0,1]``. Each output column is a histogram of
    that image column, which is what makes a waveform show *where* in the frame
    the tones are rather than merely how many there are.
    """
    ih, iw = values.shape
    dev = values.device

    if iw < w:
        # Scope wider than the image: gather the nearest source column for each
        # output column. Mapping source to destination instead would light only
        # every nth column and leave the trace combed with vertical gaps.
        src = (torch.arange(w, device=dev) * iw // w).clamp(0, iw - 1)
        values = values[:, src]
        iw = w

    x = (torch.arange(iw, device=dev).view(1, iw).expand(ih, iw).reshape(-1) * (w / iw)).to(torch.int64).clamp(0, w - 1)
    y = ((1.0 - values.reshape(-1).clamp(0, 1)) * (h - 1)).round().to(torch.int64).clamp(0, h - 1)
    flat = torch.zeros(h * w, device=dev, dtype=torch.float32)
    flat.index_add_(0, y * w + x, torch.ones_like(y, dtype=torch.float32))
    dens = flat.view(h, w)
    # Normalise per column: a bright sky would otherwise swamp every other
    # column and the trace would vanish.
    peak = dens.max(dim=0, keepdim=True).values.clamp(min=1.0)
    return (dens / peak).pow(0.45).clamp(0, 1)


def _waveform(image: torch.Tensor, h: int, w: int) -> torch.Tensor:
    dev, dt = image.device, image.dtype
    canvas = _panel(h, w, dev, dt)
    _graticule(canvas)
    lum = luma_bt709(srgb_to_linear(image[0, ..., :3].clamp(0, 1))).clamp(0, 1).pow(1 / 2.2)
    dens = _waveform_channel(lum, h, w, 256).to(dt)
    trace = _hex(CHANNEL["luma"], dev, dt).view(1, 1, 3)
    return (canvas + trace * dens.unsqueeze(-1)).clamp(max=1.0)


def _parade(image: torch.Tensor, h: int, w: int) -> torch.Tensor:
    """Three waveforms side by side. The fastest way to see a colour cast."""
    dev, dt = image.device, image.dtype
    canvas = _panel(h, w, dev, dt)
    gap = 4
    cw = (w - gap * 2) // 3
    for i, key in enumerate(("r", "g", "b")):
        x0 = i * (cw + gap)
        sub = _panel(h, cw, dev, dt)
        _graticule(sub)
        dens = _waveform_channel(image[0, ..., i].clamp(0, 1), h, cw, 256).to(dt)
        trace = _hex(CHANNEL[key], dev, dt).view(1, 1, 3)
        canvas[:, x0 : x0 + cw, :] = (sub + trace * dens.unsqueeze(-1)).clamp(max=1.0)
    return canvas


def render_scope(image: torch.Tensor, mode: str = "all", width: int = 512, height: int = 256) -> torch.Tensor:
    """Render a scope for the first frame of ``[B,H,W,C]`` as ``[1,H,W,3]``."""
    if mode not in SCOPE_MODES:
        raise ValueError(f"unknown scope mode {mode!r}, expected one of {SCOPE_MODES}")

    img = image[:1, ..., :3].detach().to(torch.float32)
    w = max(64, int(width))
    h = max(48, int(height))

    if mode == "histogram":
        out = _histogram(img, h, w)
    elif mode == "waveform":
        out = _waveform(img, h, w)
    elif mode == "parade":
        out = _parade(img, h, w)
    else:
        # Stacked, each getting a third of the height, so one node shows the
        # three readings people actually cross-check against each other.
        gap = 4
        each = (h - gap * 2) // 3
        panels = [_histogram(img, each, w), _waveform(img, each, w), _parade(img, each, w)]
        out = _panel(h, w, img.device, img.dtype)
        for i, p in enumerate(panels):
            y0 = i * (each + gap)
            out[y0 : y0 + each, :, :] = p

    return out.unsqueeze(0).clamp(0.0, 1.0)
