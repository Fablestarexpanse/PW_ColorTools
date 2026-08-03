"""Render a PALETTE as an IMAGE.

Separate from `palette.py` because extraction is maths and this is typography,
and because the strip is a genuine output people share — a palette image posted
next to a render is half the reason this node gets used.

Drawn with Pillow rather than by hand in torch, purely because text. Everything
else here is rectangles.
"""

from __future__ import annotations

import torch

from .colour import hex_to_srgb
from .theme import THEME
from .types import Palette

__all__ = ["render_strip"]


def _font(size: int):
    """Pillow's bundled font at a usable size, with a fallback.

    `load_default(size=...)` needs Pillow 10.1+. Older builds get the tiny
    bitmap font, which is ugly but legible — better than refusing to render.
    """
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default()


def render_strip(
    palette: Palette,
    width: int = 1024,
    height: int = 200,
    show_labels: bool = True,
) -> torch.Tensor:
    """Render to a ComfyUI IMAGE ``[1,H,W,3]``.

    Layout per swatch: a colour block, then the hex, then a coverage bar with
    its percentage. The bar is drawn relative to the *largest* swatch rather
    than to 100%, because a five-colour palette where nothing exceeds 30% would
    otherwise render as five near-invisible slivers.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    n = max(1, len(palette.colors))
    img = Image.new("RGB", (width, height), THEME["panel"])
    draw = ImageDraw.Draw(img)

    pad = max(6, width // 100)
    cell_w = (width - pad * (n + 1)) / n
    block_h = int(height * (0.62 if show_labels else 1.0)) - pad
    label_size = max(9, min(18, int(cell_w / 6)))
    font = _font(label_size)

    peak = max((c.coverage for c in palette.colors), default=1.0) or 1.0

    for i, sw in enumerate(palette.colors):
        x0 = pad + i * (cell_w + pad)
        x1 = x0 + cell_w
        r, g, b = hex_to_srgb(sw.hex)
        fill = (int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5))
        draw.rectangle([x0, pad, x1, pad + block_h], fill=fill, outline=THEME["border"], width=1)

        if not show_labels:
            continue

        y = pad + block_h + max(6, pad)
        draw.text((x0, y), sw.hex, fill=THEME["text"], font=font)

        bar_y = y + label_size + 6
        bar_h = max(3, int(label_size * 0.4))
        bar_w = cell_w * 0.62
        draw.rectangle([x0, bar_y, x0 + bar_w, bar_y + bar_h], fill=THEME["well"])
        draw.rectangle(
            [x0, bar_y, x0 + bar_w * (sw.coverage / peak), bar_y + bar_h],
            fill=THEME["accent"],
        )
        draw.text((x0 + bar_w + 6, bar_y - 2), f"{sw.coverage * 100:.0f}%", fill=THEME["textMute"], font=font)

    arr = np.asarray(img, dtype=np.float32) / 255.0
    return torch.from_numpy(arr).unsqueeze(0)
