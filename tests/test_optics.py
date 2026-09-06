"""Halation, vignette and chromatic aberration.

All three are spatial, so none of them can be baked into a lattice — which
makes "is it exactly a no-op at zero" the property that matters most, since a
node that is never quite neutral cannot be reset.
"""

from __future__ import annotations

import pytest
import torch

from pw_color import optics


def _image(seed: int = 3, h: int = 64, w: int = 96) -> torch.Tensor:
    g = torch.Generator().manual_seed(seed)
    return torch.rand(1, h, w, 3, generator=g)


# -- optics: halation --------------------------------------------------------


def _bright_window(h: int = 96, w: int = 96) -> torch.Tensor:
    img = torch.full((1, h, w, 3), 0.10)
    img[:, 20:40, 20:40] = 1.0
    return img


def test_halation_zero_is_a_no_op():
    img = _bright_window()
    assert torch.equal(optics.apply_halation(img, 0.0), img)


def test_halation_is_red():
    """It is red because the red-sensitive layer is re-exposed from behind.
    A neutral version of this is just bloom, which PW Look already has."""
    img = _bright_window()
    out = optics.apply_halation(img, 0.8, radius=20.0)
    d = (out - img)[0, 30, 45]  # just outside the bright square
    assert float(d[0]) > float(d[1]) > float(d[2]), d.tolist()
    assert float(d[0]) > 0.02


def test_halation_leaves_a_dark_frame_alone():
    dark = torch.full((1, 48, 48, 3), 0.12)
    out = optics.apply_halation(dark, 1.0, threshold=0.70)
    assert float((out - dark).abs().max()) < 1e-3


def test_halation_falls_off_with_distance():
    img = _bright_window()
    out = optics.apply_halation(img, 0.8, radius=20.0)
    near = float((out - img)[0, 30, 45].max())
    far = float((out - img)[0, 30, 90].max())
    assert near > far


def test_halation_stays_in_range():
    out = optics.apply_halation(_bright_window(), 1.0, radius=40.0)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


# -- optics: vignette --------------------------------------------------------


def test_vignette_zero_is_a_no_op():
    img = torch.full((1, 64, 64, 3), 0.6)
    assert torch.equal(optics.apply_vignette(img, 0.0), img)


def test_vignette_darkens_corners_not_centre():
    img = torch.full((1, 64, 64, 3), 0.6)
    out = optics.apply_vignette(img, 0.6)
    assert float(out[0, 32, 32, 0]) == pytest.approx(0.6, abs=0.01)
    assert float(out[0, 0, 0, 0]) < 0.5


def test_negative_vignette_brightens_corners():
    img = torch.full((1, 64, 64, 3), 0.4)
    out = optics.apply_vignette(img, -0.6)
    assert float(out[0, 0, 0, 0]) > 0.45


def test_vignette_is_symmetric():
    img = torch.full((1, 64, 64, 3), 0.6)
    out = optics.apply_vignette(img, 0.5)
    corners = [float(out[0, y, x, 0]) for y in (0, 63) for x in (0, 63)]
    assert max(corners) - min(corners) < 1e-4


def test_vignette_holds_neutrality():
    """Applied as exposure in linear light, not a multiply, so it must not
    introduce a colour cast."""
    img = torch.full((1, 64, 64, 3), 0.6)
    out = optics.apply_vignette(img, 0.7)
    assert float((out[..., 0] - out[..., 2]).abs().max()) < 1e-5


def test_vignette_roundness_changes_the_shape():
    img = torch.full((1, 64, 128, 3), 0.6)
    ellipse = optics.apply_vignette(img, 0.6, roundness=1.0)
    boxy = optics.apply_vignette(img, 0.6, roundness=0.2)
    assert not torch.allclose(ellipse, boxy, atol=1e-3)


# -- optics: chromatic aberration -------------------------------------------


def test_ca_zero_is_a_no_op():
    img = _image()
    assert torch.equal(optics.apply_chromatic_aberration(img, 0.0), img)


def test_ca_separates_channels_at_the_edges_not_the_centre():
    """Real lateral CA grows with distance from the optical axis; a uniform
    shift would put fringing in the middle of the frame, where a lens has none."""
    img = torch.zeros(1, 64, 64, 3)
    img[:, :, 30:34] = 1.0  # a vertical bar through the centre
    out = optics.apply_chromatic_aberration(img, 1.0)
    centre = float((out[0, 32, 30:34, 0] - out[0, 32, 30:34, 2]).abs().max())
    edge = float((out[0, 2, 30:34, 0] - out[0, 2, 30:34, 2]).abs().max())
    assert edge >= centre


def test_ca_preserves_shape_and_range():
    img = _image()
    out = optics.apply_chromatic_aberration(img, 0.5)
    assert out.shape == img.shape
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0


def test_optics_alpha_passes_through():
    img = torch.cat((_bright_window(), torch.rand(1, 96, 96, 1)), dim=-1)
    for fn in (
        lambda x: optics.apply_halation(x, 0.5),
        lambda x: optics.apply_vignette(x, 0.5),
        lambda x: optics.apply_chromatic_aberration(x, 0.5),
    ):
        assert torch.equal(fn(img)[..., 3:], img[..., 3:])


def test_optics_are_deterministic():
    img = _bright_window()
    assert torch.equal(optics.apply_halation(img, 0.4), optics.apply_halation(img, 0.4))


