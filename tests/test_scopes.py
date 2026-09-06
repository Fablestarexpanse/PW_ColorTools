"""Histogram, waveform and parade.

Rendered from the full-resolution image rather than from a proxy: a histogram
of a downscale is not the histogram of the image, because resampling fills in
exactly the gaps that make a posterised source obvious.
"""

from __future__ import annotations

import pytest
import torch

from pw_color.scopes import SCOPE_MODES, render_scope


def _image(seed: int = 3, h: int = 64, w: int = 96) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)


# -- scopes ------------------------------------------------------------------


@pytest.mark.parametrize("mode", SCOPE_MODES)
def test_scope_renders_at_the_requested_size(mode: str):
    out = render_scope(_image(), mode, width=320, height=200)
    assert out.shape == (1, 200, 320, 3)
    assert out.dtype == torch.float32
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_scope_rejects_unknown_mode():
    with pytest.raises(ValueError, match="scope mode"):
        render_scope(_image(), "vectorscope")


def test_histogram_of_black_sits_at_the_left():
    out = render_scope(torch.zeros(1, 32, 32, 3), "histogram", 256, 128)[0]
    left = float(out[:, :8].mean())
    right = float(out[:, -8:].mean())
    assert left > right * 1.5


def test_histogram_of_white_sits_at_the_right():
    out = render_scope(torch.ones(1, 32, 32, 3), "histogram", 256, 128)[0]
    assert float(out[:, -8:].mean()) > float(out[:, :8].mean()) * 1.5


def test_waveform_puts_a_bright_left_half_on_the_left():
    """A waveform must show *where* in the frame the tones are, which is the
    whole difference between it and a histogram.

    Measured as top-vs-bottom energy *within* each half rather than left-vs-
    right: the panel background and graticule are a non-zero floor across the
    whole scope, so a bare left/right comparison mostly measures the backdrop.
    """
    img = torch.zeros(1, 64, 64, 3)
    img[:, :, :32] = 1.0  # left half white, right half black
    out = render_scope(img, "waveform", 128, 128)[0]

    # Where the brightest row sits in each half is the claim, directly.
    lum = out.mean(dim=-1)
    left_peak = int(lum[:, 8:56].mean(dim=1).argmax())
    right_peak = int(lum[:, 72:120].mean(dim=1).argmax())
    assert left_peak < 16, f"white half traced at row {left_peak}, expected near the top"
    assert right_peak > 112, f"black half traced at row {right_peak}, expected near the bottom"


def test_waveform_has_no_gaps_when_the_scope_is_wider_than_the_image():
    """Mapping source columns onto a wider scope lights only every nth column
    and leaves the trace combed with vertical gaps."""
    img = torch.full((1, 32, 40, 3), 0.5)
    out = render_scope(img, "waveform", 400, 128)[0]
    lum = out.mean(dim=-1)
    # Every output column must carry some trace above the panel background.
    floor = float(lum.min())
    per_column_peak = lum.max(dim=0).values
    assert float(per_column_peak.min()) > floor + 0.05, "trace has empty columns"


def test_parade_shows_a_colour_cast():
    """The fastest way to see a cast: the red trace sits higher than the blue."""
    img = torch.zeros(1, 32, 64, 3)
    img[..., 0] = 0.9
    img[..., 1] = 0.5
    img[..., 2] = 0.1
    out = render_scope(img, "parade", 300, 120)[0]
    third = 300 // 3
    # Red panel's energy is near the top, blue panel's near the bottom.
    red_top = float(out[:40, :third].mean())
    blue_top = float(out[:40, 2 * third :].mean())
    assert red_top > blue_top


def test_scope_is_deterministic():
    img = _image()
    assert torch.equal(render_scope(img, "all"), render_scope(img, "all"))


def test_scope_handles_a_flat_image_without_dividing_by_zero():
    for value in (0.0, 1.0, 0.5):
        out = render_scope(torch.full((1, 16, 16, 3), value), "all")
        assert torch.isfinite(out).all()


def test_scope_ignores_alpha():
    rgb = _image()
    rgba = torch.cat((rgb, torch.rand(1, 64, 96, 1)), dim=-1)
    assert torch.equal(render_scope(rgb, "all"), render_scope(rgba, "all"))
