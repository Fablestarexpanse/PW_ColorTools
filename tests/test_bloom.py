"""The bright-pass bloom shared by PW Look's glow and PW Optics' halation.

Both effects were written out separately and are now four lines each over this
module, so the properties that used to be tested twice — once per caller — are
tested once here, on the thing that actually does the work.
"""

from __future__ import annotations

import pytest
import torch

from pw_color.bloom import bright_pass_bloom


def _image(h: int = 32, w: int = 48, seed: int = 4) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)


def test_zero_amount_returns_the_input_untouched():
    """The property the reset chip depends on: at zero this node does nothing."""
    img = _image()
    out = bright_pass_bloom(img, 0.0, 12.0, 0.5)
    assert out is img


def test_a_dark_frame_has_nothing_to_bloom():
    """Everything is below the threshold, so there is no light to spread."""
    dark = torch.full((1, 16, 16, 3), 0.1)
    out = bright_pass_bloom(dark, 0.8, 8.0, 0.7)
    assert float((out - dark).abs().max()) < 1e-6


def test_light_spreads_outward_from_a_highlight():
    """A single bright patch on black must brighten its neighbourhood, which is
    the whole point — and must do so less further away."""
    img = torch.zeros(1, 33, 33, 3)
    img[:, 15:18, 15:18, :] = 1.0
    out = bright_pass_bloom(img, 1.0, 12.0, 0.5)

    near = float(out[0, 20, 16].mean())
    far = float(out[0, 30, 16].mean())
    assert near > 1e-4, "no light reached a nearby pixel"
    assert near > far, "the bloom should fall off with distance"


def test_output_stays_in_range_at_a_large_amount():
    img = _image()
    out = bright_pass_bloom(img, 4.0, 20.0, 0.2)
    assert torch.isfinite(out).all()
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_tint_biases_the_added_light_not_the_original():
    """Halation is red because the tint multiplies the *blurred* highlights.
    A tint that reached the base image would recolour the whole frame."""
    img = torch.zeros(1, 33, 33, 3)
    img[:, 15:18, 15:18, :] = 1.0
    red = bright_pass_bloom(img, 1.0, 12.0, 0.5, (1.0, 0.0, 0.0))

    edge = red[0, 25, 16]
    assert float(edge[0]) > float(edge[2]), "a red tint should add red, not blue"
    corner = red[0, 0, 0]
    assert float(corner.max()) < 1e-3, "a corner far from any highlight should stay black"


def test_no_tint_is_the_same_as_a_neutral_one():
    img = _image()
    a = bright_pass_bloom(img, 0.5, 10.0, 0.4, None)
    b = bright_pass_bloom(img, 0.5, 10.0, 0.4, (1.0, 1.0, 1.0))
    assert torch.equal(a, b)


def test_alpha_passes_through_untouched():
    rgb = _image()
    alpha = torch.rand(1, 32, 48, 1, generator=torch.Generator().manual_seed(2))
    out = bright_pass_bloom(torch.cat((rgb, alpha), dim=-1), 0.5, 10.0, 0.4)
    assert out.shape[-1] == 4
    assert torch.equal(out[..., 3:], alpha)


@pytest.mark.parametrize("shape", [(1, 1, 1, 3), (1, 1, 16, 3), (1, 16, 1, 3), (3, 9, 7, 3)])
def test_degenerate_shapes_do_not_crash(shape):
    """A blur radius larger than the image used to raise on reflect padding."""
    img = torch.rand(*shape, generator=torch.Generator().manual_seed(6))
    out = bright_pass_bloom(img, 0.6, 200.0, 0.3)
    assert out.shape == img.shape
    assert torch.isfinite(out).all()


def test_it_does_not_modify_its_input():
    img = _image()
    before = img.clone()
    bright_pass_bloom(img, 0.7, 9.0, 0.4)
    assert torch.equal(img, before)
